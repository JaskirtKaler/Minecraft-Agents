#!/usr/bin/env bash
# Local, foreground supervisor. Ctrl+C stops only processes started by this run.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$ROOT_DIR"
PYTHON_BIN="${MINECRAFT_PYTHON:-$HOME/miniforge3/envs/minecraft-agent/bin/python}"
JAVA_BIN="${MINECRAFT_JAVA:-}"
CHECK_ONLY=0

usage() {
    echo "Usage: ./start.sh [--check]"
    echo "Starts local Minecraft, the Python controller, and the Mineflayer bot."
    echo "Reuses Ollama/Jarvis if running; otherwise starts a local Ollama service."
    echo "Keep this terminal open. Ctrl+C saves/stops Minecraft and shuts down the agent."
    echo "--check validates setup and port availability without launching anything."
    echo "Optional overrides: MINECRAFT_PYTHON, MINECRAFT_JAVA."
}
case "${1:-}" in
    "") ;;
    --check) CHECK_ONLY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then usage >&2; exit 2; fi

fail() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$PYTHON_BIN" ] || fail "Conda Python not found: $PYTHON_BIN. Set MINECRAFT_PYTHON to your minecraft-agent interpreter."
command -v node >/dev/null 2>&1 || fail "Node.js is not installed or not on PATH."
[ -d "$ROOT_DIR/bot/node_modules" ] || fail "Install bot dependencies first: npm ci --prefix bot"
[ -f "$ROOT_DIR/.env" ] || fail "Create .env from .env.example and configure your local model first."
[ -s "$ROOT_DIR/server/server.jar" ] || fail "Missing server/server.jar. Complete server setup first."
[ -s "$ROOT_DIR/server/plugins/ViaVersion.jar" ] || fail "Missing ViaVersion plugin. Complete server setup first."
[ -s "$ROOT_DIR/server/plugins/ViaBackwards.jar" ] || fail "Missing ViaBackwards plugin. Complete server setup first."

if [ -z "$JAVA_BIN" ]; then
    if [ -x /usr/libexec/java_home ]; then
        JAVA_HOME_FOUND="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
        if [ -n "$JAVA_HOME_FOUND" ]; then JAVA_BIN="$JAVA_HOME_FOUND/bin/java"; fi
    fi
    if [ -z "$JAVA_BIN" ]; then JAVA_BIN="$(command -v java || true)"; fi
fi
[ -n "$JAVA_BIN" ] && [ -x "$JAVA_BIN" ] || fail "Java 21 not found. Set MINECRAFT_JAVA to its java executable."
JAVA_INFO="$("$JAVA_BIN" -version 2>&1)" || fail "Java could not start."
echo "$JAVA_INFO" | head -n 1

# Parse dotenv as data, never as shell code. Emit only explicitly selected,
# shell-quoted non-secret settings. This preserves the existing memory identity
# even though all listeners are restricted to IPv4 loopback for this launcher.
SETTINGS="$("$PYTHON_BIN" - <<'PY'
import importlib
import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    if sys.version_info < (3, 12):
        raise RuntimeError("The minecraft-agent environment needs Python 3.12+.")
    from agent.config import config
    import main
    if config.memory_enabled and config.graphiti_enabled:
        for name in ("graphiti_core", "redislite", "falkordb"):
            importlib.import_module(name)
    if config.mc_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This launcher is for the local world only; MC_HOST must be localhost.")
    if config.mc_version != "1.20.1":
        raise RuntimeError("Keep MC_VERSION=1.20.1 for this bundled server and bot.")
    if not all(1 <= p <= 65535 for p in (config.mc_port, config.ws_port)):
        raise RuntimeError("MC_PORT and WS_PORT must be valid TCP ports.")
    if config.mc_port == config.ws_port:
        raise RuntimeError("MC_PORT and WS_PORT must be different.")
    eula_path = Path("server/eula.txt")
    properties = {}
    if eula_path.exists():
        for line in eula_path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                properties[key.strip()] = value.strip()
    if properties.get("eula", "").lower() != "true":
        raise RuntimeError("Accept Minecraft's EULA yourself in server/eula.txt before launching.")
    def local_ollama(url):
        parsed = urlparse(url)
        return parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434
    endpoints = [config.nebius_base_url, os.getenv("JARVIS_ENDPOINT", "http://localhost:11434/api/chat")]
    if config.memory_enabled and config.graphiti_enabled:
        endpoints.append(config.memory_base_url)
    settings = {
        "MC_PORT": str(config.mc_port),
        "WS_PORT": str(config.ws_port),
        "MC_WORLD_ID": os.getenv("MC_WORLD_ID") or f"{config.mc_host}:{config.mc_port}",
        "BOT_USERNAME": config.mc_username,
        "MC_TRAINING_MODE": "true" if config.mc_training_mode else "false",
        "NEEDS_OLLAMA": "1" if any(local_ollama(url) for url in endpoints) else "0",
    }
    for key, value in settings.items():
        print(f"{key}={shlex.quote(value)}")
