@echo off
echo Building portable ani-cli-arabic Windows GUI...
echo.
cd /d "%~dp0.."
python build_desktop.py --bundle-mpv --zip %*
echo.
pause