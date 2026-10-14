#!/usr/bin/env bash
# Server-only bootstrap using the bundled Purpur runtime.
set -euo pipefail
SERVER_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
source "$SERVER_DIR/../scripts/runtime.sh"
cd "$SERVER_DIR"
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
    echo "Usage: ./server/start.sh [Minecraft server arguments]"
    echo "Starts only the bundled Minecraft server. Use root ./start.sh for server + agent."
    exit 0
fi
[ -s server.jar ] || { echo "Missing bundled server/server.jar. Restore it from the repository." >&2; exit 1; }
for plugin in ViaVersion ViaBackwards; do
    [ -s "plugins/$plugin.jar" ] || { echo "Missing bundled $plugin.jar. Restore it from the repository." >&2; exit 1; }
done
grep -Eiq '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true[[:space:]]*$' eula.txt 2>/dev/null || {
    echo "Review Minecraft's EULA and accept it yourself in server/eula.txt before launching." >&2
    exit 1
}
JAVA_CMD="$(minecraft_java)"
echo "Starting Purpur 1.20.1 with the existing server configuration..."
exec "$JAVA_CMD" -Xms2G -Xmx2G -jar server.jar --nogui "$@"
