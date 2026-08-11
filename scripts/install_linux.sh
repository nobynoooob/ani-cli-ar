#!/bin/sh
#
# install.sh — Installer for the ani-cli-ar GUI (Linux)
#
# Copies the standalone ani-cli-ar-gui executable into ~/.local/bin (default)
# or /usr/local/bin (--system) and registers it in the desktop menu via a
# .desktop entry in ~/.local/share/applications (or /usr/share/applications).
#
# Usage (run from inside the extracted ani-cli-ar-gui bundle directory):
#   ./install.sh           # user install (default)
#   ./install.sh --system  # system-wide install (/usr/local/bin)
#

set -eu

# --- locate bundle files relative to this script ---------------------------
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN_NAME="ani-cli-ar-gui"
BIN_SRC="$SCRIPT_DIR/$BIN_NAME"
DESKTOP_SRC="$SCRIPT_DIR/ani-cli-ar.desktop"
ICON_SRC="$SCRIPT_DIR/ani-cli-ar.png"

if [ ! -f "$BIN_SRC" ]; then
    echo "error: $BIN_SRC not found — run from the extracted ani-cli-ar-gui bundle directory." >&2
    exit 1
fi

# --- choose install destinations -------------------------------------------
SYSTEM=0
[ "${1:-}" = "--system" ] && SYSTEM=1

if [ "$SYSTEM" = "1" ]; then
    BIN_DIR="/usr/local/bin"
    APP_DIR="/usr/share/applications"
    ICON_DIR="/usr/share/icons/hicolor/256x256/apps"
else
    BIN_DIR="$HOME/.local/bin"
    APP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
fi

ICON_NAME="ani-cli-ar.png"

# --- install ---------------------------------------------------------------
echo "Installing $BIN_NAME to $BIN_DIR ..."
mkdir -p "$BIN_DIR"
install -m 0755 "$BIN_SRC" "$BIN_DIR/$BIN_NAME"

echo "Registering desktop entry in $APP_DIR ..."
mkdir -p "$APP_DIR"
# Rewrite the Exec line to the actual install location so the menu entry
# always resolves, regardless of user/system target.
sed "s|^Exec=.*|Exec=$BIN_DIR/$BIN_NAME|" "$DESKTOP_SRC" > "$APP_DIR/$BIN_NAME.desktop"
chmod 0644 "$APP_DIR/$BIN_NAME.desktop"

if [ -f "$ICON_SRC" ]; then
    echo "Installing icon to $ICON_DIR ..."
    mkdir -p "$ICON_DIR"
    install -m 0644 "$ICON_SRC" "$ICON_DIR/$ICON_NAME"
fi

# Refresh the desktop database so the entry appears immediately.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo
echo "Done. Launch ani-cli-ar GUI from your application menu, or run:"
echo "  $BIN_DIR/$BIN_NAME"