"""Launch configuration and readiness checks; never start services or inference."""

import argparse
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
import urllib.request
from urllib.parse import urlparse


def is_local_ollama(url):
    parsed = urlparse(url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434


def prepare_agent_environment(environment, memory_override, learning_override):
    username = environment.get("MC_USERNAME", "AI_Agent")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", username):
        raise RuntimeError("MC_USERNAME must be 1-16 letters, digits, or underscores.")
    if not environment.get("MC_WORLD_ID"):
        raise RuntimeError("Set MC_WORLD_ID in .env to the existing server's world ID before joining.")
    environment["MEMORY_DIR"] = memory_override or f"data/agents/{username.lower()}/memory"
    environment["LEARNING_DIR"] = learning_override or f"data/agents/{username.lower()}/learning"


def lan_addresses(port):
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private and not address.is_loopback and not address.is_link_local:
                addresses.add(f"{address}:{port}")
    except OSError:
        pass  # Address discovery must not prevent the server from starting.
    return " ".join(sorted(addresses))


def settings_from_config(config, agent_only, environment, root=Path(".")):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", config.mc_username):
        raise RuntimeError("MC_USERNAME must be 1-16 letters, digits, or underscores.")
    if not agent_only and config.mc_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This launcher hosts the world here; keep MC_HOST=localhost. To join another host, use ./start-agent.sh --host LAN_IP --name AGENT_NAME.")
    if not config.mc_command_players or any(not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name) for name in config.mc_command_players):
        raise RuntimeError("Set MC_COMMAND_PLAYERS to comma-separated human Minecraft usernames (no wildcards or bot names).")
    bind_host = environment.get("MC_BIND_HOST", "0.0.0.0")
    if not agent_only and bind_host not in {"0.0.0.0", "127.0.0.1"}:
        raise RuntimeError("MC_BIND_HOST must be 0.0.0.0 (home network) or 127.0.0.1 (this computer only).")
    if config.mc_version != "1.20.1":
        raise RuntimeError("Keep MC_VERSION=1.20.1 for this bundled server and bot.")
    if not all(1 <= port <= 65535 for port in (config.mc_port, config.ws_port)):
        raise RuntimeError("MC_PORT and WS_PORT must be valid TCP ports.")
    if config.mc_port == config.ws_port:
        raise RuntimeError("MC_PORT and WS_PORT must be different.")
    if not agent_only:
        eula_path = root / "server/eula.txt"
        properties = {}
        if eula_path.exists():
            for line in eula_path.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    properties[key.strip()] = value.strip()
        if properties.get("eula", "").lower() != "true":
            raise RuntimeError("Accept Minecraft's EULA yourself in server/eula.txt before launching.")
    endpoints = [config.nebius_base_url, environment.get("JARVIS_ENDPOINT", "http://localhost:11434/api/chat")]
    if config.memory_enabled and config.graphiti_enabled:
        endpoints.append(config.memory_base_url)
    return {
        "MC_PORT": str(config.mc_port),
        "MC_HOST": config.mc_host if agent_only else "127.0.0.1",
        "MC_BIND_HOST": bind_host,
        "LAN_ADDRESSES": lan_addresses(config.mc_port) if not agent_only and bind_host == "0.0.0.0" else "",
        "WS_PORT": str(config.ws_port),
        "MC_WORLD_ID": environment.get("MC_WORLD_ID") or f"{config.mc_host}:{config.mc_port}",
        "BOT_USERNAME": config.mc_username,
        "AGENT_KEY": config.mc_username.lower(),
        "COMMAND_PLAYERS": ", ".join(config.mc_command_players),
        "MEMORY_DIR": config.memory_dir,
        "LEARNING_DIR": config.learning_dir,
        "MC_TRAINING_MODE": "true" if config.mc_training_mode else "false",
        "NEEDS_OLLAMA": "1" if any(is_local_ollama(url) for url in endpoints) else "0",
    }


def load_settings():
    if sys.version_info < (3, 12):
        raise RuntimeError("The minecraft-agent environment needs Python 3.12+.")
    agent_only = os.environ["MINECRAFT_AGENT_ONLY"] == "1"
    memory_override, learning_override = os.getenv("MEMORY_DIR"), os.getenv("LEARNING_DIR")
    from dotenv import load_dotenv
    load_dotenv(".env")
    if agent_only:
        prepare_agent_environment(os.environ, memory_override, learning_override)
    from agent.config import config
    importlib.import_module("main")  # Check controller imports without executing it.
    if config.memory_enabled and config.graphiti_enabled:
        for name in ("graphiti_core", "redislite", "falkordb"):
            importlib.import_module(name)
    return settings_from_config(config, agent_only, os.environ)


def required_ollama_models(config, environment):
    required = []
    if is_local_ollama(config.nebius_base_url):
        required.append(config.nebius_model)
    if is_local_ollama(environment.get("JARVIS_ENDPOINT", "http://localhost:11434/api/chat")):
        required.append(environment.get("JARVIS_MODEL", "gemma4:26b"))
    if config.memory_enabled and config.graphiti_enabled and is_local_ollama(config.memory_base_url):
        required.extend([config.memory_model, config.memory_embedding_model])
    return required


def check_models():
    from agent.config import config
    required = required_ollama_models(config, os.environ)
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
        available = {model["name"] for model in json.load(response)["models"]}
    def normalize(name):
        return name if ":" in name else name + ":latest"
    installed = {normalize(name) for name in available}
    missing = sorted({name for name in required if normalize(name) not in installed})
    if missing:
        raise RuntimeError("Missing local models: " + ", ".join(missing) + ". Install each with: ollama pull MODEL")
    print("Configured local models are installed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("settings", "models", "port"))
    parser.add_argument("port", type=int, nargs="?")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    args = parser.parse_args()
    if args.check == "port":
        if args.port is None:
            parser.error("port check requires a TCP port")
        try:
            with socket.create_connection((args.host, args.port), timeout=2):
                return 0
        except OSError:
            return 1
    try:
        if args.check == "settings":
            for key, value in load_settings().items():
                print(f"{key}={shlex.quote(value)}")
        else:
            check_models()
    except Exception as error:
        print(f"Setup check failed: {type(error).__name__}: {error}", file=sys.stderr)
        if args.check == "settings":
            print("For missing Python packages, use the minecraft-agent Python with: -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
