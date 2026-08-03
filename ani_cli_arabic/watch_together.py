"""Watch Together room sync via Supabase Realtime Broadcast + mpv IPC."""

import asyncio
import atexit
import getpass
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
MAX_MEMBERS = 8
HEARTBEAT_INTERVAL = 3.0
DRIFT_THRESHOLD = 1.5
SYNC_HARD_SEEK_THRESHOLD = 2.0
SEEK_BACKWARD_TOLERANCE = 2.0
SEEK_FORWARD_TOLERANCE = 8.0
POLL_INTERVAL = 0.5
RECONNECT_TIMEOUT = 30.0
SEEK_COOLDOWN = 2.0

SENDER_HOST = "host"
ROLE_HOST = "host"
ROLE_GUEST = "guest"
ROLE_CO_HOST = "co-host"

EV_LOAD = "LOAD_MEDIA"
EV_PLAY = "PLAY"
EV_PAUSE = "PAUSE"
EV_SEEK = "SEEK"
EV_HEARTBEAT = "HEARTBEAT"
EV_JOIN = "JOIN"
EV_LEAVE = "LEAVE"
EV_STATE = "STATE"
EV_MEMBERS = "MEMBERS"
EV_STATUS = "STATUS"
EV_KICK = "KICK"
EV_CONTROL = "CONTROL"
EV_TRANSFER = "TRANSFER_HOST"


def _os_username() -> str:
    """Return the local OS username, sanitized and guaranteed non-empty."""
    raw = ""
    for fn in (lambda: getpass.getuser(), lambda: os.getlogin()):
        try:
            raw = fn()
        except Exception:
            raw = ""
        if raw:
            break
    name = str(raw).strip().strip('"').strip("'")
    return name or "User"