except Exception as exc:
    print(f"Setup check failed: {exc}", file=sys.stderr)
    print("For missing Python packages, use the minecraft-agent Python with: -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)
PY
)" || fail "Python/configuration preflight failed."
eval "$SETTINGS"
export MC_PORT WS_PORT MC_WORLD_ID
export MC_HOST=127.0.0.1 WS_HOST=127.0.0.1 WS_URL="ws://127.0.0.1:$WS_PORT"
export PYTHONUNBUFFERED=1

port_open() {
    "$PYTHON_BIN" - "$1" <<'PY' >/dev/null 2>&1
import socket
import sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.3):
        pass
except OSError:
    sys.exit(1)
PY
}
if port_open "$MC_PORT"; then
    fail "Minecraft port $MC_PORT is already occupied. Stop the existing server before using this launcher."
fi
if port_open "$WS_PORT"; then
    fail "Controller port $WS_PORT is already occupied. Stop the existing controller before using this launcher."
fi
if [ "$NEEDS_OLLAMA" = 1 ] && ! port_open 11434; then
    command -v ollama >/dev/null 2>&1 || fail "Install/start Ollama for the configured local models."
fi
if [ "$CHECK_ONLY" = 1 ]; then
    echo "Setup checks passed. Join address after launch: 127.0.0.1:$MC_PORT"
    exit 0
fi

