#!/usr/bin/env bash
# Easy launcher for the ani-cli-arabic desktop GUI.
# Detects an active virtualenv (venv/ or .venv/) and runs the pywebview app.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
elif [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

exec python3 -m ani_cli_arabic.gui "$@"