def _subprocess_no_window_flags():
    """Return subprocess creation flags that suppress an extra console window
    when spawning background helper processes on Windows (no-op elsewhere)."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


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


def _mpv_ipc_is_tcp() -> bool:
    """True when mpv IPC must use a local TCP loopback socket instead of a
    Unix socket. Windows Python builds may lack socket.AF_UNIX, so the IPC
    transport falls back to AF_INET on 127.0.0.1 there."""
    if sys.platform == "win32" and not hasattr(socket, "AF_UNIX"):
        return True
    return False


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

        for evt in (EV_LOAD, EV_PLAY, EV_PAUSE, EV_SEEK, EV_HEARTBEAT, EV_JOIN, EV_LEAVE, EV_STATE, EV_MEMBERS, EV_STATUS, EV_KICK, EV_CONTROL, EV_TRANSFER):
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
        self._tcp_port: Optional[int] = _pick_free_port() if _mpv_ipc_is_tcp() else None
        self._sock: Optional[socket.socket] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._buf = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _connect_target(self):
        if hasattr(socket, "AF_UNIX"):
            return self.path
        return ("127.0.0.1", self._tcp_port or 0)

    def connect(self, timeout: float = 15.0) -> bool:
        if self.connected:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if hasattr(socket, "AF_UNIX"):
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                else:
                    # Windows: socket.AF_UNIX is unavailable, so fall back to a
                    # local TCP socket on loopback for the mpv IPC transport.
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(self._connect_target())
                sock.settimeout(2.0)
                self._sock = sock
                self._buf = b""
                return True
            except (AttributeError, OSError, ValueError):
                try:
                    sock.close()
                except OSError:
                    pass
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

    def _read_line(self, timeout: float = 2.0) -> Optional[dict]:
        if self._sock is None:
            return None
        try:
            self._sock.settimeout(timeout)
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

    def ping(self) -> bool:
        """Check that the mpv IPC socket is still alive. On failure, drop the
        socket so callers can reconnect."""
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._send({"command": ["get_property", "time-pos"]})
                self._read_line(timeout=1.0)
                return True
            except (OSError, AttributeError, ValueError):
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buf = b""
            return False

    def show_text(self, msg: str, duration_ms: int = 2000):
        """Display an OSD message in mpv (mpv-only; VLC has no reliable OSD)."""
        self.send_command(["show-text", str(msg), duration_ms])


def _pick_free_port() -> int:
    """Pick an available TCP port for the VLC rc interface."""
    for _ in range(100):
        port = random.randint(42000, 43000)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    return random.randint(42000, 43000)


class VlcIpcClient:
    """Client for the VLC rc interface over TCP.

    VLC is launched with `--extraintf rc --rc-host 127.0.0.1:<port>` and
    controlled by sending plain-text commands terminated by newline. Every
    response ends with the `> ` prompt. `--rc-quiet` is not available on
    VLC 3.x (dropped after 2.x), so the prompt-delimited parsing below is used.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
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
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
                sock.settimeout(2.0)
                self._sock = sock
                self._buf = b""
                self._read_response(timeout=4.0)
                return True
            except OSError:
                time.sleep(0.3)
        return False

    def close(self):
        if self._sock is not None:
            try:
                self._sock.sendall(b"quit\n")
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = b""

    def _send(self, line: str):
        if self._sock is None:
            raise OSError("VLC rc not connected")
        self._sock.sendall((line + "\n").encode("utf-8"))

    def _read_response(self, timeout: float = 2.0) -> str:
        """Read until the VLC `> ` prompt. Returns everything before it."""
        if self._sock is None:
            return ""
        self._sock.settimeout(timeout)
        out = b""
        while True:
            idx = self._buf.find(b"> ")
            if idx != -1:
                out += self._buf[:idx]
                self._buf = self._buf[idx + 2:]
                return out.decode("utf-8", errors="replace")
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            self._buf += chunk
        idx = self._buf.find(b"> ")
        if idx != -1:
            out += self._buf[:idx]
            self._buf = self._buf[idx + 2:]
        else:
            out += self._buf
            self._buf = b""
        return out.decode("utf-8", errors="replace")

    def request(self, command: str, timeout: float = 2.0) -> Optional[str]:
        with self._lock:
            if self._sock is None:
                return None
            try:
                self._send(command)
            except OSError:
                return None
            resp = self._read_response(timeout)
            lines = resp.splitlines()
            if lines and lines[0].strip() == command.strip():
                return "\n".join(lines[1:])
            return resp

    def get_time_pos(self) -> Optional[float]:
        resp = self.request("get_time") or ""
        for line in resp.splitlines():
            line = line.strip()
            if line.isdigit():
                return float(int(line))
        return None

    def get_pause(self) -> Optional[bool]:
        resp = self.request("status") or ""
        for line in resp.splitlines():
            line = line.strip()
            if line.startswith("( state"):
                tokens = line.strip("()").split()
                if len(tokens) >= 2:
                    if tokens[-1] == "paused":
                        return True
                    if tokens[-1] == "playing":
                        return False
                    return None
        return None

    def set_pause(self, paused: bool):
        current = self.get_pause()
        if current is None:
            return
        if current != paused:
            self.request("pause")

    def seek(self, seconds: float):
        self.request(f"seek {int(seconds)}")

    def ping(self) -> bool:
        """Check that the VLC rc socket is still alive. On failure, drop the
        socket so callers can reconnect."""
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._send("status")
                self._read_response(timeout=1.0)
                return True
            except (OSError, AttributeError, ValueError):
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buf = b""
            return False


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
    def __init__(self, player_kind: str = "mpv"):
        self.code = "".join(str(random.randint(0, 9)) for _ in range(ROOM_CODE_LEN))
        self.socket_path = _unique_socket_path(self.code)
        self.player_kind = player_kind
        self.rc_port: Optional[int] = None
        self._rt = SupabaseRealtime()
        self._channel = None
        self._stop = threading.Event()
        self._player = PlayerManager()
        if player_kind == "vlc":
            self.rc_port = _pick_free_port()
            self._ipc = VlcIpcClient("127.0.0.1", self.rc_port)
        else:
            self._ipc = MpvIpcClient(self.socket_path)
        self._current = {}
        self._sync_thread: Optional[threading.Thread] = None
        self._active = False
        self._stopped = False
        self.username = _os_username()
        self.members: Dict[str, str] = {self.username: ROLE_HOST}
        self._member_status: Dict[str, dict] = {}
        self._member_controls: Dict[str, bool] = {}
        atexit.register(self._atexit_cleanup)

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
        self._broadcast_members()
        return True

    def _disambiguate_name(self, name: str) -> str:
        """Append a numeric suffix when a joining member shares a username
        with an existing member (e.g. testing locally in two terminals)."""
        if name not in self.members:
            return name
        n = 2
        while f"{name}_{n}" in self.members:
            n += 1
        return f"{name}_{n}"

    def _member_list(self) -> list:
        return [
            {"name": name, "role": role}
            for name, role in self.members.items()
        ]

    def _broadcast_members(self):
        payload = {
            "members": self._member_list(),
            "host": self.username,
        }
        self._broadcast(EV_MEMBERS, payload)

    def _on_message(self, message: dict):
        """Host is the single authority: it ignores all incoming playback
        control events. Only presence notifications (join/leave) and guest
        status reports are used, for roster + OSD display purposes."""
        event = message.get("event")
        payload = message.get("payload") or {}
        if event == EV_JOIN:
            self._on_guest_joined(payload)
        elif event == EV_LEAVE:
            self._on_guest_left(payload)
        elif event == EV_STATUS:
            self._on_guest_status(payload)

    def _on_guest_status(self, payload: dict):
        """Track per-member sync status reported by guests (drift/buffering)
        so the host can render live sync badges in the member manager."""
        raw = str(payload.get("username") or "").strip()
        if not raw or raw == self.username:
            return
        drift = payload.get("drift")
        playing = payload.get("playing")
        buffering = payload.get("buffering")
        self._member_status[raw] = {
            "drift": float(drift) if drift is not None else 0.0,
            "playing": bool(playing) if playing is not None else None,
            "buffering": bool(buffering) if buffering is not None else False,
            "last_seen": time.time(),
        }

    def _on_guest_joined(self, payload: dict):
        raw = str(payload.get("username") or _os_username()).strip() or "User"
        name = self._disambiguate_name(raw)
        if name not in self.members:
            self.members[name] = ROLE_GUEST
        self._broadcast_members()
        self._send_state()
        self._osd(f"{name} joined")

    def _on_guest_left(self, payload: dict):
        raw = str(payload.get("username") or "").strip()
        self.members.pop(raw, None)
        self._broadcast_members()
        if raw:
            self._osd(f"{raw} left")

    def kick_member(self, name: str) -> bool:
        """Host action: remove a member from the roster and broadcast the
        updated list. Returns True if the member was found and removed."""
        name = str(name or "").strip()
        if not name or name == self.username or name not in self.members:
            return False
        if self.members.pop(name, None) is not None:
            self._member_status.pop(name, None)
            self._member_controls.pop(name, None)
            self._broadcast(EV_KICK, {"name": name})
            self._broadcast_members()
            self._send_state()
            self._osd(f"{name} was kicked by host")
            return True
        return False

    def promote_member(self, name: str) -> bool:
        """Host action: promote a guest to co-host. Returns True on success."""
        name = str(name or "").strip()
        if name not in self.members:
            return False
        self.members[name] = ROLE_CO_HOST
        self._broadcast_members()
        self._send_state()
        self._osd(f"{name} promoted to co-host")
        return True

    def toggle_member_control(self, name: str) -> bool:
        """Host action: toggle whether a member may pause/seek locally.
        Broadcasts the updated permission so the guest can unlock its player."""
        name = str(name or "").strip()
        if not name or name == self.username or name not in self.members:
            return False
        allowed = not bool(self._member_controls.get(name, False))
        self._member_controls[name] = allowed
        self._broadcast(EV_CONTROL, {"name": name, "allowed": allowed})
        self._broadcast_members()
        self._osd(f"{name} can {'now' if allowed else 'no longer'} control playback")
        return True

    def transfer_host(self, name: str) -> bool:
        """Host action: hand the host role to another member. The current
        host demotes itself to co-host and the target becomes host."""
        name = str(name or "").strip()
        if not name or name == self.username or name not in self.members:
            return False
        self.members[name] = ROLE_HOST
        self.members[self.username] = ROLE_CO_HOST
        self._broadcast(EV_TRANSFER, {"old_host": self.username, "new_host": name})
        self._broadcast_members()
        self._send_state()
        self._osd(f"{name} is now the host")
        return True

    def member_sync_label(self, name: str) -> str:
        """Best-effort live sync badge for a member (host view only).

        Returns an emoji-prefixed label: 🟢 Synced, 🟡 Buffering,
        🔴 +X.Xs drift, or "" when there is no fresh status report.
        """
        status = self._member_status.get(name)
        if not status or name not in self.members or name == self.username:
            return ""
        if time.time() - status.get("last_seen", 0) > HEARTBEAT_INTERVAL * 3:
            return ""
        if status.get("buffering"):
            return "🟡 Buffering"
        drift = float(status.get("drift") or 0.0)
        if abs(drift) > DRIFT_THRESHOLD:
            return f"🔴 {'+' if drift > 0 else '-'}{abs(drift):.1f}s"
        return "🟢 Synced"

    def _osd(self, msg: str):
        if self.player_kind == "vlc" or not self._ipc.connected:
            return
        try:
            threading.Thread(
                target=lambda: self._ipc.show_text(str(msg), 2000),
                daemon=True,
            ).start()
        except Exception:
            pass

    def _broadcast(self, event: str, payload: dict):
        if self._channel is None:
            return
        try:
            data = dict(payload)
            data.setdefault("sender", SENDER_HOST)
            self._channel.send_broadcast(event, data)
        except Exception:
            pass

    def _send_state(self):
        payload = dict(self._current)
        payload["sender"] = SENDER_HOST
        payload["host"] = self.username
        payload["members"] = self._member_list()
        payload.setdefault("time", self._ipc.get_time_pos() if self._ipc.connected else None)
        if self._ipc.connected:
            paused = self._ipc.get_pause()
            payload["playing"] = bool(paused is False)
        self._broadcast(EV_STATE, payload)

    def notify_load(self, title: str, episode_num, language: str = "English Sub", url: str = "", headers: Optional[Dict[str, str]] = None):
        self._current = {
            "title": title,
            "episode": str(episode_num),
            "language": language,
            "url": url or "",
            "headers": dict(headers) if headers else {},
        }
        self._broadcast(EV_LOAD, self._current)
        self._osd(f"Now playing: {title} - Ep {episode_num}")
        self._stop.clear()
        if self._sync_thread is None or not self._sync_thread.is_alive():
            self._sync_thread = threading.Thread(
                target=self._sync_loop, daemon=True
            )
            self._sync_thread.start()

    def notify_stop(self):
        self._broadcast(EV_PAUSE, {})
        self._stop.set()

    def _reconnect_ipc(self) -> bool:
        """Reconnect a dropped IPC socket, retrying for up to
        RECONNECT_TIMEOUT seconds."""
        deadline = time.time() + RECONNECT_TIMEOUT
        while time.time() < deadline and not self._stop.is_set():
            if self._ipc.connect(timeout=2.0):
                return True
            time.sleep(0.5)
        return False

    def _sync_loop(self):
        if not self._ipc.connect(timeout=20.0):
            self._stop.set()
            return
        prev_time: Optional[float] = None
        prev_pause: Optional[bool] = None
        last_heartbeat = 0.0
        last_ping = 0.0
        while not self._stop.is_set():
            now = time.time()
            if not self._ipc.connected:
                if not self._reconnect_ipc():
                    break
                continue
            if now - last_ping >= HEARTBEAT_INTERVAL:
                if not self._ipc.ping():
                    self._reconnect_ipc()
                last_ping = now
            time_pos = self._ipc.get_time_pos()
            paused = self._ipc.get_pause()
            now = time.time()
            if paused is not None and paused != prev_pause:
                self._broadcast(EV_PAUSE if paused else EV_PLAY, {})
                self._osd("Paused by host" if paused else "Resumed by host")
                prev_pause = paused
            if time_pos is not None and prev_time is not None:
                delta = time_pos - prev_time
                if delta < -SEEK_BACKWARD_TOLERANCE or delta > SEEK_FORWARD_TOLERANCE:
                    self._broadcast(EV_SEEK, {"time": time_pos})
                    self._osd(f"Seeking to {time_pos:.0f}s")
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
                        "url": self._current.get("url", ""),
                        "language": self._current.get("language", "English Sub"),
                        "headers": self._current.get("headers", {}),
                    },
                )
                last_heartbeat = now
            time.sleep(POLL_INTERVAL)
        self._ipc.close()

    def _atexit_cleanup(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            self.stop()
        except Exception:
            pass

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.notify_stop()
        self._active = False
        if self._channel is not None:
            try:
                self._channel.unsubscribe()
            except Exception:
                pass
        self._rt.close()
        try:
            self._player.kill_active_player()
        except Exception:
            pass
        self._ipc.close()


class WatchGuest:
    def __init__(self, code: str, player_kind: str = "mpv"):
        self.code = code
        self.socket_path = _unique_socket_path(code)
        self.player_kind = player_kind
        self.rc_port: Optional[int] = None
        self._rt = SupabaseRealtime()
        self._channel = None
        self._stop = threading.Event()
        if player_kind == "vlc":
            self.rc_port = _pick_free_port()
            self._ipc = VlcIpcClient("127.0.0.1", self.rc_port)
        else:
            self._ipc = MpvIpcClient(self.socket_path)
        self._player_proc: Optional[subprocess.Popen] = None
        self._state_lock = threading.Lock()
        self._pending = {}
        self._last_host_time: Optional[float] = None
        self._last_host_playing: Optional[bool] = None
        self._player = PlayerManager()
        self._active = False
        self._stopped = False
        self._last_seek_ts = 0.0
        self._monitor_thread: Optional[threading.Thread] = None
        self.username = _os_username()
        self.members: Dict[str, str] = {}
        self._controls_allowed = False
        atexit.register(self._atexit_cleanup)

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
        self._send_join()
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self._monitor_thread.start()
        return True

    def _send_join(self):
        if self._channel is None:
            return
        try:
            self._channel.send_broadcast(
                EV_JOIN, {"sender": "guest", "username": self.username}
            )
        except Exception:
            pass

    def _monitor_loop(self):
        last_ping = 0.0
        last_status = 0.0
        while not self._stop.is_set() and self._active:
            now = time.time()
            if now - last_ping >= HEARTBEAT_INTERVAL:
                if self._player_proc is not None and not self._ipc.connected:
                    self._ensure_ipc()
                elif self._player_proc is not None and not self._ipc.ping():
                    self._ensure_ipc()
                last_ping = now
            if now - last_status >= HEARTBEAT_INTERVAL:
                self._report_status()
                last_status = now
            time.sleep(POLL_INTERVAL)

    def _report_status(self):
        """Periodically report local sync state to the host so it can render
        live status badges (synced/buffering/drift) in the member manager."""
        if self._channel is None:
            return
        drift = 0.0
        playing = None
        buffering = bool(self._player_proc is not None and not self._ipc.connected)
        if self._ipc.connected:
            guest_time = self._ipc.get_time_pos()
            if guest_time is not None and self._last_host_time is not None:
                drift = float(self._last_host_time) - float(guest_time)
            playing = self._ipc.get_pause()
            if playing is not None:
                playing = bool(playing is False)
        try:
            self._channel.send_broadcast(EV_STATUS, {
                "sender": "guest",
                "username": self.username,
                "drift": drift,
                "playing": playing,
                "buffering": buffering,
            })
        except Exception:
            pass

    def _ensure_ipc(self) -> bool:
        """Reconnect a dropped IPC socket, retrying for up to
        RECONNECT_TIMEOUT seconds. On success, re-request a full snapshot
        from the host."""
        deadline = time.time() + RECONNECT_TIMEOUT
        while time.time() < deadline and not self._stop.is_set():
            if self._ipc.connect(timeout=2.0):
                self._resync_from_host()
                return True
            time.sleep(0.5)
        return False

    def _resync_from_host(self):
        """Ask the host for a fresh full state snapshot after a reconnect."""
        self._send_join()

    def _on_message(self, message: dict):
        event = message.get("event")
        payload = message.get("payload") or {}
        if payload.get("sender") != SENDER_HOST and event in (EV_LOAD, EV_PLAY, EV_PAUSE, EV_SEEK, EV_HEARTBEAT, EV_STATE, EV_MEMBERS, EV_KICK, EV_CONTROL, EV_TRANSFER):
            return
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
        elif event == EV_STATE:
            self._handle_state(payload)
        elif event == EV_MEMBERS:
            self._handle_members(payload)
        elif event == EV_KICK:
            self._handle_kick(payload)
        elif event == EV_CONTROL:
            self._handle_control(payload)
        elif event == EV_TRANSFER:
            self._handle_transfer(payload)

    def _handle_kick(self, payload: dict):
        name = str(payload.get("name") or "").strip()
        if name == self.username:
            self._osd("You were kicked by the host")
            threading.Thread(target=self._stop_playback, daemon=True).start()

    def _handle_control(self, payload: dict):
        name = str(payload.get("name") or "").strip()
        if name != self.username:
            return
        self._controls_allowed = bool(payload.get("allowed"))
        self._osd(
            "Host granted you pause/seek control" if self._controls_allowed
            else "Host locked your controls"
        )

    def _handle_transfer(self, payload: dict):
        old_host = str(payload.get("old_host") or "").strip()
        new_host = str(payload.get("new_host") or "").strip()
        if new_host == self.username:
            self._osd("You are now the host")
        elif old_host == self.username:
            self._osd("You transferred the host role")

    def _stop_playback(self):
        try:
            if self._player_proc is not None:
                self._player_proc.kill()
        except Exception:
            pass

    def _handle_members(self, payload: dict):
        prev = dict(self.members)
        members = payload.get("members") or []
        new_members = {}
        host_name = str(payload.get("host") or "").strip()
        for entry in members:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            role = str(entry.get("role") or ROLE_GUEST).strip() or ROLE_GUEST
            new_members[name] = role
        with self._state_lock:
            self.members = new_members
        for name, role in new_members.items():
            if name not in prev:
                label = f"{name} (Host)" if (name == host_name or role == ROLE_HOST) else f"{name} joined"
                self._osd(f"{label}")
                break
        for name in prev:
            if name not in new_members:
                self._osd(f"{name} left")
                break

    def _resolve_stream(self, payload: dict) -> Tuple[str, Dict, str]:
        title = payload.get("title", "")
        episode = payload.get("episode", "1")
        language = payload.get("language", "English Sub")
        mode = "dub" if language == "English Dub" else "sub"

        if language == "Arabic Sub":
            return self._resolve_arabic(title, episode)
        return self._resolve_english(title, episode, mode)

    @staticmethod
    def _has_active_media(payload: dict) -> bool:
        """Return True only when the payload describes a real episode the host
        has started playing. Hosts broadcast room sync (state/members/heartbeat)
        during join and lobby periods with no episode chosen yet; those must NOT
        trigger a player launch or a provider resolution."""
        if not isinstance(payload, dict):
            return False
        if str(payload.get("url") or "").strip():
            return True
        title = str(payload.get("title") or "").strip()
        episode = payload.get("episode")
        return bool(title and episode is not None and str(episode).strip())

    def _resolve_english(self, title: str, episode, mode: str) -> Tuple[str, Dict, str]:
        from .scrapers import ProviderManager
        from .scrapers.provider_manager import normalize_provider
        from .settings import SettingsManager

        settings = SettingsManager()
        preferred = normalize_provider(settings.get("preferred_provider", ""))
        import asyncio

        pm = ProviderManager(preferred_provider=preferred if preferred and preferred != "auto" else None)
        url, headers, provider = asyncio.run(
            pm.resolve_stream(
                title, episode, mode=mode, language="english", provider=preferred,
                quiet=True,
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
        ctx = {
            "anime": title,
            "episode": str(selected.display_num),
            "provider": "arabic_api",
        }
        server_data = api.get_streaming_servers(anime.id, str(selected.number), anime.type, ctx)
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
        direct = api.extract_mediafire_direct(mf_url, ctx)
        return direct or "", {}, "arabic_api"

    def _handle_load(self, payload: dict):
        with self._state_lock:
            self._pending = dict(payload)
        if not self._has_active_media(payload):
            self._pending = {}
            return
        def worker():
            url = str(payload.get("url") or "")
            headers = dict(payload.get("headers") or {})
            if url:
                self._launch_player(url, headers)
                self._pending = {}
                return
            try:
                url, headers, provider = self._resolve_stream(payload)
            except Exception:
                url, headers, provider = "", {}, ""
                exc_type, exc_val, exc_tb = sys.exc_info()
                lang = str(payload.get("language", ""))
                if "dub" in lang.lower():
                    mode = "dub"
                elif "arabic" in lang.lower():
                    mode = "arabic_sub"
                else:
                    mode = "sub"
                try:
                    from .monitoring import monitor
                    monitor.track_error(
                        "Guest stream resolution failed",
                        {
                            "anime": payload.get("title", ""),
                            "episode": payload.get("episode", ""),
                            "language": lang,
                            "provider": "arabic_api" if "arabic" in lang.lower() else "",
                            "translation_mode": mode,
                        },
                        exc_info=(exc_type, exc_val, exc_tb),
                    )
                except Exception:
                    pass
            if not url:
                self._pending = {}
                return
            self._launch_player(url, headers)
            self._pending = {}

        threading.Thread(target=worker, daemon=True).start()

    def _handle_state(self, payload: dict):
        """Apply a full state snapshot from the host (used on join/reconnect).
        Uses the host-provided stream URL if present, otherwise resolves.
        No player is launched from a background state sync when the host has
        not actually selected an episode yet."""
        with self._state_lock:
            self._pending = dict(payload)
        if not self._has_active_media(payload):
            self._pending = {}
            return
        if self._player_proc is not None:
            threading.Thread(target=self._apply_pending, daemon=True).start()
            return
        url = str(payload.get("url") or "")
        headers = dict(payload.get("headers") or {})
        if url:
            self._launch_player(url, headers)
            self._pending = {}
            return
        def worker():
            try:
                resolved_url, resolved_headers, provider = self._resolve_stream(payload)
            except Exception:
                resolved_url, resolved_headers, provider = "", {}, {}
            if not resolved_url:
                self._pending = {}
                return
            self._launch_player(resolved_url, resolved_headers)
            self._pending = {}

        threading.Thread(target=worker, daemon=True).start()

    def _launch_player(self, url: str, headers: Dict[str, str]):
        self._watch_start = time.time()
        with self._state_lock:
            self._watch_meta = dict(self._pending)
        try:
            from .monitoring import monitor
            monitor.set_activity(
                "watching",
                self._watch_meta.get("title", ""),
                self._watch_meta.get("episode"),
            )
        except Exception:
            pass
        if self.player_kind == "vlc":
            vlc_path = self._player.get_available_players().get("VLC") or "vlc"
            args = self._player.build_vlc_args(
                vlc_path,
                url,
                headers=headers,
                rc_port=self.rc_port,
                lock_controls=not self._controls_allowed,
            )
        else:
            mpv_path = self._player.get_mpv_path()
            args = self._player.build_mpv_args(
                mpv_path,
                url,
                headers=headers,
                ipc_socket=self.socket_path,
                lock_controls=not self._controls_allowed,
            )
        if self._player_proc is not None:
            try:
                self._player_proc.kill()
            except Exception:
                pass
        try:
            self._player_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_subprocess_no_window_flags(),
            )
        except Exception:
            exc_type, exc_val, exc_tb = sys.exc_info()
            self._player_proc = None
            try:
                from .monitoring import monitor
                monitor.track_error(
                    "Failed to launch guest player",
                    {"player": self.player_kind, "stream_url": url},
                    exc_info=(exc_type, exc_val, exc_tb),
                )
            except Exception:
                pass
            return
        threading.Thread(target=self._watch_player_exit, daemon=True).start()
        threading.Thread(target=self._apply_pending, daemon=True).start()
        self._osd(f"Now playing: {self._watch_meta.get('title', '')}")

    def _watch_player_exit(self):
        proc = self._player_proc
        if proc is None:
            return
        proc.wait()
        if self._player_proc is proc:
            self._ipc.close()
            self._player_proc = None
        self._player.cleanup_guest_input_conf()
        try:
            from .monitoring import monitor
            monitor.set_activity("idle")
            start = getattr(self, "_watch_start", None)
            if start is not None:
                meta = getattr(self, "_watch_meta", {}) or {}
                lang = str(meta.get("language", ""))
                from .monitoring import monitor
                monitor.track_video_play(
                    meta.get("title", "") or "",
                    meta.get("episode", "") or "",
                    player=self.player_kind,
                    provider="arabic_api" if "arabic" in lang.lower() else "",
                    watch_start=start,
                    watch_end=time.time(),
                )
        except Exception:
            pass

    def _apply_pending(self):
        try:
            if not self._ipc.connect(timeout=20.0):
                return
            with self._state_lock:
                pending = dict(self._pending)
            if pending:
                t = pending.get("time")
                if t is not None:
                    self._last_seek_ts = time.time()
                    self._ipc.seek(float(t))
                playing = pending.get("playing")
                if playing is not None:
                    self._ipc.set_pause(not playing)
        except (AttributeError, OSError, ConnectionError, ValueError):
            pass

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
            return
        guest_time = self._ipc.get_time_pos()
        if guest_time is None:
            return
        now = time.time()
        drift = float(host_time) - guest_time
        if (
            abs(drift) > SYNC_HARD_SEEK_THRESHOLD
            and now - self._last_seek_ts >= SEEK_COOLDOWN
        ):
            self._last_seek_ts = now
            threading.Thread(
                target=lambda: self._ipc.seek(float(host_time)),
                daemon=True,
            ).start()
            self._osd(f"Synced to host at {float(host_time):.0f}s")

    def _osd(self, msg: str):
        if self.player_kind == "vlc" or not self._ipc.connected:
            return
        try:
            threading.Thread(
                target=lambda: self._ipc.show_text(str(msg), 1500),
                daemon=True,
            ).start()
        except Exception:
            pass

    def _atexit_cleanup(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            self.stop()
        except Exception:
            pass

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        self._active = False
        if self._channel is not None:
            try:
                self._channel.send_broadcast(
                    EV_LEAVE, {"sender": "guest", "username": self.username}
                )
            except Exception:
                pass
        if self._player_proc is not None:
            try:
                self._player_proc.kill()
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