SERVER_PID="" CONTROLLER_PID="" BOT_PID="" OLLAMA_PID=""
RUN_DIR="" SERVER_FIFO="" FIFO_OPEN=0 LOCK_OWNED=0
LOCK_DIR="$ROOT_DIR/data/run/start.lock"
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
wait_stopped() {
    local pid="$1" seconds="$2" elapsed=0
    while alive "$pid"; do
        if [ "$elapsed" -ge "$seconds" ]; then return 1; fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    wait "$pid" 2>/dev/null || true
}
stop_child() {
    local pid="$1" name="$2"
    if alive "$pid"; then
        kill -TERM "$pid" 2>/dev/null || true
        if ! wait_stopped "$pid" 15; then
            echo "WARNING: $name PID $pid is still shutting down. Leaving it running instead of force-killing it."
        fi
    fi
}
cleanup() {
    local result=$? remaining_pid=""
    trap - EXIT
    trap '' INT TERM HUP
    set +e
    if [ -n "$RUN_DIR" ]; then echo; echo "Stopping this launcher's services..."; fi
    stop_child "$BOT_PID" "Mineflayer bot"
    stop_child "$CONTROLLER_PID" "Python controller"
    if alive "$SERVER_PID"; then
        echo "Saving and stopping Minecraft..."
        if [ "$FIFO_OPEN" = 1 ]; then
            printf 'stop\n' >&3
            exec 3>&-
            FIFO_OPEN=0
        fi
        if ! wait_stopped "$SERVER_PID" 45; then
            echo "Minecraft is still saving; sending TERM (never force-killing the world server)."
            kill -TERM "$SERVER_PID" 2>/dev/null
            wait_stopped "$SERVER_PID" 15
        fi
        if alive "$SERVER_PID"; then
            echo "WARNING: Minecraft PID $SERVER_PID is still running. Check $RUN_DIR/server.log before restarting."
        fi
    fi
    stop_child "$OLLAMA_PID" "Ollama"
    if [ "$FIFO_OPEN" = 1 ]; then exec 3>&-; fi
    if [ -n "$SERVER_FIFO" ] && ! alive "$SERVER_PID"; then rm -f "$SERVER_FIFO"; fi
    if [ "$LOCK_OWNED" = 1 ]; then
        for pid in "$SERVER_PID" "$CONTROLLER_PID" "$BOT_PID"; do
            if alive "$pid"; then remaining_pid="$pid"; break; fi
        done
        if [ -n "$remaining_pid" ]; then
            printf '%s\n' "$remaining_pid" > "$LOCK_DIR/pid"
            echo "Launch lock retained until PID $remaining_pid exits; check the logs before restarting."
            if [ "$result" = 0 ]; then result=1; fi
        else
            rm -f "$LOCK_DIR/pid"
            rmdir "$LOCK_DIR" 2>/dev/null
        fi
    fi
    if [ -n "$RUN_DIR" ]; then echo "Logs kept in: $RUN_DIR"; fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

mkdir -p "$ROOT_DIR/data/run"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    OLD_PID=""
    if [ -f "$LOCK_DIR/pid" ]; then read -r OLD_PID < "$LOCK_DIR/pid" || true; fi
    case "$OLD_PID" in
        ""|*[!0-9]*) fail "Launcher lock exists at $LOCK_DIR; inspect it before restarting." ;;
    esac
    if alive "$OLD_PID"; then fail "Launcher PID $OLD_PID is already running."; fi
    # Remove only a verified stale lock's known PID file, never runtime history.
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" || fail "Cannot clear stale launcher lock."
    mkdir "$LOCK_DIR" || fail "Another launcher acquired the lock."
fi
LOCK_OWNED=1
printf '%s\n' "$$" > "$LOCK_DIR/pid"
RUN_DIR="$ROOT_DIR/data/run/$(date +%Y%m%d-%H%M%S)-$$"
mkdir "$RUN_DIR"
echo "Logs: $RUN_DIR"

