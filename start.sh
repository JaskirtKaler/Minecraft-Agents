#!/usr/bin/env bash
# Local, foreground supervisor. Ctrl+C stops only processes started by this run.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/runtime.sh"
CHECK_ONLY=0
AGENT_ONLY=0

usage() {
    echo "Usage: ./start.sh [--check]"
    echo "Starts local Minecraft, the Python controller, and the Mineflayer bot."
    echo "Minecraft accepts home-network connections; MC_BIND_HOST=127.0.0.1 restricts it to this computer."
    echo "Reuses Ollama/Jarvis if running; otherwise starts a local Ollama service."
    echo "Keep this terminal open. Ctrl+C saves/stops Minecraft and shuts down the agent."
    echo "--check validates setup and port availability without launching anything."
    echo "Optional overrides: MINECRAFT_PYTHON, MINECRAFT_JAVA."
    echo "To join an existing server without Java/server files: ./start-agent.sh --help"
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --agent-only) AGENT_ONLY=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
export MINECRAFT_AGENT_ONLY="$AGENT_ONLY"
PYTHON_BIN="$(minecraft_python)"

fail() { echo "ERROR: $*" >&2; exit 1; }
command -v node >/dev/null 2>&1 || fail "Node.js is not installed or not on PATH."
[ -d "$ROOT_DIR/bot/node_modules" ] || fail "Install bot dependencies first: npm ci --prefix bot"
[ -f "$ROOT_DIR/.env" ] || fail "Create .env from .env.example and configure your local model first."
if [ "$AGENT_ONLY" = 0 ]; then
    [ -s "$ROOT_DIR/server/server.jar" ] || fail "Missing server/server.jar. Complete server setup first."
    [ -s "$ROOT_DIR/server/plugins/ViaVersion.jar" ] || fail "Missing ViaVersion plugin. Complete server setup first."
    [ -s "$ROOT_DIR/server/plugins/ViaBackwards.jar" ] || fail "Missing ViaBackwards plugin. Complete server setup first."

    JAVA_BIN="$(minecraft_java)"
    JAVA_INFO="$("$JAVA_BIN" -version 2>&1)" || fail "Java could not start."
    echo "$JAVA_INFO" | head -n 1
fi

# Only selected, shell-quoted non-secret settings are returned.
SETTINGS="$("$PYTHON_BIN" -m scripts.launch_checks settings)" || fail "Python/configuration preflight failed."
eval "$SETTINGS"
export MC_HOST MC_PORT WS_PORT MC_WORLD_ID MEMORY_DIR LEARNING_DIR
export MC_USERNAME="$BOT_USERNAME" WS_HOST=127.0.0.1 WS_URL="ws://127.0.0.1:$WS_PORT"
export PYTHONUNBUFFERED=1

show_lan_addresses() {
    if [ "$MC_BIND_HOST" = "0.0.0.0" ]; then
        if [ -n "$LAN_ADDRESSES" ]; then
            for join_address in $LAN_ADDRESSES; do
                echo "Home network players/agents: $join_address"
            done
        else
            echo "Home network players/agents: use this computer's LAN IPv4 address with port $MC_PORT."
        fi
    fi
}

port_open() {
    "$PYTHON_BIN" -m scripts.launch_checks port "$1" "${2:-127.0.0.1}" >/dev/null 2>&1
}
if [ "$AGENT_ONLY" = 1 ]; then
    port_open "$MC_PORT" "$MC_HOST" || fail "Cannot reach Minecraft at $MC_HOST:$MC_PORT. Start the host server and check its LAN address."
elif port_open "$MC_PORT"; then
    fail "Minecraft port $MC_PORT is already occupied. Stop the existing server before using this launcher."
fi
if port_open "$WS_PORT"; then
    fail "Controller port $WS_PORT is already occupied. Stop the existing controller before using this launcher."
fi
if [ "$NEEDS_OLLAMA" = 1 ] && ! port_open 11434; then
    if [ "$AGENT_ONLY" = 1 ]; then
        fail "Start the configured local Ollama service first (ollama serve). The agent-only launcher starts only the controller and bot."
    fi
    command -v ollama >/dev/null 2>&1 || fail "Install/start Ollama for the configured local models."
fi
if [ "$CHECK_ONLY" = 1 ]; then
    if [ "$AGENT_ONLY" = 1 ]; then
        echo "Setup checks passed. Agent $BOT_USERNAME will join $MC_HOST:$MC_PORT via local bridge port $WS_PORT."
        echo "Agent memory: $MEMORY_DIR; learning: $LEARNING_DIR"
    else
        echo "Setup checks passed. Join address after launch: 127.0.0.1:$MC_PORT"
        show_lan_addresses
    fi
    exit 0
fi

