#!/bin/bash
cd "$(dirname "$0")"

# Locate Java
JAVA_CMD="java"
if ! command -v java &> /dev/null; then
    if [ -x "/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home/bin/java" ]; then
        JAVA_CMD="/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home/bin/java"
    elif [ -x "$JAVA_HOME/bin/java" ]; then
        JAVA_CMD="$JAVA_HOME/bin/java"
    fi
fi

JAR_NAME="server.jar"
PRIMARY_URL="https://api.purpurmc.org/v2/purpur/1.20.1/latest/download"
FALLBACK_URL="https://piston-data.mojang.com/v1/objects/84194a2f286ef7c14ed7ce0090dba59902951553/server.jar"

if [ ! -f "$JAR_NAME" ] || [ $(wc -c < "$JAR_NAME") -lt 1000000 ]; then
    echo "⬇️ Downloading Minecraft 1.20.1 server jar (~45MB)..."
    rm -f "$JAR_NAME"
    curl -L -f -o "$JAR_NAME" "$PRIMARY_URL"
    if [ $? -ne 0 ] || [ $(wc -c < "$JAR_NAME") -lt 1000000 ]; then
        echo "⚠️ Primary download failed, trying official Mojang server fallback..."
        curl -L -f -o "$JAR_NAME" "$FALLBACK_URL"
    fi
    if [ $? -ne 0 ] || [ $(wc -c < "$JAR_NAME") -lt 1000000 ]; then
        echo "❌ Failed to download server.jar. Please check your network connection."
        exit 1
    fi
    echo "✅ Server download complete."
fi

# Ensure ViaVersion & ViaBackwards plugins exist for multi-version support
mkdir -p plugins
if [ ! -f "plugins/ViaVersion.jar" ]; then
    echo "⬇️ Installing ViaVersion (Multi-version client compatibility plugin)..."
    curl -L -f -o plugins/ViaVersion.jar "https://github.com/ViaVersion/ViaVersion/releases/download/5.12.0/ViaVersion-5.12.0.jar"
fi
if [ ! -f "plugins/ViaBackwards.jar" ]; then
    echo "⬇️ Installing ViaBackwards plugin..."
    curl -L -f -o plugins/ViaBackwards.jar "https://github.com/ViaVersion/ViaBackwards/releases/download/5.12.0/ViaBackwards-5.12.0.jar"
fi

# Ensure eula.txt is accepted
echo "eula=true" > eula.txt

echo "🚀 Starting Minecraft Server with ViaVersion (online-mode=false)..."
"$JAVA_CMD" -Xms2G -Xmx2G -jar "$JAR_NAME" nogui
