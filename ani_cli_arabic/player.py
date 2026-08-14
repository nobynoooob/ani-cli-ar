import os
import re
import sys
import time
import shutil
import subprocess
import tempfile
from typing import Optional
from .utils import is_bundled


def _no_window_flags():
    """Return subprocess creation flags that suppress an extra console window
    when spawning helper processes on Windows (no-op elsewhere)."""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0

# Custom keyboard hotkeys map for mpv input.conf (used when mpv_custom_keys=True)
_CUSTOM_KEY_BINDINGS = {
    "UP": "seek 60",
    "DOWN": "seek -60",
    "RIGHT": "seek 10",
    "LEFT": "seek -10",
    "PGUP": "seek 600",
    "PGDWN": "seek -600",
    "SPACE": "cycle pause",
    "m": "cycle mute",
    "[": "multiply speed 0.9",
    "]": "multiply speed 1.1",
    "s": "cycle sub-visibility",
    "o": "show-progress",
    "f": "cycle fullscreen",
    "q": "quit",
    "ESC": "stop",
}

_STALL_GRACE = 10.0          # seconds of non-advancing playback before fallback
_PLAYLIST_TIMEOUT = 6.0      # HLS master playlist fetch cap
_IPC_CONNECT_TIMEOUT = 5.0   # mpv IPC socket connect cap before giving up

_GUEST_VOLUME_BINDINGS = (
    "VOLUME_UP add volume 5",
    "VOLUME_DOWN add volume -5",
    "MUTE cycle mute",
    "9 add volume 5",
    "0 add volume -5",
    "MOUSE_BTN_WHEEL_UP add volume 5",
    "MOUSE_BTN_WHEEL_DOWN add volume -5",
)

