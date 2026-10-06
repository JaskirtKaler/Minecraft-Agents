#!/usr/bin/env bash
# Build only. Deliberately does NOT install into server/plugins.
set -euo pipefail
PLUGIN_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT_DIR="$(CDPATH= cd -- "$PLUGIN_DIR/../.." && pwd -P)"
JAVA_BIN="${MINECRAFT_JAVA:-}"
if [ -z "$JAVA_BIN" ]; then
    JAVA_RUNTIME_DIR="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
    if [ -n "$JAVA_RUNTIME_DIR" ]; then JAVA_BIN="$JAVA_RUNTIME_DIR/bin/java";
    else JAVA_BIN="$(command -v java || true)"; fi
fi
JDK_BIN="$(dirname -- "$JAVA_BIN")"
[ -x "$JDK_BIN/javac" ] && [ -x "$JDK_BIN/jar" ] || { echo "Practice oracle needs a Java 21 JDK." >&2; exit 1; }
API_JAR="$ROOT_DIR/server/libraries/org/purpurmc/purpur/purpur-api/1.20.1-R0.1-SNAPSHOT/purpur-api-1.20.1-R0.1-SNAPSHOT.jar"
[ -f "$API_JAR" ] || { echo "Missing cached Purpur API; initialize the local server first." >&2; exit 1; }
CLASSPATH="$API_JAR"
while IFS= read -r dependency; do CLASSPATH="$CLASSPATH:$dependency"; done < <(find "$ROOT_DIR/server/libraries" -type f -name '*.jar')
mkdir -p "$PLUGIN_DIR/build/classes"
"$JDK_BIN/javac" --release 17 -proc:none -encoding UTF-8 -classpath "$CLASSPATH" -d "$PLUGIN_DIR/build/classes" \
    "$PLUGIN_DIR/src/main/java/dev/minecraftagents/practice/PracticeOracle.java"
"$JDK_BIN/jar" --create --file "$PLUGIN_DIR/build/PracticeOracle.jar" -C "$PLUGIN_DIR/build/classes" . -C "$PLUGIN_DIR/src/main/resources" .
echo "PracticeOracle built (not installed in the normal server)."
