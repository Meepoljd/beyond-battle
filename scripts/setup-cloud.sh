#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required for the validated dependency lock"'
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Existing .venv does not use Python 3.12; select a matching environment"'
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.lock
.venv/bin/python -m pip check

# Tests must not seed the developer's real game database into a published image.
battle_setup_scratch="$(mktemp -d /tmp/beyond-battle-setup.XXXXXX)"
trap 'rm -rf -- "$battle_setup_scratch"' EXIT
BATTLE_DB="$battle_setup_scratch/test.sqlite3" .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check-startup.py

if command -v chromium >/dev/null 2>&1 || [[ -n "${CHROMIUM_PATH:-}" ]]; then
  BATTLE_SCREENSHOTS="$battle_setup_scratch/screenshots" .venv/bin/python tests/e2e_browser.py
else
  echo 'Browser E2E: NOT RUN (no system Chromium or CHROMIUM_PATH; no network policy changes made).'
fi
echo 'Development setup ready. Start the loopback game with: bash scripts/run.sh'
