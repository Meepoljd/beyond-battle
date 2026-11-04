# Beyond Battle development workspace

This repository contains only the 战报之外 bridge-exercise game. Work from this
repository root; do not use or modify MyAgent, its credentials, deployments, or
databases. Read README.md and docs/DEVELOPMENT_PLAN.md before changing gameplay.

## Environment

- Python 3.12, SQLite, FastAPI, plain browser HTML/CSS/JavaScript.
- Prepare the isolated local environment with `bash scripts/setup-cloud.sh`.
- Run commands with `.venv/bin/python`; there is no frontend npm build.
- Start the game with `bash scripts/run.sh` (127.0.0.1:8765 by default).
- `BATTLE_DB` selects a game-only SQLite path. Never commit runtime databases,
  `.env`, credentials, `.venv`, browser artifacts, or generated archives.
- Repository source persists in GitHub. Task files and game saves are not a
  permanent hosting or backup service.

## Checks

- Logic/API/storage: `.venv/bin/python -m unittest discover -s tests -v`.
- UI: `.venv/bin/python tests/e2e_browser.py`; prefers system Chromium, with
  `CHROMIUM_PATH` available to select an existing browser executable.
- Startup: `.venv/bin/python scripts/check-startup.py`.
- Use the relevant checks for changes. Report PASS/FAIL/NOT RUN honestly.
- Keep existing unrelated edits. For subsequent feature work use a descriptive
  branch and prepare a PR unless the user has requested another delivery flow.

## Invariants

- Only the simulator changes battlefield truth. Agent providers receive limited
  observations and submit validated proposals, never arbitrary tools or code.
- Player APIs expose delivered reports and acknowledgements only. Full truth is
  available only after an exercise ends.
- Idempotency, expected versions, atomic SQLite commits, and recorded decisions
  must preserve reliable recovery and replay. Never wait on a model in a write
  transaction. Never advance turns based on server downtime.
- Memories require known-to-character source records, review, bounded injection,
  and deactivation. No permanent casualties, equipment rewards, or truth leaks.
- Rule changes need explicit scenario/ruleset version consideration. Review
  expected behavior before updating a golden fixture; do not merely bless a
  changed test hash.
- This build is rules-only. Real model providers, credentials and paid calls
  require the user's supplier and budget decision. Do not borrow project keys.
- NUC deployment and public exposure are separate from cloud development.

## Reusable cloud setup

The intended configuration is documented in docs/CODEX_CLOUD.md. Files in this
repository do not themselves create or publish a platform environment. Only
report one as published when the platform confirms that operation.
