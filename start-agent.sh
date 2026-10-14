#!/usr/bin/env bash
# Join an existing Minecraft server with only a controller and a bot.
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
LAUNCH_ARGS=(--agent-only)

usage() {
    echo "Usage: ./start-agent.sh [--host LAN_IP] [--name AGENT_NAME] [--port 25565] [--bridge-port 8765] [--check]"
    echo "Joins an existing server. Starts only this agent's Python controller and Mineflayer bot."
    echo "Unspecified connection/model settings come from .env. Start the configured model service first."
    echo "Give every bot a unique name; use a different bridge port for each agent on the same computer."
    echo "Example: ./start-agent.sh --host 192.168.50.63 --name AI_Agent_2"
    echo "Ctrl+C stops this agent; the shared Minecraft server keeps running."
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host|--name|--port|--bridge-port)
            [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != --* ]] || {
                echo "Missing value for $1" >&2; exit 2;
            }
            case "$1" in
                --host) export MC_HOST="$2" ;;
                --name) export MC_USERNAME="$2" ;;
                --port) export MC_PORT="$2" ;;
                --bridge-port) export WS_PORT="$2" ;;
            esac
            shift 2 ;;
        --check) LAUNCH_ARGS+=(--check); shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
exec bash "$ROOT_DIR/start.sh" "${LAUNCH_ARGS[@]}"
