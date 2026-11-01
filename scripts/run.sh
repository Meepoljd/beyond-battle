#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv/bin/python ]]; then
  battle_python=.venv/bin/python
else
  battle_python=python3
fi
# Loopback only. Changing network exposure requires a separate deployment decision.
exec "$battle_python" -m uvicorn server.api:app --host 127.0.0.1 --port "${BATTLE_PORT:-8765}" --workers 1
