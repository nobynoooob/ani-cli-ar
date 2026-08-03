import os
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
    ) -> list:
        """Build mpv arguments. With lock_controls, all default keybindings are
        disabled so guests cannot pause/seek manually; volume-only keys are bound
        via a generated input.conf."""
        mpv_args = [
            mpv_path,
            '--fullscreen',
            '--keep-open=yes',
            '--cache=yes',
            '--demuxer-max-bytes=150M',
            '--demuxer-max-back-bytes=64M',
            '--demuxer-readahead-secs=20',
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
        if headers:
            ref = headers.get('Referer')
            if ref:
                mpv_args += ['--http-header-fields=Referer: ' + ref]
            ua = headers.get('User-Agent')
            if ua:
                mpv_args += ['--user-agent=' + ua]
        mpv_args.append(url)
        return mpv_args

    def build_vlc_args(
        self,
        vlc_path: str,
        url: str,
        title: str = "",
        headers: Optional[dict] = None,
        rc_port: Optional[int] = None,
        lock_controls: bool = False,
    ) -> list:
        """Build VLC arguments. rc_port enables the rc interface over TCP
        (used for Watch Together sync). With lock_controls, playback hotkeys
        are unbound so guests cannot pause/seek manually."""
        vlc_args = [vlc_path, '--fullscreen', '--no-video-title-show']
        if title:
            vlc_args.append('--meta-title=' + title)
        if rc_port:
            vlc_args += [
                '--extraintf=rc',
                '--rc-host=127.0.0.1:' + str(rc_port),
            ]
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

    def get_mpv_path(self) -> Optional[str]:
        if is_bundled():
            exe_name = 'mpv.exe' if os.name == 'nt' else 'mpv'
            bundled_mpv = os.path.join(sys._MEIPASS, 'mpv', exe_name)
            if os.path.exists(bundled_mpv):
                if not self.temp_mpv_path or not os.path.exists(self.temp_mpv_path):
                    temp_dir = tempfile.mkdtemp(prefix='anime_browser_mpv_')
                    self.temp_mpv_path = os.path.join(temp_dir, exe_name)
                    shutil.copy2(bundled_mpv, self.temp_mpv_path)
                    
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

    def play(self, url: str, title: str, player_type: str = 'ask', headers: Optional[dict] = None, ipc_socket: Optional[str] = None, rc_port: Optional[int] = None):
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
                self._play_vlc(url, title, available_players['VLC'], headers, rc_port=rc_port)
            elif selected_player == 'MPV':
                self._play_mpv(url, title, available_players['MPV'], headers, ipc_socket=ipc_socket)
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

    def _play_vlc(self, url: str, title: str, vlc_path: str = None, headers: dict = None, rc_port: Optional[int] = None):
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

    def _play_mpv(self, url: str, title: str, mpv_path: str = None, headers: dict = None, ipc_socket: Optional[str] = None):
        if not mpv_path:
            mpv_path = self.get_available_players().get('MPV')

        if not mpv_path or (mpv_path != 'mpv' and not os.path.exists(mpv_path)):
            raise FileNotFoundError(f"MPV not found at: {mpv_path}")

        if not url or not url.strip():
            raise ValueError("No playable stream URL found")

        url = url.strip().strip('"').strip("'")

        mpv_args = self.build_mpv_args(
            mpv_path, url, title=title, headers=headers, ipc_socket=ipc_socket
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
        try:
            result = proc.wait()
        finally:
            self._last_proc = None

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