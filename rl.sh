#!/usr/bin/env bash
# Optional small local policy training; never starts an LLM.
set -euo pipefail
RL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
source "$RL_ROOT/scripts/runtime.sh"
RL_PYTHON="$(minecraft_python)"
cd "$RL_ROOT"
# Keep this tiny CPU experiment from competing with the local LLM's thread pool.
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
exec "$RL_PYTHON" -m rl.runner "$@"