wait_log() {
    local pid="$1" log="$2" marker="$3" seconds="$4" label="$5" elapsed=0
    while true; do
        if ! alive "$pid"; then
            tail -n 30 "$log" >&2
            fail "$label exited before it was ready. See $log"
        fi
        if grep -Fq "$marker" "$log"; then return 0; fi
        if [ "$elapsed" -ge "$seconds" ]; then
            tail -n 30 "$log" >&2
            fail "Timed out waiting for $label. See $log"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

if [ "$NEEDS_OLLAMA" = 1 ]; then
    if port_open 11434; then
        echo "Reusing your existing local Ollama/Jarvis service (it will not be stopped)."
    else
        echo "Starting local Ollama..."
        OLLAMA_HOST=127.0.0.1:11434 ollama serve >"$RUN_DIR/ollama.log" 2>&1 < /dev/null 3>&- &
        OLLAMA_PID=$!
        elapsed=0
        until port_open 11434; do
            alive "$OLLAMA_PID" || fail "Ollama exited. See $RUN_DIR/ollama.log"
            [ "$elapsed" -lt 30 ] || fail "Ollama did not become ready. See $RUN_DIR/ollama.log"
            sleep 1
            elapsed=$((elapsed + 1))
        done
    fi
    # Only inspect local model tags: no inference, downloads, or Nebius calls.
    "$PYTHON_BIN" - <<'PY' || fail "Local models are not ready."
import json
import os
import sys
import urllib.request
from urllib.parse import urlparse
from agent.config import config

def is_local(url):
    parsed = urlparse(url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434
required = []
if is_local(config.nebius_base_url):
    required.append(config.nebius_model)
if is_local(os.getenv("JARVIS_ENDPOINT", "http://localhost:11434/api/chat")):
    required.append(os.getenv("JARVIS_MODEL", "gemma4:26b"))
if config.memory_enabled and config.graphiti_enabled and is_local(config.memory_base_url):
    required.extend([config.memory_model, config.memory_embedding_model])
try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
        available = {model["name"] for model in json.load(response)["models"]}
    def normalize(name):
        return name if ":" in name else name + ":latest"
    missing = sorted({name for name in required if normalize(name) not in {normalize(n) for n in available}})
    if missing:
        raise RuntimeError("Missing local models: " + ", ".join(missing) + ". Install each with: ollama pull MODEL")
    print("Configured local models are installed.")
except Exception as exc:
    print(f"Ollama check failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
fi

if [ -f "$ROOT_DIR/server-plugins/jarvis-debug/build.sh" ]; then
    echo "Building the local read-only inventory debugger..."
    MINECRAFT_JAVA="$JAVA_BIN" bash "$ROOT_DIR/server-plugins/jarvis-debug/build.sh"
fi

echo "1/3 Starting Minecraft on 127.0.0.1:$MC_PORT..."
SERVER_FIFO="$RUN_DIR/server.stdin"
mkfifo "$SERVER_FIFO"
(
    cd "$ROOT_DIR/server"
    exec "$JAVA_BIN" -Djarvis.bot.username="$BOT_USERNAME" -Djarvis.training.mode="${MC_TRAINING_MODE:-true}" -Xms2G -Xmx2G -jar server.jar --nogui --host 127.0.0.1 --port "$MC_PORT"
) <"$SERVER_FIFO" >"$RUN_DIR/server.log" 2>&1 3>&- &
SERVER_PID=$!
exec 3>"$SERVER_FIFO"
FIFO_OPEN=1
wait_log "$SERVER_PID" "$RUN_DIR/server.log" "Done (" 120 "Minecraft server"
port_open "$MC_PORT" || fail "Minecraft logged ready but port $MC_PORT is not reachable."

echo "2/3 Starting Python controller with persistent world memory..."
"$PYTHON_BIN" -u "$ROOT_DIR/main.py" --chat-only >"$RUN_DIR/controller.log" 2>&1 < /dev/null 3>&- &
CONTROLLER_PID=$!
wait_log "$CONTROLLER_PID" "$RUN_DIR/controller.log" "WebSocket bridge server running and awaiting bot connection." 30 "Python controller"

echo "3/3 Joining the Mineflayer agent..."
node "$ROOT_DIR/bot/index.js" >"$RUN_DIR/bot.log" 2>&1 < /dev/null 3>&- &
BOT_PID=$!
wait_log "$BOT_PID" "$RUN_DIR/bot.log" "Bot successfully spawned in world" 60 "Mineflayer bot"
wait_log "$BOT_PID" "$RUN_DIR/bot.log" "Connected to Python Orchestrator successfully." 30 "Bot/controller connection"
wait_log "$CONTROLLER_PID" "$RUN_DIR/controller.log" "Agent is ready for objectives." 15 "Chat controller"

echo
echo "READY — join Minecraft Java at 127.0.0.1:$MC_PORT"
echo "Agent: $BOT_USERNAME. In game chat: mine 3 oak logs and drop them to me"
echo "Memory recall: memory"
echo "Inventory: chat 'inventory', /jarvisinventory, or right-click $BOT_USERNAME (read-only)."
if [ "${MC_TRAINING_MODE:-true}" = true ]; then echo "Development mode: Peaceful difficulty + full food, still Survival crafting/mining."; fi
echo "Keep this terminal open. Ctrl+C stops the agent and saves/stops the server."
echo
while true; do
    for entry in "$SERVER_PID:server" "$CONTROLLER_PID:controller" "$BOT_PID:bot"; do
        pid="${entry%%:*}"
        name="${entry#*:}"
        if ! alive "$pid"; then
            tail -n 30 "$RUN_DIR/$name.log" >&2
            fail "$name exited unexpectedly. See $RUN_DIR/$name.log"
        fi
    done
    if [ -n "$OLLAMA_PID" ] && ! alive "$OLLAMA_PID"; then
        fail "The local Ollama service exited. See $RUN_DIR/ollama.log"
    fi
    sleep 1
done
