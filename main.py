import os
import sys


def _init_console():
    """Enable Windows VT/ANSI processing and force UTF-8 output so the TUI
    renders special symbols correctly in CMD, PowerShell, and Windows
    Terminal. No-op on POSIX platforms."""
    if os.name == "nt":
        try:
            import colorama
            colorama.just_fix_windows_console()
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


from ani_cli_arabic.app import main

if __name__ == "__main__":
    _init_console()
    main()