class PlayerManager:
    _vlc_version_cache: Optional[tuple] = None

    def __init__(self, rpc_manager=None, console=None):
        self.temp_mpv_path = None
        self.rpc_manager = rpc_manager
        self.console = console
        self.guest_input_conf_path = None
        self._last_proc: Optional[subprocess.Popen] = None

    def kill_active_player(self):
        """Terminate the most recently launched player process, if still
        running (used by atexit / Watch Together cleanup)."""
        proc = self._last_proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            pass
        self._last_proc = None

    def build_mpv_args(
        self,
        mpv_path: str,
        url: str,
        title: str = "",
        headers: Optional[dict] = None,
        ipc_socket: Optional[str] = None,
        lock_controls: bool = False,
        subtitles: Optional[list] = None,
        aspect: Optional[str] = None,
        custom_hotkeys: bool = False,
    ) -> list:
        """Build mpv arguments. With lock_controls, all default keybindings are
        disabled so guests cannot pause/seek manually; volume-only keys are bound
        via a generated input.conf. ``subtitles`` are remote track URLs passed
        via ``--sub-file``. ``aspect`` enforces a custom aspect ratio override
        (e.g. "16:9" or "4:3"). With ``custom_hotkeys`` the app's custom
        keyboard map is applied via a generated input.conf."""
        mpv_args = [
            mpv_path,
            '--fullscreen',
            '--keep-open=yes',
            '--cache=yes',
            '--demuxer-max-bytes=150M',
            '--demuxer-max-back-bytes=64M',
            '--demuxer-readahead-secs=30',
            '--hwdec=auto-safe',
            '--sub-auto=fuzzy',
            '--force-window=yes',
        ]
        if title:
            mpv_args.append('--force-media-title=' + title)
        if ipc_socket:
            mpv_args.append('--input-ipc-server=' + ipc_socket)
        if lock_controls:
            mpv_args.append('--no-input-default-bindings')
            conf = self._create_guest_input_conf()
            if conf:
                mpv_args.append('--input-conf=' + conf)
        elif custom_hotkeys:
            conf = self._create_custom_input_conf()
            if conf:
                mpv_args.append('--input-conf=' + conf)
        if aspect and str(aspect).strip().lower() not in ("auto", "", "off"):
            mpv_args.append('--video-aspect-override=' + str(aspect).strip())
        if headers:
            ref = headers.get('Referer')
            if ref:
                mpv_args += ['--http-header-fields=Referer: ' + ref]
            ua = headers.get('User-Agent')
            if ua:
                mpv_args += ['--user-agent=' + ua]
        for sub in (subtitles or []):
            if sub and str(sub).startswith(('http://', 'https://')):
                mpv_args.append('--sub-file=' + str(sub))
        mpv_args.append(url)
        return mpv_args

    @classmethod
    def _vlc_version(cls) -> Optional[tuple]:
        """Return the installed VLC major.minor as a tuple, or None.

        Cached after the first probe. Used to gate options that only exist on
        certain VLC releases (e.g. ``--rc-quiet`` was dropped after VLC 2.x
        and re-added in VLC 4.x)."""
        if cls._vlc_version_cache is not None:
            return cls._vlc_version_cache
        cls._vlc_version_cache = None
        try:
            out = subprocess.run(
                ["vlc", "--version"],
                capture_output=True,
                text=True,
                timeout=8.0,
            ).stdout or ""
            import re as _re
            m = _re.search(r"vlc version (\d+)\.(\d+)", out)
            if m:
                cls._vlc_version_cache = (int(m.group(1)), int(m.group(2)))
        except Exception:
            cls._vlc_version_cache = None
        return cls._vlc_version_cache

    def build_vlc_args(
        self,
        vlc_path: str,
        url: str,
        title: str = "",
        headers: Optional[dict] = None,
        rc_port: Optional[int] = None,
        lock_controls: bool = False,
        subtitles: Optional[list] = None,
    ) -> list:
        """Build VLC arguments. rc_port enables the rc interface over TCP
        (used for Watch Together sync). With lock_controls, playback hotkeys
        are unbound so guests cannot pause/seek manually."""
        vlc_args = [
            vlc_path,
            '--fullscreen',
            '--no-video-title-show',
            '--network-caching=5000',
            '--live-caching=3000',
            '--audio-time-stretch',
        ]
        if title:
            vlc_args.append('--meta-title=' + title)
        if rc_port:
            vlc_args += [
                '--extraintf=rc',
                '--rc-host=127.0.0.1:' + str(rc_port),
            ]
            # --rc-quiet exists only on VLC 4.x+; avoid an "unknown option"
            # warning on earlier releases (dropped after VLC 2.x).
            ver = self._vlc_version()
            if ver and ver[0] >= 4:
                vlc_args.append('--rc-quiet')
        else:
            vlc_args.append('--play-and-exit')
        if lock_controls:
            vlc_args += [
                '--key-play=',
                '--key-jump+short=',
                '--key-jump+medium=',
                '--key-jump+long=',
                '--key-jump+extrashort=',
                '--key-next=',
                '--key-prev=',
                '--key-stop=',
                '--key-quit=',
            ]
        for sub in (subtitles or []):
            if sub and str(sub).startswith(('http://', 'https://')):
                vlc_args.append('--sub-file=' + str(sub))
        if headers:
            ref = headers.get('Referer')
            if ref:
                vlc_args.append('--http-referrer=' + ref)
            ua = headers.get('User-Agent')
            if ua:
                vlc_args.append('--http-user-agent=' + ua)
        vlc_args.append(url)
        return vlc_args

    def _create_guest_input_conf(self) -> Optional[str]:
        try:
            fd, path = tempfile.mkstemp(prefix='ani_cli_guest_input_', suffix='.conf')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("\n".join(_GUEST_VOLUME_BINDINGS) + "\n")
            self.guest_input_conf_path = path
            return path
        except (OSError, IOError):
            return None

    def cleanup_guest_input_conf(self):
        if self.guest_input_conf_path:
            try:
                os.unlink(self.guest_input_conf_path)
            except OSError:
                pass
            self.guest_input_conf_path = None

    def _create_custom_input_conf(self) -> Optional[str]:
        """Write the app's custom keyboard hotkeys to a temp input.conf for
        mpv ``--input-conf``. Returns the file path or None on failure."""
        try:
            lines = []
            for key, cmd in _CUSTOM_KEY_BINDINGS.items():
                lines.append(f"{key} {cmd}")
            fd, path = tempfile.mkstemp(prefix='ani_cli_custom_input_', suffix='.conf')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines) + "\n")
            self.custom_input_conf_path = path
            return path
        except (OSError, IOError):
            return None

    def cleanup_custom_input_conf(self):
        if getattr(self, 'custom_input_conf_path', None):
            try:
                os.unlink(self.custom_input_conf_path)
            except OSError:
                pass
            self.custom_input_conf_path = None

    def get_mpv_path(self) -> Optional[str]:
        if is_bundled():
            exe_name = 'mpv.exe' if os.name == 'nt' else 'mpv'
            bundled_dir = os.path.join(sys._MEIPASS, 'mpv')
            bundled_mpv = os.path.join(bundled_dir, exe_name)
            if os.path.exists(bundled_mpv):
                if not self.temp_mpv_path or not os.path.exists(self.temp_mpv_path):
                    temp_dir = tempfile.mkdtemp(prefix='anime_browser_mpv_')
                    # Copy the whole bundled mpv directory so adjacent DLLs
                    # (Windows winbuilds) travel with the executable.
                    for name in os.listdir(bundled_dir):
                        src = os.path.join(bundled_dir, name)
                        dst = os.path.join(temp_dir, name)
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src, dst)
                    self.temp_mpv_path = os.path.join(temp_dir, exe_name)

                    # Ensure executable permissions on Linux/macOS
                    if os.name != 'nt':
                        st = os.stat(self.temp_mpv_path)
                        os.chmod(self.temp_mpv_path, st.st_mode | 0o111)
                return self.temp_mpv_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            exe_name = 'mpv.exe' if os.name == 'nt' else 'mpv'
            
            dev_mpv = os.path.join(base_dir, 'mpv', exe_name)
            if os.path.exists(dev_mpv):
                return dev_mpv
            
            local_mpv = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mpv', exe_name)
            if os.path.exists(local_mpv):
                return local_mpv

            # Check system PATH
            if os.name == 'nt':
                for name in ('mpv.exe', 'mpv'):
                    found = shutil.which(name)
                    if found:
                        return found
            else:
                found = shutil.which('mpv')
                if found:
                    return found
            
            return 'mpv'
        
        return 'mpv'

    def cleanup_temp_mpv(self):
        if self.temp_mpv_path and os.path.exists(self.temp_mpv_path):
            try:
                temp_dir = os.path.dirname(self.temp_mpv_path)
                shutil.rmtree(temp_dir, ignore_errors=True)
            except (OSError, PermissionError):
                pass

    def get_available_players(self) -> dict:
        players = {}
        
        # Check VLC
        vlc_path = shutil.which('vlc')
        if not vlc_path:
            if os.name == 'nt':
                paths = [
                    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
                ]
                for p in paths:
                    if os.path.exists(p):
                        vlc_path = p
                        break
            elif sys.platform == 'darwin':
                paths = [
                    "/Applications/VLC.app/Contents/MacOS/VLC",
                    os.path.expanduser("~/Applications/VLC.app/Contents/MacOS/VLC")
                ]
                for p in paths:
                    if os.path.exists(p):
                        vlc_path = p
                        break
        if vlc_path:
            players['VLC'] = vlc_path

        # Check MPV
        mpv_path = self.get_mpv_path()
        if mpv_path == 'mpv':
            if os.name == 'nt':
                found = shutil.which('mpv.exe') or shutil.which('mpv')
            else:
                found = shutil.which('mpv')
            if found:
                players['MPV'] = found
        elif os.path.exists(mpv_path):
            players['MPV'] = mpv_path

        # Check MPC-HC
        mpc_path = shutil.which('mpc-hc64') or shutil.which('mpc-hc')
        if not mpc_path and os.name == 'nt':
            paths = [
                r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                r"C:\Program Files\MPC-HC\mpc-hc.exe",
                r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe",
                r"C:\Program Files\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"
            ]
            for p in paths:
                if os.path.exists(p):
                    mpc_path = p
                    break
        if mpc_path:
            players['MPC-HC'] = mpc_path

        return players

    def play(self, url: str, title: str, player_type: str = 'ask', headers: Optional[dict] = None, ipc_socket: Optional[str] = None, rc_port: Optional[int] = None, subtitles: Optional[list] = None):
        if not url:
            msg = "Error: Extracted stream URL is invalid or empty."
            if self.console:
                from rich.text import Text
                self.console.print(Text(msg, style="bold red"))
            else:
                print(msg, file=sys.stderr)
            return

        url = url.strip().strip('"').strip("'")

        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("rtmp://")):
            msg = f"Error: Stream URL does not start with http/https/rtmp: {url[:100]}"
            if self.console:
                from rich.text import Text
                self.console.print(Text(msg, style="bold red"))
            else:
                print(msg, file=sys.stderr)
            return

        available_players = self.get_available_players()
        
        if not available_players:
            msg = "No video players found on your computer. Please download and install VLC Media Player from https://www.videolan.org/vlc/"
            if self.console:
                from rich.text import Text
                self.console.print(Text(msg, style="bold red"))
                input("Press Enter to continue...")
            else:
                print(msg, file=sys.stderr)
                input("Press Enter to continue...")
            return

        player_names = list(available_players.keys())
        preferred = (player_type or 'ask').strip().lower()
        selected_player = None

        if preferred == 'mpv' and 'MPV' in available_players:
            selected_player = 'MPV'
        elif preferred == 'vlc' and 'VLC' in available_players:
            selected_player = 'VLC'

        if selected_player is None:
            if len(player_names) == 1:
                selected_player = player_names[0]
            else:
                if self.console:
                    from rich.prompt import Prompt
                    from rich.panel import Panel
                    from rich.text import Text
                    from rich.align import Align

                    options_text = "\n".join([f"[{i+1}] {name}" for i, name in enumerate(player_names)])
                    panel = Panel(options_text, title=Text("Select Video Player", style="bold cyan"), border_style="cyan", padding=(1, 4))
                    self.console.print()
                    self.console.print(Align.center(panel))

                    choice = Prompt.ask(
                        "Enter the number of the player", 
                        choices=[str(i+1) for i in range(len(player_names))], 
                        default="1", 
                        console=self.console
                    )
                    selected_player = player_names[int(choice)-1]
                else:
                    print("\nAvailable Video Players:")
                    for i, name in enumerate(player_names):
                        print(f"{i+1}. {name}")

                    while True:
                        try:
                            choice = input(f"Choose a video player (1-{len(player_names)}) [1]: ")
                            if not choice.strip():
                                choice = "1"
                            choice_idx = int(choice) - 1
                            if 0 <= choice_idx < len(player_names):
                                selected_player = player_names[choice_idx]
                                break
                            print("Invalid choice.")
                        except ValueError:
                            print("Invalid input.")

        try:
            if selected_player == 'VLC':
                self._play_vlc(url, title, available_players['VLC'], headers, rc_port=rc_port, subtitles=subtitles)
            elif selected_player == 'MPV':
                self._play_mpv(url, title, available_players['MPV'], headers, ipc_socket=ipc_socket, subtitles=subtitles)
            elif selected_player == 'MPC-HC':
                self._play_mpc(url, title, available_players['MPC-HC'], headers)
            return selected_player.lower() if selected_player else None
        except Exception as e:
            if self.console:
                from rich.text import Text
                self.console.print(Text(f"Error launching player: {str(e)}", style="bold red"))
                input("Press Enter to continue...")
            else:
                print(f"Error launching player: {str(e)}", file=sys.stderr)
                input("Press Enter to continue...")
            return None

    def _play_vlc(self, url: str, title: str, vlc_path: str = None, headers: dict = None, rc_port: Optional[int] = None, subtitles: Optional[list] = None):
        if not vlc_path:
            vlc_path = self.get_available_players().get('VLC')

        if not vlc_path:
            raise FileNotFoundError("VLC not found")

        if not url or not url.strip():
            raise ValueError("No playable stream URL found")

        url = url.strip().strip('"').strip("'")

        vlc_args = self.build_vlc_args(
            vlc_path,
            url,
            title=title,
            headers=headers,
            rc_port=rc_port,
            subtitles=subtitles,
        )

        if self.console:
            from rich.text import Text
            self.console.print(Text(f"[DEBUG] Launching VLC with stream URL: {url}", style="dim"))
        else:
            sys.stderr.write(f"[DEBUG] Launching VLC with stream URL: {url}\n")

        proc = subprocess.Popen(
            vlc_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_no_window_flags(),
        )
        self._last_proc = proc
        try:
            result = proc.wait()
        finally:
            self._last_proc = None

        if result != 0:
            err_msg = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            detail = f"VLC exited with error code {result}"
            if err_msg:
                detail += f"\nVLC stderr:\n{err_msg[:2000]}"
            if self.console:
                from rich.text import Text
                self.console.print(Text(detail, style="bold red"))
                input("Press Enter to continue...")
            else:
                print(detail, file=sys.stderr)
                input("Press Enter to continue...")

    def _play_mpv(self, url: str, title: str, mpv_path: str = None, headers: dict = None, ipc_socket: Optional[str] = None, subtitles: Optional[list] = None, aspect: Optional[str] = None, custom_hotkeys: bool = False, progress_cb=None):
        if not mpv_path:
            mpv_path = self.get_available_players().get('MPV')

        if not mpv_path or (mpv_path != 'mpv' and not os.path.exists(mpv_path)):
            raise FileNotFoundError(f"MPV not found at: {mpv_path}")

        if not url or not url.strip():
            raise ValueError("No playable stream URL found")

        url = url.strip().strip('"').strip("'")

        # Set up an IPC socket for progress capture when a callback was given
        # (Continue Watching). Reuse a caller-provided socket (Watch Together)
        # when available, otherwise allocate an ephemeral one.
        progress_client = None
        progress_socket = ipc_socket
        if progress_cb is not None:
            try:
                from .watch_together import MpvIpcClient, _unique_socket_path
                if not progress_socket:
                    progress_client = MpvIpcClient(_unique_socket_path("play"))
                    tcp_port = getattr(progress_client, "_tcp_port", None)
                    progress_socket = f"127.0.0.1:{tcp_port}" if tcp_port else progress_client.path
            except Exception:
                progress_client = None
                progress_socket = ipc_socket

        mpv_args = self.build_mpv_args(
            mpv_path, url, title=title, headers=headers, ipc_socket=progress_socket,
            subtitles=subtitles, aspect=aspect, custom_hotkeys=custom_hotkeys,
        )

        if self.console:
            from rich.text import Text
            self.console.print(Text(f"[DEBUG] Launching MPV with stream URL: {url}", style="dim"))
        else:
            sys.stderr.write(f"[DEBUG] Launching MPV with stream URL: {url}\n")

        proc = subprocess.Popen(
            mpv_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=_no_window_flags(),
        )
        self._last_proc = proc
        poller = None
        if progress_cb is not None:
            poller = self._start_progress_poller(progress_client, progress_socket, proc, progress_cb)
        try:
            result = proc.wait()
        finally:
            self._last_proc = None
        if poller is not None:
            try:
                poller.join(timeout=2.0)
            except Exception:
                pass
        if progress_client is not None:
            try:
                progress_client.close()
            except Exception:
                pass

        if result != 0:
            err_msg = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            detail = f"MPV exited with error code {result}"
            if err_msg:
                detail += f"\nMPV stderr:\n{err_msg[:2000]}"
            if self.console:
                from rich.text import Text
                self.console.print(Text(detail, style="bold red"))
                input("Press Enter to continue...")
            else:
                print(detail, file=sys.stderr)
                input("Press Enter to continue...")

    def _play_mpc(self, url: str, title: str, mpc_path: str = None, headers: dict = None):
        if not mpc_path:
            mpc_path = self.get_available_players().get('MPC-HC')

        if not mpc_path:
            raise FileNotFoundError("MPC-HC not found")

        mpc_args = [
            mpc_path,
            url,
            '/fullscreen',
            '/play',
            '/close'
        ]

        subprocess.run(
            mpc_args,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window_flags(),
        )

    # ------------------------------------------------------------------
    # stream quality fallback (mpv only)
    # ------------------------------------------------------------------
    @staticmethod
    def _hls_variant_list(url: str, headers: Optional[dict] = None) -> list:
        """Return HLS master-playlist renditions sorted best->worst as
        ``[(height, bandwidth, url), ...]``. Returns ``[]`` when the URL is
        not an HLS master playlist (plain mp4, single media playlist, unknown)
        or on any failure."""
        if not url or ".m3u8" not in str(url).split("?")[0].lower():
            return []
        try:
            import httpx
            hdrs = {}
            if headers:
                ref = headers.get("Referer")
                if ref:
                    hdrs["Referer"] = str(ref)
                ua = headers.get("User-Agent")
                if ua:
                    hdrs["User-Agent"] = str(ua)
            r = httpx.get(url, headers=hdrs, timeout=_PLAYLIST_TIMEOUT, follow_redirects=True)
            if r.status_code != 200:
                return []
            variants = []
            pending = None
            for ln in (r.text or "").splitlines():
                ln = ln.strip()
                if ln.startswith("#EXT-X-STREAM-INF"):
                    m_res = re.search(r"RESOLUTION=(\d+)x(\d+)", ln)
                    m_bw = re.search(r"BANDWIDTH=(\d+)", ln)
                    pending = (
                        int(m_bw.group(1)) if m_bw else 0,
                        int(m_res.group(2)) if m_res else 0,
                    )
                elif pending is not None and ln and not ln.startswith("#"):
                    from urllib.parse import urljoin
                    variants.append((pending[1], pending[0], urljoin(url, ln)))
                    pending = None
            # best -> worst by resolution, then bandwidth
            variants.sort(key=lambda v: (v[0], v[1]), reverse=True)
            return variants
        except Exception:
            return []

    @classmethod
    def _hls_variants(cls, url: str, headers: Optional[dict] = None) -> list:
        """Return HLS rendition URLs sorted best->worst for an m3u8 URL.

        When the URL is not an HLS master playlist (plain mp4, single media
        playlist, unknown), a single-element list is returned so the caller
        just plays it. Never raises; every failure degrades to ``[url]``.
        """
        variants = cls._hls_variant_list(url, headers)
        if not variants:
            return [url]
        return [v[2] for v in variants]

    @classmethod
    def _pick_hls_variant(cls, url: str, headers: Optional[dict] = None,
                          resolution: str = "auto") -> str:
        """Select the best HLS rendition at or below ``resolution``
        (``"1080p"``/``"720"``/``"480p"``/``"auto"``). Falls back to the
        highest rendition when nothing matches, and to the original URL when
        the playlist cannot be parsed."""
        resolution = (resolution or "auto").strip().lower()
        if resolution in ("auto", "", "best", "highest"):
            variants = cls._hls_variants(url, headers)
            return variants[0] if variants else url
        m = re.search(r"(\d{3,4})", resolution)
        target = int(m.group(1)) if m else 0
        if target <= 0:
            variants = cls._hls_variants(url, headers)
            return variants[0] if variants else url
        variants = cls._hls_variant_list(url, headers)
        if not variants:
            return url
        for height, _bw, variant_url in variants:  # sorted best->worst
            if height and height <= target:
                return variant_url
        return variants[-1][2] if variants else url

    def _watch_mpv_stall(self, ipc_client, proc) -> bool:
        """Return True when mpv has not started advancing playback within
        ``_STALL_GRACE`` seconds (initial buffering that never resolves).

        Returns False (no fallback, no interruption) whenever no IPC watch is
        possible: the client failed to connect, or the player exited on its own.
        """
        if ipc_client is None:
            return False
        if not ipc_client.connected:
            ipc_client.connect(timeout=_IPC_CONNECT_TIMEOUT)
        if not ipc_client.connected:
            return False
        deadline = time.time() + _STALL_GRACE
        max_pos = 0.0
        while time.time() < deadline:
            if proc.poll() is not None:
                return False  # user closed the player; not a stall
            pos = ipc_client.get_time_pos()
            if pos is not None:
                max_pos = max(max_pos, float(pos))
                if max_pos >= 1.0:
                    return False  # playback is advancing
            time.sleep(0.5)
        return True

    def _start_progress_poller(self, ipc_client, ipc_socket, proc, progress_cb):
        """Start a daemon thread that samples mpv time-pos/duration and calls
        ``progress_cb(pos, dur)`` every ~3s until the player exits. Best-effort;
        never raises. Returns the thread (or None if IPC is unavailable)."""
        try:
            import threading as _threading
            if ipc_client is None:
                from .watch_together import MpvIpcClient
                ipc_client = MpvIpcClient(ipc_socket)
            poller = _threading.Thread(
                target=self._mpv_progress_loop,
                args=(ipc_client, proc, progress_cb),
                daemon=True,
            )
            poller.start()
            return poller
        except Exception:
            return None

    def _mpv_progress_loop(self, ipc_client, proc, progress_cb):
        """Poll mpv position/duration until the process exits."""
        try:
            if not ipc_client.connected:
                ipc_client.connect(timeout=_IPC_CONNECT_TIMEOUT)
            if not ipc_client.connected:
                return
            while proc.poll() is None:
                pos = ipc_client.get_time_pos()
                dur = None
                if pos is not None:
                    try:
                        dur = ipc_client.request(["get_property", "duration"], timeout=1.0)
                    except Exception:
                        dur = None
                    try:
                        progress_cb(float(pos), float(dur) if dur is not None else None)
                    except Exception:
                        pass
                time.sleep(3.0)
        except Exception:
            pass

    def play_with_quality_fallback(
        self,
        url: str,
        title: str = "",
        player_type: str = "mpv",
        headers: Optional[dict] = None,
        subtitles: Optional[list] = None,
        ipc_socket: Optional[str] = None,
        rc_port: Optional[int] = None,
        aspect: Optional[str] = None,
        custom_hotkeys: bool = False,
        progress_cb=None,
        resolution: Optional[str] = None,
    ):
        """Play ``url`` with the chosen player and, for mpv + HLS master
        playlists, auto-downgrade the quality once when the initial stream
        buffers without ever starting (slow/dead CDN).

        Variants are parsed from the master playlist ahead of launch and mpv is
        relaunched at the next-lower rendition if playback does not advance
        within ``_STALL_GRACE`` seconds. When ``resolution`` is set (e.g.
        ``"1080p"``/``"720"``) the playlist is pre-filtered to the best
        rendition at or below that height and played directly. Non-mpv players
        and single-rendition streams delegate to the classic ``play()`` /
        ``_play_mpv()`` launchers.
        Returns the player kind actually used (or None on hard failure)."""
        if not url or not str(url).strip():
            print("Error: Extracted stream URL is invalid or empty.", file=sys.stderr)
            return None
        url = str(url).strip().strip('"').strip("'")
        if not url.startswith(("http://", "https://", "rtmp://")):
            print(
                f"Error: Stream URL does not start with http/https/rtmp: {url[:100]}",
                file=sys.stderr,
            )
            return None

        available = self.get_available_players()
        preferred = (player_type or "mpv").lower()
        if not (preferred == "mpv" and "MPV" in available):
            return self.play(
                url, title, player_type=preferred or "ask",
                headers=headers, ipc_socket=ipc_socket, rc_port=rc_port,
                subtitles=subtitles,
            )

        mpv_path = available["MPV"]
        resolution = (resolution or "auto").strip().lower()
        if resolution not in ("auto", "", "best", "highest"):
            picked = self._pick_hls_variant(url, headers, resolution)
            if picked and picked != url:
                url = picked
        variants = self._hls_variants(url, headers)
        if len(variants) <= 1:
            return self._play_mpv(
                url, title, mpv_path, headers, ipc_socket=ipc_socket,
                subtitles=subtitles, aspect=aspect, custom_hotkeys=custom_hotkeys,
                progress_cb=progress_cb,
            )

        # Build the watchdog IPC client up front so a Windows TCP fallback can
        # allocate its port before mpv is launched with the matching argument.
        ipc_client = None
        ipc_arg = None
        if ipc_socket:
            ipc_arg = ipc_socket
        else:
            try:
                from .watch_together import MpvIpcClient, _unique_socket_path
                ipc_client = MpvIpcClient(_unique_socket_path("play"))
                tcp_port = getattr(ipc_client, "_tcp_port", None)
                ipc_arg = f"127.0.0.1:{tcp_port}" if tcp_port else ipc_client.path
            except Exception:
                ipc_client = None
                ipc_arg = None

        for idx, variant_url in enumerate(variants):
            is_last = idx >= len(variants) - 1
            proc = subprocess.Popen(
                self.build_mpv_args(
                    mpv_path, variant_url, title=title, headers=headers,
                    ipc_socket=ipc_arg, subtitles=subtitles, aspect=aspect,
                    custom_hotkeys=custom_hotkeys,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=_no_window_flags(),
            )
            self._last_proc = proc
            stalled = False
            if not is_last:
                stalled = self._watch_mpv_stall(ipc_client, proc)
                if stalled:
                    sys.stderr.write(
                        f"[!] Stream stalled at quality {idx + 1}/{len(variants)} — "
                        f"trying the next-lower rendition.\n"
                    )
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5.0)
                    except Exception:
                        pass
                    continue
            poller = None
            if progress_cb is not None:
                poller = self._start_progress_poller(ipc_client, ipc_arg, proc, progress_cb)
            try:
                result = proc.wait()
            finally:
                self._last_proc = None
            if poller is not None:
                try:
                    poller.join(timeout=2.0)
                except Exception:
                    pass
            if result != 0:
                err_msg = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                detail = f"MPV exited with error code {result}"
                if err_msg:
                    detail += f"\nMPV stderr:\n{err_msg[:2000]}"
                print(detail, file=sys.stderr)
            return "mpv"
        return "mpv"