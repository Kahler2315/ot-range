#!/bin/bash
# Run by the Windows shortcut (see ot-range.iss's [Icons] section) via
# `wsl.exe bash "/mnt/c/.../bootstrap.sh"`. Clones the repo into WSL's
# own filesystem on first run, pulls latest on every later run, then
# hands off to start-panel.sh — so the shortcut always launches
# whatever's on `master` instead of going stale after the installer
# ships. `|| true` on the pull keeps this working offline once cloned.
set -e

if [ -d ~/ot-range ]; then
  cd ~/ot-range
  git pull -q || true
else
  git clone -q https://github.com/Kahler2315/ot-range.git ~/ot-range
  cd ~/ot-range
fi

exec ./start-panel.sh
