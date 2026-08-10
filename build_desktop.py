#!/usr/bin/env python3
"""Build a standalone, zero-dependency desktop GUI executable for
ani-cli-arabic using PyInstaller.

Produces ``dist/ani-cli-ar-gui`` on Linux/macOS and
``dist/ani-cli-ar-gui.exe`` on Windows.

The output is a windowed (--noconsole) one-file executable that bundles the
entire Python runtime, the ``ani_cli_arabic`` package, its ``ui/`` static
assets, and all third-party libraries (pywebview, playwright, httpx, ...).

External system dependencies are still required at runtime (not bundled by
PyInstaller): a WebView2 runtime on Windows and WebKit2GTK + GTK3 on Linux.
mpv for playback and the Playwright Chromium browser CAN be bundled so the
resulting executable is fully portable (double-click to launch).

Usage:
    python build_desktop.py                         # default build
    python build_desktop.py --debug                 # keep PyInstaller output visible
    python build_desktop.py --bundle-mpv            # embed mpv (PATH autodetect or mpv/)
    python build_desktop.py --mpv-dir .cache/mpv    # bundle mpv from an explicit dir
    python build_desktop.py --bundle-browser        # embed Playwright Chromium
    python build_desktop.py --zip                   # also produce a portable .zip
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = "ani_cli_arabic"
ENTRY_NAME = "ani-cli-ar-gui"

# Browser bundle is placed under this name inside the PyInstaller bundle and
# advertised to Playwright via the PLAYWRIGHT_BROWSERS_PATH runtime hook.
BROWSER_DEST = "ms-playwright"
# mpv bundle destination directory inside the PyInstaller bundle. Must match
# the layout expected by player.py's get_mpv_path() (sys._MEIPASS/mpv/...).
MPV_DEST = "mpv"


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


def _find_mpv_dir() -> "Path | None":
    """Return a directory containing the mpv executable, or None."""
    # 1) A local mpv/ bundle checked into the project (CI convenience).
    local = ROOT / "mpv"
    exe_in = lambda d: (d / "mpv.exe") if os.name == "nt" else (d / "mpv")
    if exe_in(local).exists():
        return local
    # 2) Resolve from PATH and return its containing directory (keeps DLLs).
    for name in ("mpv.exe" if os.name == "nt" else "mpv",):
        path = shutil.which(name)
        if path:
            return Path(path).resolve().parent
    return None


def _find_browser_dir() -> "Path | None":
    """Locate the Playwright ms-playwright directory (Chromium installs)."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and Path(env).is_dir():
        candidate = Path(env)
        if _has_chromium(candidate):
            return candidate
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        base = Path.home() / ".cache" / "ms-playwright"
    if base.is_dir() and _has_chromium(base):
        return base
    return None


def _has_chromium(base: Path) -> bool:
    return any(p.name.startswith("chromium") for p in base.iterdir() if p.is_dir())


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


def _create_browser_hook() -> "Path | None":
    """Write a PyInstaller runtime hook that points Playwright at the bundled
    browsers ($MEIPASS/ms-playwright) before any application imports run.
    Returns the hook path or None if browser bundling is not requested."""
    hook = ROOT / "build" / "_browsers_path_hook.py"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):\n"
        "    browsers_dir = os.path.join(sys._MEIPASS, %r)\n"
        "    if os.path.isdir(browsers_dir):\n"
        "        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_dir\n" % BROWSER_DEST,
        encoding="utf-8",
    )
    return hook


