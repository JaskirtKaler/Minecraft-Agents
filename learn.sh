#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PYTHON_BIN="${MINECRAFT_PYTHON:-$HOME/miniforge3/envs/minecraft-agent/bin/python}"
cd "$ROOT_DIR"
LEARN_BACKEND=minecraft
LEARN_ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --backend)
            [ "$#" -ge 2 ] || { echo "--backend needs minecraft or simulator" >&2; exit 2; }
            LEARN_BACKEND="$2"
            shift 2 ;;
        *) LEARN_ARGS+=("$1"); shift ;;
    esac
done
case "$LEARN_BACKEND" in
    simulator)
        # Separate Python-only spatial practice from real isolated-server
        # learning. No Minecraft process or normal world is opened here.
        set -- ${LEARN_ARGS[@]+"${LEARN_ARGS[@]}"}
        SIMULATOR_ARGS=()
        SIMULATOR_SUITE=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --suite)
                    [ "$#" -ge 2 ] || { echo "--suite needs construction" >&2; exit 2; }
                    SIMULATOR_SUITE="$2"; shift 2 ;;
                *) SIMULATOR_ARGS+=("$1"); shift ;;
            esac
        done
        [ "$SIMULATOR_SUITE" = construction ] || {
            echo "Simulator learning requires --suite construction" >&2; exit 2;
        }
        exec "$PYTHON_BIN" -m practice.construction ${SIMULATOR_ARGS[@]+"${SIMULATOR_ARGS[@]}"} ;;
    minecraft) exec "$PYTHON_BIN" -m practice.runner --learn ${LEARN_ARGS[@]+"${LEARN_ARGS[@]}"} ;;
    *) echo "Unknown --backend: $LEARN_BACKEND; use minecraft or simulator" >&2; exit 2 ;;
esac
