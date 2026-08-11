#!/usr/bin/env python3
"""Build standalone PyInstaller executables for ani-cli-arabic.

Two targets are supported:

  * ``--target gui`` — the pywebview desktop GUI. Windowed (--noconsole)
    one-file executable that bundles the entire Python runtime, the
    ``ani_cli_arabic`` package, its ``ui/`` static assets, pywebview and all
    third-party libraries.
  * ``--target cli`` — the terminal TUI. A console one-file executable that
    uses ``main.py`` as its entry point and aggressively excludes every GUI
    framework so no pywebview / Qt / Tk pixels are shipped.

Note that the Playwright Chromium *browser* is intentionally NOT bundled
(that is what bloated old builds to 400+ MB). The Playwright driver is bundled
(required to spawn a browser), and the actual Chromium binary is downloaded on
first use by ``ani_cli_arabic.playwright_bootstrap.ensure_playwright_chromium``.

External system dependencies are still required at runtime (not bundled by
PyInstaller): a WebView2 runtime on Windows and WebKit2GTK + GTK3 on Linux.
mpv CAN be bundled for the GUI so the result is portable (double-click to
launch) — as in the downloadable release.

Usage:
    python build_desktop.py                            # GUI build
    python build_desktop.py --target cli               # CLI build
    python build_desktop.py --target gui --bundle-mpv  # embed mpv (PATH or mpv/)
    python build_desktop.py --exclude-module numpy     # extra module exclusions
    python build_desktop.py --zip                      # also produce {exe}.zip
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
ENTRY_GUI = "ani-cli-ar-gui"
ENTRY_CLI = "ani-cli-ar-cli"

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


def _webview_platform_imports() -> list:
    """pywebview backends are loaded dynamically at runtime (guilib.initialize
    picks one per OS). Enumerate the platform modules that actually exist in
    the installed pywebview so the bundle always carries the right one(s),
    without PyInstaller warnings for modules that were dropped in newer
    versions (e.g. ``edgehtml`` was replaced by ``edgechromium`` in pywebview
    4.x)."""
    installed = []
    try:
        import webview.platforms as platforms_pkg
        pkg_dir = os.path.dirname(platforms_pkg.__file__)
        installed = sorted(
            name[:-3]
            for name in os.listdir(pkg_dir)
            if name.endswith(".py") and name != "__init__.py"
        )
    except Exception:
        pass
    preferred = [
        "winforms", "edgehtml", "edgechromium", "mshtml", "win32",
        "gtk", "gtk3", "qt", "cocoa", "cef",
    ]
    # Propose backends relevant to the current OS. The preferred list for that
    # OS is kept verbatim (PyInstaller merely warns on a missing hidden import,
    # e.g. edgehtml on pywebview >=4 where it was renamed to edgechromium),
    # then whatever the package actually ships is appended so nothing installed
    # is left out.
    if os.name == "nt":
        platform_names = ("winforms", "edgehtml", "edgechromium", "mshtml", "win32", "cef")
    elif sys.platform == "darwin":
        platform_names = ("cocoa", "qt", "cef")
    else:
        platform_names = ("gtk", "gtk3", "qt", "cef")
    preferred_matches = [n for n in preferred if n in platform_names]
    names = list(dict.fromkeys(preferred_matches + [n for n in installed if n in platform_names]))
    return [f"webview.platforms.{name}" for name in names]


def _webview_runtime_imports() -> list:
    """pythonnet/clr is required by pywebview's Windows (winforms) backend and
    is only present on that platform. ``hook-clr`` in hooks-contrib then picks
    up the native ``Python.Runtime.dll`` automatically."""
    if os.name != "nt":
        return []
    return ["clr", "clr_loader", "pythonnet"]


def _hidden_imports(target: str) -> list:
    """Modules imported lazily/dynamically that static analysis misses."""
    is_gui = target == "gui"
    _platforms = _webview_platform_imports() if is_gui else []
    _win_runtime = _webview_runtime_imports() if is_gui else []
    mods = [
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
    if is_gui:
        # pywebview backends (loaded dynamically at runtime) plus its HTTP
        # server / JS bridge deps.
        mods += [
            *_platforms,
            *_win_runtime,
            "webview", "webview.platforms", "webview.http", "bottle",
            "proxy_tools", "typing_extensions",
        ]
    return mods


def _collect_all() -> list:
    """Modules that must be bundled *wholesale* (data + binaries + submodules).
    Playwright's node driver lives in ``playwright/driver/`` and is required
    at runtime to spawn the browser — ``collect-submodules`` alone misses it.
    (The Chromium *browser* itself is deliberately NOT bundled; it is installed
    on first use via ``ensure_playwright_chromium``.)"""
    return ["playwright"]


def _collect_submodules(target: str) -> list:
    """Modules that ship many submodules we want to bundle wholesale."""
    mods = ["rich", "httpx", "websockets"]
    if target == "gui":
        mods.insert(0, "webview")
        if os.name == "nt":
            mods.append("clr_loader")
            mods.append("pythonnet")
        else:
            mods.append("realtime")
    else:
        # CLI: no webview, but watch-together still needs the realtime lib.
        mods.append("realtime")
    return mods


def _excludes(target: str, extra: list) -> list:
    """Modules never needed by the target build. ``extra`` holds user-supplied
    ``--exclude-module`` names."""
    base = [
        "IPython", "jupyter", "notebook", "matplotlib", "scipy", "pandas",
        "pytest",
    ]
    if target == "gui":
        # `email`/`numpy`/`PIL` must NOT be excluded: importing the package
        # (ani_cli_arabic/__init__.py) always pulls in app -> ui.py, which does
        # `import numpy` and `from PIL import Image, ImageEnhance` at module
        # load. httpx/websockets/cryptography also import email.* at import
        # time. Excluding any of them crashes the GUI at startup. PyQt/PySide/
        # customtkinter are no-ops (pywebview uses WinForms/GTK) but guard
        # against a stray Qt import.
        base += [
            "tkinter", "unittest", "pydoc",
            "PyQt5", "PyQt6", "PySide2", "PySide6", "customtkinter",
        ]
    else:
        # CLI build: aggressively drop every GUI framework. email must stay
        # (requests/httpx mail parsing is used for downloads).
        base += [
            "tkinter", "unittest", "pydoc",
            "webview", "bottle", "proxy_tools",
            "pythonnet", "clr_loader",
            "PyQt5", "PyQt6", "PySide2", "PySide6", "customtkinter",
        ]
    return list(dict.fromkeys(base + extra))


def _cli_entry_script() -> "Path | None":
    """The CLI build uses ``main.py`` at the repo root as its PyInstaller entry
    (it reconfigures the console, then dispatches to ``ani_cli_arabic.app``)."""
    entry = ROOT / "main.py"
    return entry if entry.exists() else None


def build():
    parser = argparse.ArgumentParser(description="Build desktop GUI executable")
    parser.add_argument("--target", choices=("gui", "cli"), default="gui",
                        help="Build the pywebview GUI (default) or the terminal "
                             "CLI (aggressively excludes GUI frameworks)")
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
    parser.add_argument("--exclude-module", metavar="NAME", action="append",
                        default=[],
                        help="Extra module to exclude from the bundle "
                             "(repeatable)")
    parser.add_argument("--exe-name", metavar="NAME",
                        help=f"Output executable name (default: per target — "
                             f"{ENTRY_GUI} or {ENTRY_CLI})")
    parser.add_argument("--zip", action="store_true",
                        help="Also produce dist/<exe-name>.zip")
    parser.add_argument("--skip-install", action="store_true",
                        help="Fail instead of auto-installing PyInstaller")
    args = parser.parse_args()

    print("=" * 60)
    print(f"  ani-cli-arabic {'GUI' if args.target == 'gui' else 'CLI'} Builder")
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

    is_gui = args.target == "gui"
    exe_name = args.exe_name or (ENTRY_GUI if is_gui else ENTRY_CLI)

    # GUI entry requires pywebview at runtime.
    if is_gui:
        try:
            import webview  # noqa: F401
        except ImportError:
            _err("pywebview is required for a GUI build. Install with: "
                 "pip install 'pywebview>=4.0'")

    entry = _create_entry_script() if is_gui else _cli_entry_script()
    if entry is None:
        _err("CLI entry script main.py not found.")

    ui_dir = ROOT / PKG / "ui"
    if is_gui and not (ui_dir / "index.html").exists():
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
    add_data = []
    if is_gui:
        # Use os.fspath() so Windows drive-letter/backslash paths are passed to
        # PyInstaller verbatim (no accidental forward-slash rewrites or doubled
        # backslashes in the spec we later attach to the bundle).
        ui_dir_str = os.fspath(ui_dir)
        add_data.append(f"{ui_dir_str}{sep}{PKG}/ui")
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
        add_binaries.append(f"{os.fspath(mpv_dir)}{sep}{MPV_DEST}")
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
        add_data.append(f"{os.fspath(browser_dir)}{sep}{BROWSER_DEST}")
        hook = _create_browser_hook()
        print(f"[*] Bundling Playwright browsers from: {browser_dir}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(entry),
        "--name", exe_name,
        "--onefile",
        "--clean",
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
    ]

    if is_gui:
        # The GUI is a windowed app; the CLI must keep its console for the TUI.
        cmd.append("--noconsole")

    for data in add_data:
        cmd += ["--add-data", data]
    for binary in add_binaries:
        cmd += ["--add-binary", binary]
    if browser_dir:
        cmd += ["--runtime-hook", str(ROOT / "build" / "_browsers_path_hook.py")]
    for mod in _hidden_imports(args.target):
        cmd += ["--hidden-import", mod]
    for mod in _collect_submodules(args.target):
        cmd += ["--collect-submodules", mod]
    for mod in _collect_all():
        cmd += ["--collect-all", mod]
    for mod in _excludes(args.target, args.exclude_module):
        cmd += ["--exclude-module", mod]
    if icon:
        cmd += ["--icon", str(icon)]

    if not args.debug:
        cmd += ["--log-level", "ERROR"]

    exe_name_os = exe_name + (".exe" if os.name == "nt" else "")
    print(f"[*] Output: dist/{exe_name_os}")
    print("[*] Building...\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        _err("PyInstaller build failed (re-run with --debug for details).")

    exe = ROOT / "dist" / exe_name_os
    if not exe.exists():
        _err(f"Build reported success but {exe} was not found.")

    size_mb = exe.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  BUILD SUCCESSFUL")
    print(f"  {exe}")
    print(f"  Size: {size_mb:.1f} MB")

    # ----- portable zip ------------------------------------------------------
    if args.zip:
        zip_name = f"{exe_name}.zip"
        zip_path = ROOT / "dist" / zip_name
        readme_header = "Double-click %s to launch the GUI.\n\n" % exe_name_os if is_gui else \
            "Run %s from a terminal to open the TUI.\n\n" % exe_name_os
        readme = (
            "ani-cli-arabic - portable build\n"
            "=============================\n\n"
            + readme_header +
            "Bundled:\n"
            "  - Python runtime and all application libraries\n"
            "  - %s\n"
            "%s"
            "%s"
            "Not bundled (system requirement):\n"
            "  - WebView2 runtime on Windows (preinstalled on Windows 10/11)\n"
            "  - WebKit2GTK / GTK3 on Linux\n"
        ) % (
            f"mpv player ({mpv_dir})" if mpv_dir else "no mpv (install mpv, or app auto-installs it)",
            f"  - Playwright Chromium browser ({browser_dir})\n" if browser_dir else "",
            "\n",
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, arcname=exe_name_os)
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
        print("    Playwright Chromium browser. The app auto-installs it on first")
        print("    use (python -m playwright install chromium equivalent).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        sys.exit(1)