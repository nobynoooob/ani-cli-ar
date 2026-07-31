"""Watch Together room sync via Supabase Realtime Broadcast + mpv IPC."""

import asyncio
import json
import os
import platform
import random
import socket
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from .config import SUPABASE_DEFAULT_KEY, SUPABASE_DEFAULT_URL
from .player import PlayerManager

ROOM_CODE_LEN = 6
HEARTBEAT_INTERVAL = 3.0
DRIFT_THRESHOLD = 1.5
SEEK_BACKWARD_TOLERANCE = 2.0
SEEK_FORWARD_TOLERANCE = 8.0
POLL_INTERVAL = 0.5

EV_LOAD = "LOAD_MEDIA"
EV_PLAY = "PLAY"
EV_PAUSE = "PAUSE"
EV_SEEK = "SEEK"
EV_HEARTBEAT = "HEARTBEAT"


def _socket_path(code: str) -> str:
    if platform.system() == "Windows":
        return f"\\\\.\\pipe\\ani-cli-watch-{code}"
    return f"/tmp/ani-cli-watch-{code}.sock"


def _unique_socket_path(code: str) -> str:
    """Local mpv IPC socket path. The room code alone would collide when a
    host and a guest run on the same machine (they both derive the path from
    the shared room code), so append a random suffix. Never transmitted to
    peers - purely local."""
    return _socket_path(f"{code}-{uuid.uuid4().hex[:8]}")


def _supabase_credentials() -> Tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "") or SUPABASE_DEFAULT_URL
    key = os.environ.get("SUPABASE_KEY", "") or SUPABASE_DEFAULT_KEY
    return url, key


class SupabaseRealtime:
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._channels: Dict[str, Any] = {}
        self._ready = threading.Event()
        self._stop_evt: Optional[asyncio.Event] = None

    def connect(self) -> bool:
        if self._loop is not None:
            return True
        try:
            from supabase import create_async_client
        except ImportError:
            return False
        url, key = _supabase_credentials()
        if not url or not key:
            return False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, args=(url, key), daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=15.0):
            self.close()
            return False
        return self._client is not None

    def _run(self, url: str, key: str):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main(url, key))
        finally:
            if self._loop is not None:
                try:
                    self._loop.close()
                except Exception:
                    pass
            self._loop = None

    async def _async_main(self, url: str, key: str):
        try:
            from supabase import create_async_client
            client = await create_async_client(url, key)
        except Exception:
            self._client = None
            self._ready.set()
            return
        self._client = client
        self._stop_evt = asyncio.Event()
        self._ready.set()
        try:
            await self._stop_evt.wait()
        finally:
            self._client = None
            for ch in list(self._channels.values()):
                try:
                    await ch.unsubscribe()
                except Exception:
                    pass
            self._channels.clear()
            try:
                await client.realtime.close()
            except Exception:
                pass

    def _submit(self, coro, timeout: float = 15.0):
        if self._loop is None:
            return None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout)
        except Exception:
            return None

    def channel(self, code: str):
        if self._client is None:
            return None
        return _RealtimeChannel(self, code)

    async def _subscribe(self, code: str, callback: Optional[Callable[[dict], None]]):
        if self._client is None:
            return False
        try:
            channel = self._client.channel(f"room:{code}")
        except Exception:
            return False
        self._channels[code] = channel

        def make_handler():
            def handler(payload: dict):
                try:
                    if callback is not None:
                        threading.Thread(
                            target=callback, args=(payload,), daemon=True
                        ).start()
                except Exception:
                    pass
            return handler

        for evt in (EV_LOAD, EV_PLAY, EV_PAUSE, EV_SEEK, EV_HEARTBEAT):
            try:
                channel.on_broadcast(event=evt, callback=make_handler())
            except Exception:
                pass
        try:
            await channel.subscribe()
        except Exception:
            return False
        return True

    async def _send(self, code: str, event: str, payload: dict):
        ch = self._channels.get(code)
        if ch is None:
            return False
        try:
            await ch.send_broadcast(event, payload)
            return True
        except Exception:
            return False

    async def _unsubscribe(self, code: str):
        ch = self._channels.pop(code, None)
        if ch is None:
            return False
        try:
            await ch.unsubscribe()
            return True
        except Exception:
            return False

    def close(self):
        if self._loop is not None and self._stop_evt is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_evt.set)
            except Exception:
                pass
            if self._thread is not None:
                self._thread.join(timeout=5.0)
        self._client = None


