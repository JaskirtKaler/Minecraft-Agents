#!/usr/bin/env bash
# Shared interpreter discovery. Sourcing this file starts no services.

minecraft_python() {
    local selected=""
    if [ -n "${MINECRAFT_PYTHON:-}" ]; then
        selected="$MINECRAFT_PYTHON"
    # Conda often auto-activates base; prefer the project runtime in that case.
    elif [ "${CONDA_DEFAULT_ENV:-}" != base ] && [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
        selected="$CONDA_PREFIX/bin/python"
    elif [ -x "$HOME/miniforge3/envs/minecraft-agent/bin/python" ]; then
        selected="$HOME/miniforge3/envs/minecraft-agent/bin/python"
    else
        selected="$(command -v python3 || command -v python || true)"
    fi
    selected="$(command -v "$selected" 2>/dev/null || true)"
    [ -n "$selected" ] && [ -x "$selected" ] || {
        echo "Python not found. Activate minecraft-agent or set MINECRAFT_PYTHON to its interpreter." >&2
        return 1
    }
    printf '%s\n' "$selected"
}

minecraft_java() {
    local selected="${MINECRAFT_JAVA:-}" java_home_found=""
    if [ -z "$selected" ] && [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
        selected="$JAVA_HOME/bin/java"
    fi
    if [ -z "$selected" ] && [ -x /usr/libexec/java_home ]; then
        java_home_found="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
        if [ -n "$java_home_found" ]; then selected="$java_home_found/bin/java"; fi
    fi
    if [ -z "$selected" ]; then selected="$(command -v java || true)"; fi
    selected="$(command -v "$selected" 2>/dev/null || true)"
    [ -n "$selected" ] && [ -x "$selected" ] || {
        echo "Java not found. Install a Java 21 JDK or set MINECRAFT_JAVA to its java executable." >&2
        return 1
    }
    printf '%s\n' "$selected"
}
