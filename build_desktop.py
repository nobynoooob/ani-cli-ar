#!/usr/bin/env python3
"""Build a standalone, zero-dependency desktop GUI executable for
ani-cli-arabic using PyInstaller.

Produces ``dist/ani-cli-ar-gui`` on Linux/macOS and
``dist/ani-cli-ar-gui.exe`` on Windows.

The output is a windowed (--noconsole) one-file executable that bundles the
entire Python runtime, the ``ani_cli_arabic`` package, its ``ui/`` static
assets, and all third-party libraries (pywebview, playwright, httpx, ...).

External system dependencies are still required at runtime (not bundled by
PyInstaller): mpv for playback, a WebView2 runtime on Windows, and WebKit2GTK
+ GTK3 on Linux.

Usage:
    python build_desktop.py                 # default build
    python build_desktop.py --debug         # keep PyInstaller output visible
    python build_desktop.py --bundle-mpv    # also embed the mpv binary
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = "ani_cli_arabic"
ENTRY_NAME = "ani-cli-ar-gui"


def _err(msg):
    print(f"[!] {msg}")
    sys.exit(1)


def _check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


def _install_pyinstaller():
    print("[*] PyInstaller not found, installing...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"]
    )


def _find_mpv():
    for name in ("mpv.exe" if os.name == "nt" else "mpv",):
        path = shutil.which(name)
        if path:
            return Path(path)
    return None


def _create_entry_script() -> Path:
    """PyInstaller cannot run ``ani_cli_arabic/gui.py`` directly because of its
    relative imports, so we write a tiny absolute-import entry script."""
    entry = ROOT / "build" / "_gui_entry.py"
    entry.parent.mkdir(exist_ok=True)
    entry.write_text(
        "from ani_cli_arabic.gui import main\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    return entry


def _hidden_imports() -> list:
    """Modules imported lazily/dynamically that static analysis misses."""
    return [
        # package modules
        f"{PKG}.api", f"{PKG}.app", f"{PKG}.cli", f"{PKG}.config",
        f"{PKG}.deps", f"{PKG}.discord_rpc", f"{PKG}.favorites",
        f"{PKG}.gui", f"{PKG}.history", f"{PKG}.models",
        f"{PKG}.monitoring", f"{PKG}.player", f"{PKG}.settings",
        f"{PKG}.stats", f"{PKG}.storage", f"{PKG}.ui", f"{PKG}.updater",
        f"{PKG}.utils", f"{PKG}.version", f"{PKG}.watch_together",
        # scrapers
        f"{PKG}.scrapers", f"{PKG}.scrapers.base",
        f"{PKG}.scrapers.miruro", f"{PKG}.scrapers.hianime",
        f"{PKG}.scrapers.allanime", f"{PKG}.scrapers.api_provider",
        f"{PKG}.scrapers.gogoanime", f"{PKG}.scrapers.mkissa",
        f"{PKG}.scrapers.embeds", f"{PKG}.scrapers.provider_manager",
        # pywebview backends (loaded dynamically at runtime)
        "webview", "webview.platforms", "webview.platforms.gtk",
        "webview.platforms.gtk3", "webview.platforms.qt",
        "webview.platforms.edgechromium", "webview.platforms.mshtml",
        "webview.platforms.cef", "webview.platforms.cocoa",
        "webview.platforms.winforms",
        # stream extraction / providers
        "playwright", "playwright.sync_api", "playwright.async_api",
        "httpx", "cryptography", "rich", "rich.console", "rich.panel",
        "rich.text", "rich.prompt", "rich.progress", "rich.table",
        "pypresence", "requests",
        # watch together (supabase realtime)
        "supabase", "realtime", "realtime.async_client", "realtime.async_channel",
        "websockets", "websockets.asyncio.client",
        # optional crypto deps used by allanime
        "Cryptodome", "Cryptodome.Cipher", "Cryptodome.Util",
    ]


def _collect_submodules() -> list:
    """Modules that ship many submodules we want to bundle wholesale."""
    mods = ["webview", "playwright", "rich", "httpx", "websockets"]
    if os.name != "nt":
        mods.append("realtime")
    return mods


def _excludes() -> list:
    return [
        "IPython", "jupyter", "notebook", "matplotlib", "scipy", "pandas",
        "tkinter", "PIL.ImageShow", "PIL.ImageTk", "pytest", "unittest",
    ]


def build():
    parser = argparse.ArgumentParser(description="Build desktop GUI executable")
    parser.add_argument("--debug", action="store_true",
                        help="Show full PyInstaller output")
    parser.add_argument("--bundle-mpv", action="store_true",
                        help="Bundle the mpv binary next to the app")
    parser.add_argument("--skip-install", action="store_true",
                        help="Fail instead of auto-installing PyInstaller")
    args = parser.parse_args()

    print("=" * 60)
    print("  ani-cli-arabic Desktop GUI Builder")
    print("=" * 60)

    if not _check_pyinstaller():
        if args.skip_install:
            _err("PyInstaller is not installed (use --skip-install to require it).")
        _install_pyinstaller()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _err("PyInstaller installation failed.")

    system = platform.system()
    print(f"[*] System: {system}")
    print(f"[*] Python: {sys.version.split()[0]}")
    print(f"[*] PyInstaller: {PyInstaller.__version__}")

    # GUI entry requires pywebview at runtime.
    try:
        import webview  # noqa: F401
    except ImportError:
        _err("pywebview is required. Install with: pip install pywebview")

    entry = _create_entry_script()

    ui_dir = ROOT / PKG / "ui"
    if not (ui_dir / "index.html").exists():
        _err(f"Missing GUI assets in {ui_dir}")

    icon = None
    if os.name == "nt":
        cand = ROOT / "assets" / "icon.ico"
        if cand.exists():
            icon = cand
    else:
        cand = ROOT / "assets" / "icon.png"
        if cand.exists():
            icon = cand

    # PyInstaller --add-data separator differs between OS families.
    sep = ";" if os.name == "nt" else ":"
    add_data = [f"{ui_dir}{sep}{PKG}/ui"]

    mpv_binary = None
    if args.bundle_mpv:
        mpv_binary = _find_mpv()
        if mpv_binary:
            add_data.append(f"{mpv_binary}{sep}mpv")
            print(f"[*] Bundling mpv: {mpv_binary}")
        else:
            print("[!] mpv not found in PATH, skipping bundle")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(entry),
        "--name", ENTRY_NAME,
        "--onefile",
        "--noconsole",
        "--clean",
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
    ]

    for data in add_data:
        cmd += ["--add-data", data]
    for mod in _hidden_imports():
        cmd += ["--hidden-import", mod]
    for mod in _collect_submodules():
        cmd += ["--collect-submodules", mod]
    for mod in _excludes():
        cmd += ["--exclude-module", mod]
    if icon:
        cmd += ["--icon", str(icon)]

    if not args.debug:
        cmd += ["--log-level", "ERROR"]

    print(f"[*] Output: dist/{ENTRY_NAME}{'.exe' if os.name == 'nt' else ''}")
    print("[*] Building...\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        _err("PyInstaller build failed (re-run with --debug for details).")

    exe = ROOT / "dist" / (ENTRY_NAME + (".exe" if os.name == "nt" else ""))
    if not exe.exists():
        _err(f"Build reported success but {exe} was not found.")

    size_mb = exe.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  BUILD SUCCESSFUL")
    print(f"  {exe}")
    print(f"  Size: {size_mb:.1f} MB")
    print("=" * 60)
    print("\nNotes:")
    print("  - External runtime deps NOT bundled: mpv (playback),")
    if os.name == "nt":
        print("    WebView2 runtime (Windows), and the Playwright Chromium browser.")
    else:
        print("    WebKit2GTK/GTK3 (Linux), and the Playwright Chromium browser.")
    if not args.bundle_mpv or not mpv_binary:
        print("    Install mpv separately or rebuild with --bundle-mpv.")
    print("  - Playwright browsers must be installed on the target machine:\n"
          "      playwright install chromium")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        sys.exit(1)