class _RealtimeChannel:
    def __init__(self, rt: SupabaseRealtime, code: str):
        self._rt = rt
        self._code = code
        self._callback: Optional[Callable[[dict], None]] = None

    def on_broadcast(self, event: str, callback: Callable[[dict], None]):
        self._callback = callback
        return self

    def subscribe(self) -> bool:
        return bool(self._rt._submit(self._rt._subscribe(self._code, self._callback)))

    def send_broadcast(self, event: str, payload: dict) -> bool:
        return bool(self._rt._submit(self._rt._send(self._code, event, payload)))

    def unsubscribe(self) -> bool:
        return bool(self._rt._submit(self._rt._unsubscribe(self._code)))


class MpvIpcClient:
    def __init__(self, path: str):
        self.path = path
        self._sock: Optional[socket.socket] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._buf = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, timeout: float = 15.0) -> bool:
        if self.connected:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.path)
                sock.settimeout(2.0)
                self._sock = sock
                self._buf = b""
                return True
            except OSError:
                time.sleep(0.3)
        return False

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = b""

    def _send(self, obj: dict):
        if self._sock is None:
            raise OSError("mpv IPC not connected")
        self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _read_line(self) -> Optional[dict]:
        if self._sock is None:
            return None
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return None
                self._buf += chunk
        except (socket.timeout, OSError):
            return None
        line, self._buf = self._buf.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8").strip())
        except ValueError:
            return None

    def request(self, command: list, timeout: float = 2.0) -> Any:
        with self._lock:
            if self._sock is None:
                return None
            self._req_id += 1
            req_id = self._req_id
            self._send({"command": command, "request_id": req_id})
            deadline = time.time() + timeout
            while time.time() < deadline:
                resp = self._read_line()
                if resp is None:
                    return None
                if resp.get("request_id") == req_id:
                    if resp.get("error") == "success":
                        return resp.get("data")
                    return None
        return None

    def send_command(self, command: list):
        with self._lock:
            if self._sock is None:
                return
            self._send({"command": command})

    def get_time_pos(self) -> Optional[float]:
        try:
            val = self.request(["get_property", "time-pos"], timeout=1.0)
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def get_pause(self) -> Optional[bool]:
        val = self.request(["get_property", "pause"], timeout=1.0)
        return bool(val) if val is not None else None

    def set_pause(self, paused: bool):
        self.send_command(["set_property", "pause", bool(paused)])

    def seek(self, seconds: float):
        self.send_command(["set_property", "time-pos", float(seconds)])


def _loadfile_command(url: str, headers: Optional[Dict[str, str]]) -> list:
    options: Dict[str, list] = {}
    if headers:
        fields = [f"{k}: {v}" for k, v in headers.items()]
        if fields:
            options["http-header-fields"] = fields
    cmd = ["loadfile", url, "replace"]
    if options:
        cmd.append(options)
    return cmd


