#!/usr/bin/env bash
# Offline verification only: no real server, model inference, or downloads.
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$ROOT_DIR"
PYTHON_BIN="${MINECRAFT_PYTHON:-$HOME/miniforge3/envs/minecraft-agent/bin/python}"
case "${1:-}" in
    --help|-h)
        echo "Usage: ./check.sh"
        echo "Runs Python, Node, shell syntax, and cached Java plugin tests offline."
        echo "Set MINECRAFT_PYTHON or MINECRAFT_JAVA for another installation."
        exit 0 ;;
    "") ;;
    *) echo "Usage: ./check.sh" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "Usage: ./check.sh" >&2; exit 2; }
[ -x "$PYTHON_BIN" ] || { echo "Missing minecraft-agent Python; set MINECRAFT_PYTHON." >&2; exit 1; }
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || {
    echo "Node.js and npm are required." >&2; exit 1;
}
[ -d bot/node_modules ] || { echo "Install dependencies first: npm ci --prefix bot" >&2; exit 1; }

# Explicit live-test opt-ins must not turn this offline command into inference.
export RUN_MEMORY_LIVE_TEST=0
echo "Python contracts"
"$PYTHON_BIN" -m unittest discover -s tests -v
echo "Mineflayer tool contracts"
npm test --prefix bot
node tests/test_grounding.js
node tests/test_region_inspection.js
node tests/test_construction.js
node tests/test_construction_controller.js
node tests/test_operation_progress.js
echo "Shell and JavaScript syntax"
for script in start.sh practice.sh learn.sh rl.sh check.sh scripts/untrack-runtime.sh server/start.sh server-plugins/*/build.sh; do
    bash -n "$script"
done
for source in bot/*.js; do node --check "$source"; done
echo "Java plugin contracts"
bash server-plugins/jarvis-debug/build.sh --test
bash server-plugins/practice-oracle/build.sh
echo "Offline checks passed. No live gameplay or hosted-model evaluation performed."
