#!/bin/sh
#
# install.sh — One-line installer for ani-cli-ar
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/np4abdou1/ani-cli-arabic/main/install.sh | sh
#
# Supported environments: Linux, macOS, Termux (Android)

set -eu

REPO="np4abdou1/ani-cli-arabic"
BRANCH="main"

# ---- detect package manager ----
if command -v pipx >/dev/null 2>&1; then
    INSTALL_CMD="pipx install"
elif echo "$PREFIX" | grep -q "com.termux"; then
    # Termux
    pkg update -y
    pkg install -y python python-pip git
    INSTALL_CMD="pip install"
elif command -v pip3 >/dev/null 2>&1; then
    INSTALL_CMD="pip3 install --user"
elif command -v pip >/dev/null 2>&1; then
    INSTALL_CMD="pip install --user"
else
    echo "ERROR: Neither pipx nor pip found. Please install Python first."
    exit 1
fi

# ---- install ----
echo "Installing ani-cli-ar from $REPO ..."
$INSTALL_CMD "git+https://github.com/$REPO.git@$BRANCH"

echo ""
echo "ani-cli-ar installed successfully!"
echo "Run: ani-cli-ar"