class WatchHost:
    def __init__(self):
        self.code = "".join(str(random.randint(0, 9)) for _ in range(ROOM_CODE_LEN))
        self.socket_path = _unique_socket_path(self.code)
        self._rt = SupabaseRealtime()
        self._channel = None
        self._stop = threading.Event()
        self._player = PlayerManager()
        self._ipc = MpvIpcClient(self.socket_path)
        self._current = {}
        self._sync_thread: Optional[threading.Thread] = None
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> bool:
        if not self._rt.connect():
            return False
        self._channel = self._rt.channel(self.code)
        if self._channel is None:
            return False
        self._channel.subscribe()
        self._active = True
        return True

    def _broadcast(self, event: str, payload: dict):
        if self._channel is None:
            return
        try:
            self._channel.send_broadcast(event, payload)
        except Exception:
            pass

    def notify_load(self, title: str, episode_num, language: str = "English Sub"):
        self._current = {
            "title": title,
            "episode": str(episode_num),
            "language": language,
        }
        self._broadcast(EV_LOAD, self._current)
        self._stop.clear()
        if self._sync_thread is None or not self._sync_thread.is_alive():
            self._sync_thread = threading.Thread(
                target=self._sync_loop, daemon=True
            )
            self._sync_thread.start()

    def notify_stop(self):
        self._broadcast(EV_PAUSE, {})
        self._stop.set()

    def _sync_loop(self):
        if not self._ipc.connect(timeout=20.0):
            self._stop.set()
            return
        prev_time: Optional[float] = None
        prev_pause: Optional[bool] = None
        last_heartbeat = 0.0
        while not self._stop.is_set():
            time_pos = self._ipc.get_time_pos()
            paused = self._ipc.get_pause()
            now = time.time()
            if paused is not None and paused != prev_pause:
                self._broadcast(EV_PAUSE if paused else EV_PLAY, {})
                prev_pause = paused
            if time_pos is not None and prev_time is not None:
                delta = time_pos - prev_time
                if delta < -SEEK_BACKWARD_TOLERANCE or delta > SEEK_FORWARD_TOLERANCE:
                    self._broadcast(EV_SEEK, {"time": time_pos})
            if time_pos is not None:
                prev_time = time_pos
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                self._broadcast(
                    EV_HEARTBEAT,
                    {
                        "time": time_pos,
                        "playing": bool(paused is False),
                        "title": self._current.get("title", ""),
                        "episode": self._current.get("episode", ""),
                    },
                )
                last_heartbeat = now
            time.sleep(POLL_INTERVAL)
        self._ipc.close()

    def stop(self):
        self.notify_stop()
        self._active = False
        if self._channel is not None:
            try:
                self._channel.unsubscribe()
            except Exception:
                pass
        self._rt.close()


