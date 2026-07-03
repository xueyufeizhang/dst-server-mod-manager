#!/usr/bin/env bash
# Update dst-mod-manager in place: pull the latest code, sync dependencies,
# restart the panel service. Your config.yaml and backups/ are untouched
# (they are gitignored).
#
# Usage:  ./update.sh
#         DST_MOD_MANAGER_SERVICE=my-panel ./update.sh   # custom unit name
set -euo pipefail
cd "$(dirname "$0")"

SERVICE="${DST_MOD_MANAGER_SERVICE:-dst-mod-manager}"

echo "==> pulling latest code..."
git pull --ff-only

echo "==> syncing dependencies..."
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if command -v systemctl >/dev/null 2>&1 && systemctl cat "$SERVICE" >/dev/null 2>&1; then
    echo "==> restarting $SERVICE ..."
    sudo systemctl restart "$SERVICE"
    sleep 1
    systemctl --no-pager -l status "$SERVICE" | head -5 || true
else
    echo "==> no systemd unit '$SERVICE' found — restart the panel manually."
fi

echo "==> update complete."
