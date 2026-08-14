"""Runtime Playwright Chromium bootstrap for the packaged executables.

The GUI/CLI releases no longer bundle the Playwright browser binaries (they
pushed builds past 400 MB). They still bundle Playwright's node *driver*, so
the browser can be downloaded into the user's ms-playwright cache on first use
via Playwright's own CLI (``python -m playwright install chromium`` equivalent).

This module is intentionally **stdlib-only** — the scrapers call it lazily and
the GUI build excludes `requests`/`email`/`numpy`/`PIL`, so anything heavier
imported here would break the GUI at runtime.
"""

import os
import sys
from pathlib import Path


def configure_browsers_path() -> None:
    """Point Playwright at the user's browser cache when frozen.

    Playwright's transport layer forces ``PLAYWRIGHT_BROWSERS_PATH=0`` for
    PyInstaller/Nuitka apps (``playwright/_impl/_transport.py``), which puts the
    driver in bundled-browsers mode and looks under ``driver/package/.local-browsers``
    inside the extracted archive. Our releases deliberately do NOT bundle the
    browser binaries, so we must pre-set the env var to the real user cache
    location — Playwright uses ``setdefault``, so our value wins. Idempotent and
    a no-op for non-frozen runs.
    """
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_browser_bases()[0])


def _browser_bases() -> list:
    """Default ms-playwright cache locations per OS (mirrors Playwright's own
    resolution when PLAYWRIGHT_BROWSERS_PATH is unset)."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return [Path(env)]
    if os.name == "nt":
        return [Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Caches" / "ms-playwright"]
    return [Path.home() / ".cache" / "ms-playwright"]


def _chromium_present() -> bool:
    for base in _browser_bases():
        if base.is_dir():
            try:
                if any(p.name.startswith("chromium") for p in base.iterdir() if p.is_dir()):
                    return True
            except OSError:
                pass
    return False


def ensure_playwright_chromium(force: bool = False) -> None:
    """Install the Playwright Chromium browser if it is missing.

    No-op when the browser is already present. In frozen (PyInstaller) apps the
    Playwright driver executables are bundled, so ``playwright.__main__`` can
    still download the browser into the user cache.
    """
    configure_browsers_path()
    if not force and _chromium_present():
        return
    print("[*] Playwright Chromium not found — downloading (one-time).")
    print("[*] This can take a few minutes the first time you stream.")
    old_argv = sys.argv[:]
    try:
        from playwright.__main__ import main as _pw_cli
        sys.argv = [old_argv[0], "install", "chromium"]
        _pw_cli()
        sys.argv = old_argv
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        sys.argv = old_argv
        if code != 0:
            print(f"[!] Playwright browser install failed (exit {code}).")
    except Exception as exc:  # pragma: no cover - environment diagnostic
        sys.argv = old_argv
        print(f"[!] Playwright browser auto-install failed: {exc}")
    else:
        if _chromium_present():
            print("[v] Playwright Chromium ready.")
        else:
            print("[!] Playwright reported success but no chromium was found.")
