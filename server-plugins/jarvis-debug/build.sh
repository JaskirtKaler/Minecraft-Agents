#!/usr/bin/env bash
# Offline build against this server's cached Purpur API. No Maven/downloads.
set -euo pipefail
PLUGIN_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT_DIR="$(CDPATH= cd -- "$PLUGIN_DIR/../.." && pwd -P)"
JAVA_BIN="${MINECRAFT_JAVA:-}"
MODE="${1:-install}"
case "$MODE" in install|--test) ;; *) echo "Usage: bash server-plugins/jarvis-debug/build.sh [--test]" >&2; exit 2 ;; esac
if [ -z "$JAVA_BIN" ]; then
    JAVA_RUNTIME_DIR="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
    if [ -n "$JAVA_RUNTIME_DIR" ]; then JAVA_BIN="$JAVA_RUNTIME_DIR/bin/java";
    else JAVA_BIN="$(command -v java || true)"; fi
fi
[ -x "$JAVA_BIN" ] || { echo "Java 21 not found. Set MINECRAFT_JAVA." >&2; exit 1; }
JDK_BIN="$(dirname -- "$JAVA_BIN")"
[ -x "$JDK_BIN/javac" ] && [ -x "$JDK_BIN/jar" ] || { echo "Use a full Java JDK (javac and jar are required)." >&2; exit 1; }
API_JAR="$ROOT_DIR/server/libraries/org/purpurmc/purpur/purpur-api/1.20.1-R0.1-SNAPSHOT/purpur-api-1.20.1-R0.1-SNAPSHOT.jar"
[ -f "$API_JAR" ] || { echo "Missing cached Purpur 1.20.1 API. Run the server's initial setup first." >&2; exit 1; }
CLASSPATH="$API_JAR"
while IFS= read -r dependency; do CLASSPATH="$CLASSPATH:$dependency"; done < <(find "$ROOT_DIR/server/libraries" -type f -name '*.jar')
BUILD_DIR="$PLUGIN_DIR/build"
mkdir -p "$BUILD_DIR/classes"
SOURCES=(
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/debug/InventoryLayout.java"
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/debug/ReadOnlyInventoryListener.java"
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/debug/TrainingModeListener.java"
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/debug/TrainingCommandPermissions.java"
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/debug/JarvisDebugPlugin.java"
)
"$JDK_BIN/javac" --release 17 -proc:none -encoding UTF-8 -classpath "$CLASSPATH" -d "$BUILD_DIR/classes" "${SOURCES[@]}"
if [ "$MODE" = --test ]; then
    mkdir -p "$BUILD_DIR/test-classes"
    "$JDK_BIN/javac" --release 17 -proc:none -encoding UTF-8 -classpath "$BUILD_DIR/classes:$CLASSPATH" \
        -d "$BUILD_DIR/test-classes" "$PLUGIN_DIR/src/test/java/dev/minecraftagents/debug/InventorySafetyTest.java" \
        "$PLUGIN_DIR/src/test/java/dev/minecraftagents/debug/TrainingModeTest.java"
    "$JAVA_BIN" -ea -classpath "$BUILD_DIR/test-classes:$BUILD_DIR/classes:$CLASSPATH" dev.minecraftagents.debug.InventorySafetyTest
    "$JAVA_BIN" -ea -classpath "$BUILD_DIR/test-classes:$BUILD_DIR/classes:$CLASSPATH" dev.minecraftagents.debug.TrainingModeTest
else
    "$JDK_BIN/jar" --create --file "$BUILD_DIR/JarvisDebug.jar" -C "$BUILD_DIR/classes" . -C "$PLUGIN_DIR/src/main/resources" .
    mkdir -p "$ROOT_DIR/server/plugins"
    cp "$BUILD_DIR/JarvisDebug.jar" "$ROOT_DIR/server/plugins/JarvisDebug.jar"
    echo "JarvisDebug built and installed. Restart the Minecraft server to load it."
fi
