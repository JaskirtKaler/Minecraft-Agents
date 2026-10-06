"""Headless baseline: real Purpur + real Mineflayer + the normal chat router.

No model inference, no Minecraft client, no access to the normal world/memory.
Every generated world and log is retained under data/practice for inspection.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
from types import SimpleNamespace
import uuid

from agent.bridge import MineflayerBridge
from agent.config import Config
from agent.dialogue import classify_message
from agent.world_memory import WorldMemory
from main import execute_chat_objective
from practice.scenarios import SCENARIOS, OPTIONAL_SCENARIOS, evaluate

ROOT = Path(__file__).resolve().parents[1]
BOT_NAME = "PracticeAgent"
REQUESTER = "PracticeDriver"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def java_binary():
    if os.getenv("MINECRAFT_JAVA"):
        return os.environ["MINECRAFT_JAVA"]
    if Path("/usr/libexec/java_home").exists():
        found = subprocess.check_output(["/usr/libexec/java_home", "-v", "21"], text=True).strip()
        return str(Path(found) / "bin/java")
    found = shutil.which("java")
    if not found:
        raise RuntimeError("Java 21 JDK is required")
    return found


class ServerConsole:
    def __init__(self, process, log):
        self.process, self.log = process, log
        self.pending = {}
        self.ready = asyncio.Event()
        self.oracle_ready = asyncio.Event()
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        while line := await self.process.stdout.readline():
            text = line.decode(errors="replace")
            self.log.write(text)
            self.log.flush()
            if "Done (" in text:
                self.ready.set()
            if "Practice oracle ready" in text:
                self.oracle_ready.set()
            if "ORACLE " in text:
                payload = text.split("ORACLE ", 1)[1].strip()
                token, _, data = payload.partition(" ")
                future = self.pending.get(token)
                if future and not future.done():
                    try:
                        future.set_result(json.loads(data))
                    except ValueError as error:
                        future.set_exception(error)
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Practice server exited"))

    async def request(self, action, scenario=None):
        token = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[token] = future
        command = f"practiceoracle {action} {token} {BOT_NAME}" + (f" {scenario}" if scenario else "")
        self.process.stdin.write((command + "\n").encode())
        await self.process.stdin.drain()
        try:
            reply = await asyncio.wait_for(future, 10)
            if not reply.get("ok"):
                raise RuntimeError(reply.get("error", "Oracle request failed"))
            return reply["state"]
        finally:
            self.pending.pop(token, None)

    async def stop(self):
        if self.process.returncode is None:
            self.process.stdin.write(b"stop\n")
            try:
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), 30)
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
                if self.process.returncode is None:
                    self.process.terminate()
                    try:
                        await asyncio.wait_for(self.process.wait(), 10)
                    except asyncio.TimeoutError:
                        self.process.kill()
                        await self.process.wait()
        await self.reader


def prepare_server(directory, nonce, port):
    """Copies runtime artifacts only. Never copies or symlinks normal worlds."""
    directory.mkdir()
    shutil.copy2(ROOT / "server/server.jar", directory / "server.jar")
    for name in ("cache", "libraries", "versions"):
        shutil.copytree(ROOT / "server" / name, directory / name)
    (directory / "plugins").mkdir()
    shutil.copy2(ROOT / "server-plugins/practice-oracle/build/PracticeOracle.jar", directory / "plugins/PracticeOracle.jar")
    (directory / "practice.guard").write_text(nonce)
    (directory / "eula.txt").write_text("eula=true\n")
    settings = {
        "server-ip": "127.0.0.1", "server-port": port, "online-mode": "false", "enable-rcon": "false",
        "level-name": "practice-world", "level-type": "minecraft:flat", "allow-nether": "false",
        "spawn-protection": 0, "difficulty": "peaceful", "gamemode": "survival", "max-players": 2,
        "view-distance": 3, "simulation-distance": 3, "spawn-animals": "false", "spawn-monsters": "false",
        "generate-structures": "false", "sync-chunk-writes": "true", "enable-status": "false",
        "generator-settings": json.dumps({"layers": [{"block": "minecraft:bedrock", "height": 1}], "biome": "minecraft:plains", "structures": {}}),
    }
    (directory / "server.properties").write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    (directory / "bukkit.yml").write_text("settings:\n  allow-end: false\n")


async def run(args, selected):
    logging.getLogger("BridgeServer").setLevel(logging.WARNING)
    loop = asyncio.get_running_loop()
    owner = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, owner.cancel)
    java = java_binary()
    subprocess.run(["bash", str(ROOT / "server-plugins/practice-oracle/build.sh")], check=True,
                   env={**os.environ, "MINECRAFT_JAVA": java})
    nonce = str(uuid.uuid4())
    directory = ROOT / "data/practice" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + nonce[:8])
    directory.mkdir(parents=True)
    port = free_port()
    prepare_server(directory / "server", nonce, port)
    print(f"Practice artifacts: {directory}", flush=True)
    print("Real server + bot, no Minecraft client or model inference; normal world/memory untouched.", flush=True)
    settings = Config(memory_enabled=True, graphiti_enabled=False, memory_dir=str(directory / "memory"))
    memory = WorldMemory(settings)
    bridge = MineflayerBridge(host="127.0.0.1", port=0)
    bridge.state_callbacks.append(memory.observe)
    bridge.event_callbacks.append(memory.record_event)
    ws_server = await bridge.start()
    ws_port = ws_server.sockets[0].getsockname()[1]
    agent = SimpleNamespace(memory=memory, dialogue={"pending": {}, "active": None, "phase": "", "last": {}})
    bridge.event_callbacks.append(lambda event: agent.dialogue.update(phase=event.get("data", {}).get("phase", ""))
                                  if event.get("event") == "task_progress" else None)
    console = None
    bot = None
    report = {"mode": "real-server-baseline", "inference_calls": 0, "world_id": f"practice:{nonce}",
              "requested_cases": [case.name for case in selected],
              "requested_repetitions": args.repeat, "completed": False, "cases": []}
    streams = []
    try:
        server_log = (directory / "server.log").open("w")
        streams.append(server_log)
        server = await asyncio.create_subprocess_exec(java, "-Xms512M", "-Xmx1G", f"-Dminecraftagents.practice={nonce}",
            "-jar", "server.jar", "--nogui", cwd=directory / "server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        console = ServerConsole(server, server_log)
        await asyncio.wait_for(console.ready.wait(), 120)
        await asyncio.wait_for(console.oracle_ready.wait(), 10)
        bot_log = (directory / "bot.log").open("w")
        streams.append(bot_log)
        env = {**os.environ, "MC_HOST": "127.0.0.1", "MC_PORT": str(port), "MC_USERNAME": BOT_NAME,
               "MC_VERSION": "1.20.1", "MC_WORLD_ID": f"practice:{nonce}", "WS_URL": f"ws://127.0.0.1:{ws_port}",
               "ENABLE_VIEWER": "false", "ALLOW_EXPERIMENTAL_CODE": "false"}
        bot = await asyncio.create_subprocess_exec("node", str(ROOT / "bot/index.js"), cwd=ROOT, env=env,
                                                   stdout=bot_log, stderr=asyncio.subprocess.STDOUT)
        for _ in range(100):
            if bot.returncode is not None:
                raise RuntimeError("Practice bot exited; see bot.log")
            if bridge.active_client and (await bridge.get_state()).get("ready"):
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Practice bot did not become ready")
        lock = asyncio.Lock()
        for repetition in range(args.repeat):
            for scenario in selected:
                await console.request("setup", scenario.name)
                await asyncio.sleep(0.5)  # Allow teleport/inventory/block packets to reach the bot.
                before = await console.request("snapshot")
                agent.dialogue["last"].clear()
                route = classify_message(scenario.objective, REQUESTER)
                started = asyncio.get_running_loop().time()
                print(f"RUN {scenario.name} ({repetition + 1}/{args.repeat})", flush=True)
                try:
                    await asyncio.wait_for(execute_chat_objective(agent, bridge, scenario.objective, REQUESTER, lock), args.timeout)
                except asyncio.TimeoutError:
                    await bridge.cancel_task()
                    report["cases"].append({"name": scenario.name, "passed": False, "failures": ["Case deadline exceeded; run aborted to prevent overlapping resets"]})
                    raise RuntimeError(f"{scenario.name} exceeded {args.timeout}s; see logs")
                await asyncio.sleep(0.3)
                after = await console.request("snapshot")
                result = agent.dialogue["last"].get(REQUESTER, {})
                failures = evaluate(scenario, before, after, result, route)
                entry = {"name": scenario.name, "repeat": repetition + 1, "objective": scenario.objective,
                         "route": route, "result": result, "before": before, "after": after,
                         "duration_seconds": round(asyncio.get_running_loop().time() - started, 3),
                         "passed": not failures, "failures": failures}
                report["cases"].append(entry)
                print(("PASS " if not failures else "FAIL ") + scenario.name + (": " + "; ".join(failures) if failures else ""), flush=True)
                (directory / "report.json").write_text(json.dumps(report, indent=2))
        report["completed"] = True
    finally:
        if bot and bot.returncode is None:
            bot.terminate()
            try:
                await asyncio.wait_for(bot.wait(), 10)
            except asyncio.TimeoutError:
                bot.kill()
                await bot.wait()
        if console:
            await console.stop()
        ws_server.close()
        await ws_server.wait_closed()
        await memory.close()
        for stream in streams:
            stream.close()
        report["passed"] = sum(case["passed"] for case in report["cases"])
        report["total"] = len(report["cases"])
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        loop.remove_signal_handler(signal.SIGTERM)
    print(f"RESULT {report['passed']}/{report['total']} passed. Report: {directory / 'report.json'}", flush=True)
    return 0 if report["passed"] == report["total"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List scenarios without launching or building anything")
    parser.add_argument("--cases", help="Comma-separated cases; default: all basic cases. Optional: staircase")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90, help="Seconds per case; no automatic transfer retries")
    args = parser.parse_args()
    catalog = {case.name: case for case in SCENARIOS + OPTIONAL_SCENARIOS}
    if args.list:
        for case in catalog.values():
            print(f"{case.name}: {case.objective}")
        return 0
    if not 1 <= args.repeat <= 10 or not 5 <= args.timeout <= 180:
        parser.error("repeat must be 1–10; timeout must be 5–180 seconds")
    names = args.cases.split(",") if args.cases else [case.name for case in SCENARIOS]
    if any(name not in catalog for name in names):
        parser.error("Unknown case; use --list")
    try:
        return asyncio.run(run(args, [catalog[name] for name in names]))
    except KeyboardInterrupt:
        return 130
    except asyncio.CancelledError:
        print("Practice run cancelled; owned server/bot stopped and artifacts retained.", flush=True)
        return 143
    except Exception as error:
        print(f"Practice run stopped: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
