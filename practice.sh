#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
source "$ROOT_DIR/scripts/runtime.sh"
cd "$ROOT_DIR"
PYTHON_BIN="$(minecraft_python)"
exec "$PYTHON_BIN" -m practice.runner "$@"
