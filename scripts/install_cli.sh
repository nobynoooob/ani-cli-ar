#!/bin/sh
#
# install.sh — Installer for the ani-cli-ar CLI (Linux)
#
# Copies the standalone ani-cli-ar-cli executable into ~/.local/bin (default)
# or /usr/local/bin (--system).
#
# Usage (run from inside the extracted ani-cli-ar-cli bundle directory):
#   ./install.sh           # user install (default)
#   ./install.sh --system  # system-wide install (/usr/local/bin)
#

set -eu

# --- locate bundle files relative to this script ---------------------------
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN_NAME="ani-cli-ar-cli"
BIN_SRC="$SCRIPT_DIR/$BIN_NAME"

if [ ! -f "$BIN_SRC" ]; then
    echo "error: $BIN_SRC not found — run from the extracted ani-cli-ar-cli bundle directory." >&2
    exit 1
fi

# --- choose install destinations -------------------------------------------
SYSTEM=0
[ "${1:-}" = "--system" ] && SYSTEM=1

if [ "$SYSTEM" = "1" ]; then
    BIN_DIR="/usr/local/bin"
else
    BIN_DIR="$HOME/.local/bin"
fi

# --- install ---------------------------------------------------------------
echo "Installing $BIN_NAME to $BIN_DIR ..."
mkdir -p "$BIN_DIR"
install -m 0755 "$BIN_SRC" "$BIN_DIR/$BIN_NAME"

echo
echo "Done. Run the TUI with:"
echo "  $BIN_DIR/$BIN_NAME"