SERVER_PID="" CONTROLLER_PID="" BOT_PID="" OLLAMA_PID=""
RUN_DIR="" SERVER_FIFO="" FIFO_OPEN=0 LOCK_OWNED=0
LOCK_DIR="$ROOT_DIR/data/run/start.lock"
if [ "$AGENT_ONLY" = 1 ]; then LOCK_DIR="$ROOT_DIR/data/run/agent-$AGENT_KEY.lock"; fi
if [ -n "${MINECRAFT_LAUNCH_LOCK_DIR:-}" ]; then LOCK_DIR="$MINECRAFT_LAUNCH_LOCK_DIR"; fi
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
    if [ -n "${MINECRAFT_READY_FILE:-}" ]; then rm -f "$MINECRAFT_READY_FILE"; fi
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
RUN_DIR="$ROOT_DIR/data/run/$(date +%Y%m%d-%H%M%S)-$AGENT_KEY-$$"
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
    # Inspect installed tags only: no inference or downloads.
    "$PYTHON_BIN" -m scripts.launch_checks models || fail "Local models are not ready."
fi

if [ "$AGENT_ONLY" = 0 ]; then
    if [ -f "$ROOT_DIR/server-plugins/jarvis-debug/build.sh" ]; then
        echo "Building the local read-only inventory debugger..."
        MINECRAFT_JAVA="$JAVA_BIN" bash "$ROOT_DIR/server-plugins/jarvis-debug/build.sh"
    fi

    echo "1/3 Starting Minecraft on $MC_BIND_HOST:$MC_PORT..."
    SERVER_FIFO="$RUN_DIR/server.stdin"
    mkfifo "$SERVER_FIFO"
    (
        cd "$ROOT_DIR/server"
        exec "$JAVA_BIN" -Djarvis.bot.username="$BOT_USERNAME" -Djarvis.training.mode="${MC_TRAINING_MODE:-true}" -Xms2G -Xmx2G -jar server.jar --nogui --host "$MC_BIND_HOST" --port "$MC_PORT"
    ) <"$SERVER_FIFO" >"$RUN_DIR/server.log" 2>&1 3>&- &
    SERVER_PID=$!
    exec 3>"$SERVER_FIFO"
    FIFO_OPEN=1
    wait_log "$SERVER_PID" "$RUN_DIR/server.log" "Done (" 120 "Minecraft server"
    port_open "$MC_PORT" || fail "Minecraft logged ready but port $MC_PORT is not reachable."
fi

if [ "$AGENT_ONLY" = 1 ]; then STEP=1 TOTAL=2; else STEP=2 TOTAL=3; fi
echo "$STEP/$TOTAL Starting Python controller with persistent world memory..."
"$PYTHON_BIN" -u "$ROOT_DIR/main.py" --chat-only >"$RUN_DIR/controller.log" 2>&1 < /dev/null 3>&- &
CONTROLLER_PID=$!
wait_log "$CONTROLLER_PID" "$RUN_DIR/controller.log" "WebSocket bridge server running and awaiting bot connection." 30 "Python controller"

echo "$((STEP + 1))/$TOTAL Joining the Mineflayer agent $BOT_USERNAME at $MC_HOST:$MC_PORT..."
node "$ROOT_DIR/bot/index.js" >"$RUN_DIR/bot.log" 2>&1 < /dev/null 3>&- &
BOT_PID=$!
wait_log "$BOT_PID" "$RUN_DIR/bot.log" "Bot successfully spawned in world" 60 "Mineflayer bot"
wait_log "$BOT_PID" "$RUN_DIR/bot.log" "Connected to Python Orchestrator successfully." 30 "Bot/controller connection"
wait_log "$CONTROLLER_PID" "$RUN_DIR/controller.log" "Agent is ready for objectives." 15 "Chat controller"
if [ -n "${MINECRAFT_READY_FILE:-}" ]; then
    printf '%s\n' "$$" "$CONTROLLER_PID" "$BOT_PID" > "$MINECRAFT_READY_FILE"
fi

echo
if [ "$AGENT_ONLY" = 1 ]; then
    echo "READY — agent $BOT_USERNAME joined $MC_HOST:$MC_PORT"
    echo "Agent memory: $MEMORY_DIR; learning: $LEARNING_DIR"
else
    echo "READY — join Minecraft Java at 127.0.0.1:$MC_PORT"
    show_lan_addresses
fi
echo "Agent: $BOT_USERNAME. In game chat: @$BOT_USERNAME mine 3 oak logs and drop them to me"
echo "Commands: @$BOT_USERNAME status / stop / inventory / memory"
echo "Command players: $COMMAND_PLAYERS (MC_COMMAND_PLAYERS). Plain chat and other senders are ignored."
if [ "$AGENT_ONLY" = 0 ] && [ "${MC_TRAINING_MODE:-true}" = true ]; then
    echo "Development mode: Peaceful difficulty + full food, still Survival crafting/mining."
    echo "Configured human debug players can use /gamemode creative, /gamemode survival, /time set day and /weather clear."
fi
if [ "$AGENT_ONLY" = 1 ]; then
    echo "Keep this terminal open. Ctrl+C stops only this agent; the Minecraft server keeps running."
else
    echo "Keep this terminal open. Ctrl+C stops the agent and saves/stops the server."
fi
echo
while true; do
    for entry in "$SERVER_PID:server" "$CONTROLLER_PID:controller" "$BOT_PID:bot"; do
        pid="${entry%%:*}"
        name="${entry#*:}"
        if [ -z "$pid" ]; then continue; fi
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
