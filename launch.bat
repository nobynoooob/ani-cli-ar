@echo off
REM Easy launcher for the ani-cli-arabic desktop GUI on Windows.
REM Auto-activates a local virtualenv (venv) if present, then starts the app.
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

start "" python -m ani_cli_arabic.gui %*