def _read_version():
    """Best-effort read of the package version, without importing the package
    (which may trigger heavy imports on an exotic machine)."""
    try:
        from ani_cli_arabic.version import __version__
    except Exception:
        try:
            text = (ROOT / PKG / "version.py").read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return "0.0.0"
        return "0.0.0"
    return __version__


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
                        help="Bundle mpv from PATH or the local mpv/ directory")
    parser.add_argument("--mpv-dir", metavar="DIR",
                        help="Bundle mpv from an explicit directory containing "
                             "mpv.exe (or mpv) plus any adjacent DLLs")
    parser.add_argument("--bundle-browser", action="store_true",
                        help="Bundle the installed Playwright Chromium browser "
                             "(overrides PLAYWRIGHT_BROWSERS_PATH)")
    parser.add_argument("--browser-dir", metavar="DIR",
                        help="Explicit ms-playwright directory to bundle "
                             "(implies --bundle-browser)")
    parser.add_argument("--exe-name", metavar="NAME", default=ENTRY_NAME,
                        help=f"Output executable name (default: {ENTRY_NAME})")
    parser.add_argument("--zip", action="store_true",
                        help="Also produce dist/ani-cli-ar-<version>-<os>-<arch>.zip")
    parser.add_argument("--version", metavar="VER",
                        help="Version label for the zip filename "
                             "(default: read from version.py)")
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

    # PyInstaller --add-data/--add-binary separator differs between OS families.
    sep = ";" if os.name == "nt" else ":"
    add_data = [f"{ui_dir}{sep}{PKG}/ui"]
    add_binaries = []

    # ----- mpv bundling -----------------------------------------------------
    mpv_dir = None
    if args.mpv_dir:
        mpv_dir = Path(args.mpv_dir)
        if not mpv_dir.is_dir():
            _err(f"--mpv-dir is not a directory: {mpv_dir}")
    elif args.bundle_mpv:
        mpv_dir = _find_mpv_dir()
    if mpv_dir:
        add_binaries.append(f"{mpv_dir}{sep}{MPV_DEST}")
        print(f"[*] Bundling mpv from: {mpv_dir}")

    # ----- Playwright Chromium bundling -------------------------------------
    browser_dir = None
    if args.browser_dir:
        browser_dir = Path(args.browser_dir)
        if not browser_dir.is_dir():
            _err(f"--browser-dir is not a directory: {browser_dir}")
    elif args.bundle_browser:
        browser_dir = _find_browser_dir()
    if browser_dir:
        add_data.append(f"{browser_dir}{sep}{BROWSER_DEST}")
        hook = _create_browser_hook()
        print(f"[*] Bundling Playwright browsers from: {browser_dir}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(entry),
        "--name", args.exe_name,
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
    for binary in add_binaries:
        cmd += ["--add-binary", binary]
    if browser_dir:
        cmd += ["--runtime-hook", str(ROOT / "build" / "_browsers_path_hook.py")]
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

    exe_name = args.exe_name + (".exe" if os.name == "nt" else "")
    print(f"[*] Output: dist/{exe_name}")
    print("[*] Building...\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        _err("PyInstaller build failed (re-run with --debug for details).")

    exe = ROOT / "dist" / exe_name
    if not exe.exists():
        _err(f"Build reported success but {exe} was not found.")

    size_mb = exe.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  BUILD SUCCESSFUL")
    print(f"  {exe}")
    print(f"  Size: {size_mb:.1f} MB")

    # ----- portable zip ------------------------------------------------------
    if args.zip:
        version = (args.version or _read_version()).lstrip("v")
        os_short = {"Windows": "windows", "Darwin": "macos",
                    "Linux": "linux"}.get(system, "unknown")
        arch = platform.machine().lower().replace("x86_64", "x86_64").replace("amd64", "x86_64")
        zip_name = f"ani-cli-ar-v{version}-{os_short}-{arch}.zip"
        zip_path = ROOT / "dist" / zip_name
        readme = (
            "ani-cli-arabic - portable build\n"
            "=============================\n\n"
            "Double-click %s to launch the GUI.\n\n"
            "Bundled:\n"
            "  - Python runtime and all application libraries\n"
            "  - %s\n"
            "%s"
            "%s"
            "Not bundled (system requirement):\n"
            "  - WebView2 runtime on Windows (preinstalled on Windows 10/11)\n"
            "  - WebKit2GTK / GTK3 on Linux\n"
        ) % (
            exe_name,
            f"mpv player ({mpv_dir})" if mpv_dir else "no mpv (install mpv, or app auto-installs it)",
            f"  - Playwright Chromium browser ({browser_dir})\n" if browser_dir else "",
            "\n",
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, arcname=exe_name)
            readme_name = "README.txt"
            zf.writestr(readme_name, readme)
        print(f"[*] Portable zip: {zip_path}")
        print(f"[*] Zip size: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")

    print("=" * 60)
    print("\nNotes:")
    print("  - External runtime deps NOT bundled: WebView2 runtime (Windows),")
    if os.name != "nt":
        print("    WebKit2GTK/GTK3 (Linux),")
    if not mpv_dir:
        print("    mpv (playback). Use --bundle-mpv/--mpv-dir to bundle it.")
    if not browser_dir:
        print("    Playwright Chromium browser. Use --bundle-browser/--browser-dir")
        print("    to bundle it, or install on the target machine with:\n"
              "      playwright install chromium")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        sys.exit(1)