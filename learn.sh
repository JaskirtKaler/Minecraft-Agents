#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PYTHON_BIN="${MINECRAFT_PYTHON:-$HOME/miniforge3/envs/minecraft-agent/bin/python}"
cd "$ROOT_DIR"
exec "$PYTHON_BIN" -m practice.runner --learn "$@"
