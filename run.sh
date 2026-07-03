#!/usr/bin/env bash
# Start dst-mod-manager. Creates a virtualenv on first run.
#
# Usage:
#   ./run.sh                     # uses config.yaml (or $DST_MOD_MANAGER_CONFIG)
#   ./run.sh config.sample.yaml  # try the bundled sample data
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "[run.sh] creating virtualenv and installing dependencies..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt
fi

exec .venv/bin/python -m app.main "$@"