class WatchGuest:
    def __init__(self, code: str):
        self.code = code
        self.socket_path = _unique_socket_path(code)
        self._rt = SupabaseRealtime()
        self._channel = None
        self._stop = threading.Event()
        self._ipc = MpvIpcClient(self.socket_path)
        self._mpv_proc: Optional[subprocess.Popen] = None
        self._state_lock = threading.Lock()
        self._pending = {}
        self._last_host_time: Optional[float] = None
        self._last_host_playing: Optional[bool] = None
        self._player = PlayerManager()
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> bool:
        if not self._rt.connect():
            return False
        self._channel = self._rt.channel(self.code)
        if self._channel is None:
            return False
        self._channel.on_broadcast(event="*", callback=self._on_message)
        self._channel.subscribe()
        self._active = True
        return True

    def _on_message(self, message: dict):
        event = message.get("event")
        payload = message.get("payload") or {}
        if event == EV_LOAD:
            self._handle_load(payload)
        elif event == EV_PLAY:
            self._apply_pause(False)
        elif event == EV_PAUSE:
            self._apply_pause(True)
        elif event == EV_SEEK:
            self._apply_seek(payload.get("time"))
        elif event == EV_HEARTBEAT:
            self._handle_heartbeat(payload)

    def _resolve_stream(self, payload: dict) -> Tuple[str, Dict, str]:
        title = payload.get("title", "")
        episode = payload.get("episode", "1")
        language = payload.get("language", "English Sub")
        mode = "dub" if language == "English Dub" else "sub"

        if language == "Arabic Sub":
            return self._resolve_arabic(title, episode)
        return self._resolve_english(title, episode, mode)

    def _resolve_english(self, title: str, episode, mode: str) -> Tuple[str, Dict, str]:
        from .scrapers import ProviderManager
        from .settings import SettingsManager

        settings = SettingsManager()
        preferred = settings.get("preferred_provider", "") or "auto"
        import asyncio

        pm = ProviderManager(preferred_provider=preferred if preferred else None)
        url, headers, provider = asyncio.run(
            pm.resolve_stream(
                title, episode, mode=mode, language="english", provider=preferred
            )
        )
        return url or "", headers or {}, provider or ""

    def _resolve_arabic(self, title: str, episode) -> Tuple[str, Dict, str]:
        from .api import AnimeAPI
        from .settings import SettingsManager

        api = AnimeAPI()
        results = api.search_anime(title)
        if not results:
            return "", {}, ""
        anime = results[0]
        eps = api.get_episodes(anime.id)
        if not eps:
            return "", {}, ""
        target = str(int(float(episode)))
        selected = None
        for ep in eps:
            if str(ep.display_num) == target or str(ep.number) == target:
                selected = ep
                break
        if selected is None:
            selected = eps[0]
        server_data = api.get_streaming_servers(anime.id, str(selected.number), anime.type)
        if not server_data:
            return "", {}, ""
        quality = SettingsManager().get("default_quality", "1080p")
        server_key = {
            "1080p": "FRFhdQ",
            "720p": "FRLink",
            "480p": "FRLowQ",
        }.get(quality, "FRLink")
        current_ep = server_data.get("CurrentEpisode", {})
        server_id = current_ep.get(server_key) or current_ep.get("FRLink")
        if not server_id:
            return "", {}, ""
        mf_url = api.build_mediafire_url(server_id)
        direct = api.extract_mediafire_direct(mf_url)
        return direct or "", {}, "arabic_api"

    def _handle_load(self, payload: dict):
        with self._state_lock:
            self._pending = dict(payload)
        def worker():
            try:
                url, headers, provider = self._resolve_stream(payload)
            except Exception:
                url, headers, provider = "", {}, ""
            if not url:
                self._pending = {}
                return
            self._launch_mpv(url, headers)
            self._pending = {}

        threading.Thread(target=worker, daemon=True).start()

    def _launch_mpv(self, url: str, headers: Dict[str, str]):
        mpv_path = self._player.get_mpv_path()
        args = self._player.build_mpv_args(
            mpv_path,
            url,
            headers=headers,
            ipc_socket=self.socket_path,
            lock_controls=True,
        )
        if self._mpv_proc is not None:
            try:
                self._mpv_proc.kill()
            except Exception:
                pass
        try:
            self._mpv_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            self._mpv_proc = None
            return
        threading.Thread(target=self._watch_mpv_exit, daemon=True).start()
        threading.Thread(target=self._apply_pending, daemon=True).start()

    def _watch_mpv_exit(self):
        proc = self._mpv_proc
        if proc is None:
            return
        proc.wait()
        if self._mpv_proc is proc:
            self._ipc.close()
            self._mpv_proc = None
        self._player.cleanup_guest_input_conf()

    def _apply_pending(self):
        if not self._ipc.connect(timeout=20.0):
            return
        with self._state_lock:
            pending = dict(self._pending)
        if pending:
            t = pending.get("time")
            if t is not None:
                self._ipc.seek(float(t))
            playing = pending.get("playing")
            if playing is not None:
                self._ipc.set_pause(not playing)

    def _apply_pause(self, paused: bool):
        if not self._ipc.connected:
            return
        threading.Thread(
            target=lambda: self._ipc.set_pause(bool(paused)), daemon=True
        ).start()

    def _apply_seek(self, seconds):
        if seconds is None:
            return
        if not self._ipc.connected:
            return
        threading.Thread(
            target=lambda: self._ipc.seek(float(seconds)), daemon=True
        ).start()

    def _handle_heartbeat(self, payload: dict):
        host_time = payload.get("time")
        playing = payload.get("playing")
        if host_time is None:
            return
        if playing is not None:
            self._last_host_playing = bool(playing)
        with self._state_lock:
            if not self._pending:
                self._pending = {
                    "time": host_time,
                    "playing": bool(playing is not False),
                }
        self._last_host_time = float(host_time)
        if not self._ipc.connected:
            return
        if playing is False:
            self._ipc.set_pause(True)
        guest_time = self._ipc.get_time_pos()
        if guest_time is None:
            return
        if abs(guest_time - float(host_time)) > DRIFT_THRESHOLD:
            self._ipc.seek(float(host_time))

    def stop(self):
        self._stop.set()
        self._active = False
        if self._mpv_proc is not None:
            try:
                self._mpv_proc.kill()
            except Exception:
                pass
        self._ipc.close()
        self._player.cleanup_guest_input_conf()
        if self._channel is not None:
            try:
                self._channel.unsubscribe()
            except Exception:
                pass
        self._rt.close()
