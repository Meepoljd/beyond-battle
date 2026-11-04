#!/usr/bin/env bash
set -euo pipefail
battle_root="$(cd "$(dirname "$0")/.." && pwd)"
battle_opencode_home="${BATTLE_OPENCODE_HOME:-$HOME/.local/share/beyond-battle/opencode}"
mkdir -p "$battle_opencode_home"/{work,config,data,cache,state}
export XDG_CONFIG_HOME="$battle_opencode_home/config"
export XDG_DATA_HOME="$battle_opencode_home/data"
export XDG_CACHE_HOME="$battle_opencode_home/cache"
export XDG_STATE_HOME="$battle_opencode_home/state"
export OPENCODE_CONFIG="$battle_root/config/opencode.json"
export OPENCODE_DISABLE_PROJECT_CONFIG=true
cd "$battle_opencode_home/work"
if [[ $# -eq 0 ]]; then
  set -- serve --hostname 127.0.0.1 --port "${BATTLE_OPENCODE_PORT:-4096}"
fi
exec "${BATTLE_OPENCODE_BIN:-opencode}" "$@"
