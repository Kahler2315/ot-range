#!/usr/bin/env bash
# Double-click (or run) target for the OT Range control panel.
#
# Sets up the venv on first run if it's missing, then starts the panel
# and opens it in your browser automatically. Linux and WSL2 (Windows
# 10/11 via Docker Desktop's WSL integration) — see README.md's
# "Windows" section. Native (non-WSL) Windows is not supported: this
# range's router container needs raw packet capture, which is a Linux
# container thing regardless of host OS.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "[*] First run — setting up (venv + dependencies, ~1 minute)..."
  make setup
fi

if ! .venv/bin/python -c "import flask" 2>/dev/null; then
  echo "[*] Installing panel dependencies..."
  .venv/bin/pip install -q -r requirements.txt
fi

# One-time convenience: install a real double-clickable launcher into
# the desktop's app menu, with this clone's actual absolute path baked
# in (a static .desktop file shipped in the repo would have the wrong
# path for anyone else's clone). Skipped inside WSL — there's no Linux
# desktop shell to register with; see start-panel.bat for Windows.
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/ot-range-panel.desktop"
if [[ ! -f "$DESKTOP_FILE" ]] && [[ -d "$DESKTOP_DIR" || ! -f /proc/version ]] \
   && ! grep -qi microsoft /proc/version 2>/dev/null; then
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=OT Range Control Panel
Comment=Local web GUI for the OT Range cyber range
Exec=$REPO_ROOT/start-panel.sh
Path=$REPO_ROOT
Terminal=true
Icon=utilities-terminal
Categories=Development;Security;
EOF
  chmod +x "$DESKTOP_FILE"
  echo "[*] Installed an app-menu launcher: OT Range Control Panel"
fi

exec .venv/bin/python -m panel.app
