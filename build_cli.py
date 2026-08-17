#!/usr/bin/env python3
"""Build a standalone PyInstaller CLI executable for ani-cli-arabic.

The terminal TUI (``ani_cli_arabic.app``). A console one-file executable that
uses ``main.py`` as its entry point and aggressively excludes every GUI
framework so no pywebview / Qt / Tk pixels are shipped.

Note that the Playwright Chromium *browser* is intentionally NOT bundled
(that is what bloated old builds to 400+ MB). The Playwright driver is bundled
(required to spawn a browser), and the actual Chromium binary is downloaded on
first use by ``ani_cli_arabic.playwright_bootstrap.ensure_playwright_chromium``.

Usage:
    python build_cli.py                              # CLI build
    python build_cli.py --exclude-module numpy       # extra module exclusions
    python build_cli.py --zip                        # also produce {exe}.zip
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


def _hidden_imports() -> list:
    """Modules imported lazily/dynamically that static analysis misses."""
    return [
        # package modules
        f"{PKG}.api", f"{PKG}.app", f"{PKG}.cli", f"{PKG}.config",
        f"{PKG}.deps", f"{PKG}.discord_rpc", f"{PKG}.favorites",
        f"{PKG}.history", f"{PKG}.models",
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


def _collect_all() -> list:
    """Modules that must be bundled *wholesale* (data + binaries + submodules).
    Playwright's node driver lives in ``playwright/driver/`` and is required
    at runtime to spawn the browser — ``collect-submodules`` alone misses it.
    (The Chromium *browser* itself is deliberately NOT bundled; it is installed
    on first use via ``ensure_playwright_chromium``.)"""
    return ["playwright"]


def _collect_submodules() -> list:
    """Modules that ship many submodules we want to bundle wholesale."""
    return ["rich", "httpx", "websockets", "realtime"]


def _excludes(extra: list) -> list:
    """Modules never needed by the CLI build. ``extra`` holds user-supplied
    ``--exclude-module`` names."""
    base = [
        "IPython", "jupyter", "notebook", "matplotlib", "scipy", "pandas",
        "pytest",
        # Heavy deps pulled in only by libs we never import:
        "pyiceberg", "zstandard", "uvloop",
        # stdlib / tooling bloat never needed at runtime
        "test", "pydoc_data", "lib2to3", "setuptools", "pip", "wheel",
    ]
    # Aggressively drop every GUI framework. email must stay (requests/httpx
    # mail parsing is used for downloads).
    base += [
        "tkinter", "unittest", "pydoc",
        "webview", "bottle", "proxy_tools",
        "pythonnet", "clr_loader",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "customtkinter",
    ]
    return list(dict.fromkeys(base + extra))


def _create_ssl_certs_hook() -> Path:
    """Write a PyInstaller runtime hook that wires the bundled certifi CA
    bundle into the SSL machinery before any app code runs."""
    hook = ROOT / "build" / "_ssl_certs_hook.py"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "if getattr(sys, 'frozen', False):\n"
        "    meipass = getattr(sys, '_MEIPASS', None)\n"
        "    if meipass:\n"
        "        cafile = os.path.join(meipass, 'certifi', 'cacert.pem')\n"
        "        if os.path.isfile(cafile):\n"
        "            os.environ.setdefault('SSL_CERT_FILE', cafile)\n"
        "            os.environ.setdefault('REQUESTS_CA_BUNDLE', cafile)\n"
        "            os.environ.setdefault('CURL_CA_BUNDLE', cafile)\n",
        encoding="utf-8",
    )
    return hook


def _write_spec(spec_path: Path, *, entry: Path, exe_name: str,
                strip: bool, datas: list, binaries: list,
                hiddenimports: list, collect_submodules: list,
                collect_all: list, excludes: list,
                runtime_hooks: list) -> Path:
    lines: list = []
    a = lines.append
    a("# -*- mode: python ; coding: utf-8 -*-")
    a("from PyInstaller.utils.hooks import collect_submodules")
    a("from PyInstaller.utils.hooks import collect_all")
    a("")
    a(f"datas = {datas!r}")
    a(f"binaries = {binaries!r}")
    a(f"hiddenimports = {hiddenimports!r}")
    a("")
    for mod in collect_submodules:
        a(f"hiddenimports += collect_submodules({mod!r})")
    for mod in collect_all:
        a(f"tmp_ret = collect_all({mod!r})")
        a("datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]")
    a("")
    a("a = Analysis(")
    a(f"    [{str(entry)!r}],")
    a("    pathex=[],")
    a("    binaries=binaries,")
    a("    datas=datas,")
    a("    hiddenimports=hiddenimports,")
    a("    hookspath=[],")
    a("    runtime_hooks={runtime_hooks!r},")
    a(f"    excludes={excludes!r},")
    a("    noarchive=False,")
    a("    optimize=0,")
    a(")")
    a("pyz = PYZ(a.pure)")
    a("exe = EXE(")
    a("    pyz,")
    a("    a.scripts,")
    a("    a.binaries,")
    a("    a.datas,")
    a("    [],")
    a(f"    name={exe_name!r},")
    a("    debug=False,")
    a("    bootloader_ignore_signals=False,")
    a(f"    strip={strip},")
    a("    upx=False,")
    a("    upx_exclude=[],")
    a("    runtime_tmpdir=None,")
    a("    console=True,")
    a("    disable_windowed_traceback=False,")
    a("    argv_emulation=False,")
    a("    target_arch=None,")
    a("    codesign_identity=None,")
    a("    entitlements_file=None,")
    a("    icon=[],")
    a(")")
    a("")
    spec_path.write_text("\n".join(lines), encoding="utf-8")
    return spec_path


def build():
    parser = argparse.ArgumentParser(description="Build CLI executable")
    parser.add_argument("--debug", action="store_true",
                        help="Show full PyInstaller output")
    parser.add_argument("--exclude-module", metavar="NAME", action="append",
                        default=[],
                        help="Extra module to exclude from the bundle "
                             "(repeatable)")
    parser.add_argument("--exe-name", metavar="NAME",
                        help="Output executable name (default: ani-cli-ar-cli)")
    parser.add_argument("--zip", action="store_true",
                        help="Also produce dist/<exe-name>.zip")
    parser.add_argument("--skip-install", action="store_true",
                        help="Fail instead of auto-installing PyInstaller")
    parser.add_argument("--no-strip", action="store_true",
                        help="Do NOT strip debug symbols from the executable and "
                             "bundled shared libraries (strip is on by default "
                             "on POSIX; it is a no-op on Windows)")
    args = parser.parse_args()

    print("=" * 60)
    print("  ani-cli-arabic CLI Builder")
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

    exe_name = args.exe_name or "ani-cli-ar-cli"

    entry = ROOT / "main.py"
    if not entry.exists():
        _err("CLI entry script main.py not found.")

    # PyInstaller data/binary tuples (source, destination).
    add_data = []
    add_binaries = []

    # SSL CA bundle (certifi)
    try:
        import certifi
        add_data.append((os.fspath(certifi.where()), "certifi"))
    except Exception:
        pass

    # Strip debug symbols (POSIX only — PyInstaller has no strip support on Windows).
    strip = not args.no_strip and os.name != "nt"
    ssl_hook = _create_ssl_certs_hook()
    spec = _write_spec(
        ROOT / "build" / f"{exe_name}.spec",
        entry=entry,
        exe_name=exe_name,
        strip=strip,
        datas=add_data,
        binaries=add_binaries,
        hiddenimports=_hidden_imports(),
        collect_submodules=_collect_submodules(),
        collect_all=_collect_all(),
        excludes=_excludes(args.exclude_module),
        runtime_hooks=[str(ssl_hook)],
    )

    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(spec)]
    if not args.debug:
        cmd += ["--log-level", "ERROR"]

    exe_name_os = exe_name + (".exe" if os.name == "nt" else "")
    print(f"[*] Output: dist/{exe_name_os}")
    if strip:
        print("[*] Strip: enabled (removes debug symbols)")
    print("[*] Building...\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        _err("PyInstaller build failed (re-run with --debug for details).")

    dist_dir = ROOT / "dist"
    exe = dist_dir / exe_name_os
    if not exe.exists():
        _err(f"Build reported success but {exe} was not found.")
    size_mb = exe.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  BUILD SUCCESSFUL")
    print(f"  {exe}")
    print(f"  Size: {size_mb:.1f} MB")

    # portable zip
    if args.zip:
        zip_name = f"{exe_name}.zip"
        zip_path = ROOT / "dist" / zip_name
        readme = (
            "ani-cli-arabic - portable build\n"
            "=============================\n\n"
            "Run %s from a terminal to open the TUI.\n\n"
            "Not bundled (system requirement):\n"
            "  - mpv player (installed separately)\n"
        ) % exe_name_os
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, arcname=exe_name_os)
            zf.writestr("README.txt", readme)
        print(f"[*] Portable zip: {zip_path}")
        print(f"[*] Zip size: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except KeyboardInterrupt:
        print("\nBuild interrupted.")
        sys.exit(1)