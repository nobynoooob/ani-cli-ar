import os
import sys
import subprocess
import shutil
import platform
import threading
import time
from pathlib import Path


def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def check_mpv_installed():
    try:
        result = subprocess.run(
            ["mpv", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_pyinstaller():
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    print("Installing PyInstaller...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )
        print("PyInstaller installed successfully")
        return True
    except subprocess.CalledProcessError:
        print("Failed to install PyInstaller")
        return False


def find_mpv_executable():
    try:
        result = subprocess.run(
            ["where" if platform.system() == "Windows" else "which", "mpv"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            mpv_path = result.stdout.strip().split("\n")[0]
            return Path(mpv_path) if mpv_path else None
    except Exception:
        pass
    return None


def create_spec_file(project_root, bundle_mpv=False):
    root = Path(project_root)
    pkg = "ani_cli_arabic"

    binaries = []
    if bundle_mpv:
        mpv_path = find_mpv_executable()
        if mpv_path and mpv_path.exists():
            binaries.append((str(mpv_path), "mpv"))

    binaries_str = repr(binaries) if binaries else "[]"

    hiddenimports = [
        f"{pkg}",
        f"{pkg}.api",
        f"{pkg}.app",
        f"{pkg}.cli",
        f"{pkg}.config",
        f"{pkg}.deps",
        f"{pkg}.discord_rpc",
        f"{pkg}.favorites",
        f"{pkg}.history",
        f"{pkg}.models",
        f"{pkg}.monitoring",
        f"{pkg}.player",
        f"{pkg}.settings",
        f"{pkg}.storage",
        f"{pkg}.ui",
        f"{pkg}.updater",
        f"{pkg}.utils",
        f"{pkg}.version",
        f"{pkg}.scrapers",
        f"{pkg}.scrapers.base",
        f"{pkg}.scrapers.miruro",
        f"{pkg}.scrapers.api_provider",
        f"{pkg}.scrapers.gogoanime",
        f"{pkg}.scrapers.mkissa",
        f"{pkg}.scrapers.provider_manager",
        "playwright.sync_api",
        "playwright.async_api",
        "httpx",
        "rich",
        "rich.console",
        "rich.panel",
        "rich.text",
        "rich.prompt",
        "rich.progress",
        "rich.align",
        "rich.box",
        "rich.table",
        "pypresence",
        "pycryptodomex",
    ]

    datas = [
        (str(root / "ani_cli_arabic"), pkg),
    ]

    icon_path = root / "assets" / "icon.ico"
    icon_line = ""
    if icon_path.exists():
        icon_line = f"icon=r'{icon_path}',"

    spec_content = f"""# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

block_cipher = None
root = Path(r"{root}")

a = Analysis(
    ['main.py'],
    pathex=[str(root)],
    binaries={binaries_str},
    datas={repr(datas)},
    hiddenimports={repr(hiddenimports)},
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'IPython', 'jupyter', 'notebook', 'matplotlib',
        'scipy', 'pandas', 'tkinter', 'PIL.ImageShow',
        'PIL.ImageTk',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ani-cli-ar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    {icon_line}
)
"""

    spec_file = root / "scripts" / "ani-cli-ar.spec"
    spec_file.write_text(spec_content, encoding="utf-8")
    return spec_file


def build_executable(bundle_mpv=False):
    system = platform.system()
    project_root = Path(__file__).resolve().parent.parent
    main_file = project_root / "main.py"
    dist_dir = project_root / "dist"

    if not main_file.exists():
        print(f"Error: {main_file} not found")
        return False

    print_header("Building Executable")
    print(f"System: {system}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Entry: {main_file}")

    if bundle_mpv:
        mpv_path = find_mpv_executable()
        if mpv_path and mpv_path.exists():
            print(f"MPV: {mpv_path} (will be bundled)")
        else:
            print("MPV: Not found in PATH, skipping bundle")
            bundle_mpv = False
    else:
        print("MPV: User will need to install separately")

    print("\nGenerating build configuration...")
    spec_file = create_spec_file(project_root, bundle_mpv)
    print(f"Created: {spec_file}")

    print("\nBuilding executable...\n")

    stages = {
        0: "Initializing",
        1: "Analyzing dependencies",
        2: "Building module graph",
        3: "Processing hooks",
        4: "Building PYZ archive",
        5: "Building PKG archive",
        6: "Building executable",
        7: "Finalizing",
    }

    current_stage = [0]
    build_complete = threading.Event()
    build_success = [False]

    def run_build():
        try:
            cmd = [
                sys.executable,
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                str(spec_file),
            ]
            process = subprocess.Popen(
                cmd,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )

            for line in process.stdout:
                line = line.strip()
                if "PyInstaller:" in line:
                    current_stage[0] = 0
                elif "Analyzing" in line or "Looking for" in line:
                    current_stage[0] = max(current_stage[0], 1)
                elif "Initializing module" in line or "module dependency graph" in line:
                    current_stage[0] = max(current_stage[0], 2)
                elif "Processing" in line and "hook" in line:
                    current_stage[0] = max(current_stage[0], 3)
                elif "Building PYZ" in line:
                    current_stage[0] = max(current_stage[0], 4)
                elif "Building PKG" in line:
                    current_stage[0] = max(current_stage[0], 5)
                elif "Building EXE" in line or "Building BUNDLE" in line:
                    current_stage[0] = max(current_stage[0], 6)
                elif "completed successfully" in line:
                    current_stage[0] = 7

            process.wait()
            build_success[0] = process.returncode == 0
        except Exception:
            build_success[0] = False
        finally:
            current_stage[0] = 7
            build_complete.set()

    build_thread = threading.Thread(target=run_build, daemon=True)
    build_thread.start()

    start_time = time.time()
    bar_width = 40
    last_stage = -1
    stage_start_time = time.time()

    while not build_complete.is_set():
        elapsed = time.time() - start_time
        stage = current_stage[0]

        stage_weights = {0: 5, 1: 15, 2: 20, 3: 15, 4: 15, 5: 15, 6: 10, 7: 5}
        total_weight = sum(stage_weights.values())
        completed_weight = sum(stage_weights[i] for i in range(stage))

        if stage < 7:
            if stage != last_stage:
                last_stage = stage
                stage_start_time = time.time()

            time_in_stage = time.time() - stage_start_time
            stage_completion = min(0.9, time_in_stage / 10.0)
            current_weight = stage_weights[stage] * stage_completion
        else:
            current_weight = stage_weights.get(7, 0)

        overall_progress = (completed_weight + current_weight) / total_weight

        if elapsed > 3 and overall_progress > 0.1:
            estimated_total = elapsed / overall_progress
            eta_seconds = max(0, estimated_total - elapsed)
            eta_minutes = int(eta_seconds // 60)
            eta_secs = int(eta_seconds % 60)
            eta_str = f"{eta_minutes}m {eta_secs}s" if eta_minutes > 0 else f"{eta_secs}s"
        else:
            eta_str = "calculating..."

        filled = int(bar_width * overall_progress)
        bar = "█" * filled + "░" * (bar_width - filled)
        percentage = int(overall_progress * 100)
        stage_name = stages.get(stage, "Processing")

        sys.stdout.write(
            f"\r   [{bar}] {percentage:>3}% | {stage_name:<30} | ETA: {eta_str:<12}"
        )
        sys.stdout.flush()
        time.sleep(0.3)

    sys.stdout.write(
        f"\r   [{'█' * bar_width}] 100% | Build complete{' ' * 30} | Done!{' ' * 12}\n"
    )
    sys.stdout.flush()

    if not build_success[0]:
        print("\nBuild failed. Check PyInstaller output for errors.")
        return False

    exe_name = "ani-cli-ar.exe" if system == "Windows" else "ani-cli-ar"
    exe_path = dist_dir / exe_name

    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild successful!")
        print(f"Executable: {exe_path}")
        print(f"Size: {size_mb:.2f} MB")

        if spec_file.exists():
            spec_file.unlink()
            print(f"Cleaned up: {spec_file.name}")

        return True

    print("Build failed: Executable not found")
    return False


def main():
    print_header("ani-cli-arabic Build Tool")

    if not check_pyinstaller():
        print("PyInstaller not found")
        if not install_pyinstaller():
            print("\nCannot continue without PyInstaller")
            return 1
        print()

    print("Checking system requirements...\n")

    mpv_installed = check_mpv_installed()
    if mpv_installed:
        print("MPV found")
    else:
        print("MPV not found (optional for bundling)")

    bundle_mpv = False
    if mpv_installed:
        print()
        system = platform.system()
        mpv_name = "MPV.exe" if system == "Windows" else "MPV"
        mpv_response = input(f"\nBundle {mpv_name} with the executable? (y/n): ").strip().lower()
        bundle_mpv = mpv_response == "y"

        if bundle_mpv:
            print("MPV will be bundled (larger file size, but no external player needed)")
        else:
            print("MPV will NOT be bundled")

    print()
    response = input("Proceed with build? (y/n): ").strip().lower()

    if response != "y":
        print("\nBuild cancelled")
        return 0

    if build_executable(bundle_mpv):
        print("\n" + "=" * 60)
        print("  BUILD COMPLETE!")
        print("=" * 60)

        if not bundle_mpv:
            print("\nNote: MPV is NOT bundled in the executable.")
            print("Users will need MPV installed to play videos.")

        return 0
    else:
        print("\n" + "=" * 60)
        print("  BUILD FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nBuild interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        sys.exit(1)
