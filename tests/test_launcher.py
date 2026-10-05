"""Offline process-level coverage for the local ``start.sh`` supervisor.

The fixture intentionally never starts a real Minecraft server, controller,
bot, Ollama instance, or TCP listener.  Small executable stand-ins model the
launcher contract instead: Python handles its two heredoc call shapes and
port probes; Java consumes the server FIFO; and Node emits the bot markers.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "start.sh"


class LauncherIntegrationTests(unittest.TestCase):
    """Exercise the real Bash launcher against disposable fake executables."""

    mc_port = "25566"
    ws_port = "8765"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="minecraft launcher fixture ")
        # Both the temporary directory and repository path include spaces, so
        # every process invocation also exercises the launcher's quoting.
        self.root = Path(self.tempdir.name) / "repo with spaces"
        self.root.mkdir()
        self.state = self.root / "fake state"
        self.state.mkdir()
        (self.state / "events.log").touch()
        self.fake_bin = self.root / "fake tools"
        self.fake_bin.mkdir()
        self._processes: list[tuple[subprocess.Popen[str], object]] = []

        shutil.copy2(LAUNCHER, self.root / "start.sh")
        (self.root / "start.sh").chmod((self.root / "start.sh").stat().st_mode | stat.S_IXUSR)
        (self.root / ".env").write_text("LOCAL_TEST_ONLY=1\n", encoding="utf-8")
        (self.root / "main.py").write_text("# not executed by fake python\n", encoding="utf-8")
        (self.root / "bot" / "node_modules").mkdir(parents=True)
        (self.root / "bot" / "index.js").write_text("// not executed by fake node\n", encoding="utf-8")
        (self.root / "server" / "plugins").mkdir(parents=True)
        (self.root / "server" / "server.jar").write_bytes(b"fake jar")
        (self.root / "server" / "plugins" / "ViaVersion.jar").write_bytes(b"fake plugin")
        (self.root / "server" / "plugins" / "ViaBackwards.jar").write_bytes(b"fake plugin")
        (self.root / "server" / "eula.txt").write_text("eula=true\n", encoding="utf-8")

        self.python = self.fake_bin / "python"
        self.java = self.fake_bin / "java"
        self.node = self.fake_bin / "node"
        self._write_fake_tools()

        self.env = os.environ.copy()
        self.env.update(
            {
                "MINECRAFT_PYTHON": str(self.python),
                "MINECRAFT_JAVA": str(self.java),
                "PATH": f"{self.fake_bin}{os.pathsep}{self.env.get('PATH', '')}",
                "FAKE_STATE_DIR": str(self.state),
                "FAKE_MC_PORT": self.mc_port,
                "FAKE_WS_PORT": self.ws_port,
            }
        )

    def tearDown(self):
        for process, stream in self._processes:
            if process.poll() is None:
                # Normal assertions signal only the supervisor, which lets its
                # cleanup run.  This is a last-resort guard for failed tests.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            stream.close()
        self.tempdir.cleanup()

    def _write_executable(self, path: Path, source: str) -> None:
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_fake_tools(self) -> None:
        self._write_executable(
            self.python,
            r'''
            #!/bin/sh
            set -eu

            state=${FAKE_STATE_DIR:?}
            event() { printf '%s\n' "$1" >> "$state/events.log"; }

            if [ "${1:-}" = "-" ]; then
                payload=$(cat)
                if [ "$#" -gt 1 ]; then
                    case "$2" in
                        "$FAKE_MC_PORT")
                            [ "${FAKE_OCCUPIED:-}" = "mc" ] && exit 0
                            [ -f "$state/mc.ready" ] && exit 0
                            exit 1
                            ;;
                        "$FAKE_WS_PORT")
                            [ "${FAKE_OCCUPIED:-}" = "ws" ] && exit 0
                            [ -f "$state/ws.ready" ] && exit 0
                            exit 1
                            ;;
                        *) exit 1 ;;
                    esac
                fi

                # The launcher has one other Python heredoc for /api/tags.
                # It is unused when NEEDS_OLLAMA=0, but modeling it keeps this
                # stand-in honest about both launcher call shapes.
                case "$payload" in
                    *"/api/tags"*) printf '%s\n' 'Configured local models are installed.' ;;
                    *)
                        printf 'MC_PORT=%s\n' "$FAKE_MC_PORT"
                        printf 'WS_PORT=%s\n' "$FAKE_WS_PORT"
                        printf '%s\n' 'MC_WORLD_ID=fixture-world'
                        printf '%s\n' 'BOT_USERNAME=FixtureBot'
                        printf '%s\n' 'NEEDS_OLLAMA=0'
                        ;;
                esac
                exit 0
            fi

            if [ "${1:-}" = "-u" ]; then
                event controller-start
                if [ "${FAKE_CONTROLLER_MODE:-ready}" = "exit" ]; then
                    printf '%s\n' 'simulated controller failure' >&2
                    exit 23
                fi
                : > "$state/ws.ready"
                printf '%s\n' 'WebSocket bridge server running and awaiting bot connection.'
                printf '%s\n' 'Agent is ready for objectives.'
                trap 'event controller-term; rm -f "$state/ws.ready"; exit 0' TERM INT
                while :; do sleep 0.1; done
            fi

            printf 'unexpected fake-python arguments: %s\n' "$*" >&2
            exit 64
            ''',
        )
        self._write_executable(
            self.java,
            r'''
            #!/bin/sh
            set -eu

            state=${FAKE_STATE_DIR:?}
            event() { printf '%s\n' "$1" >> "$state/events.log"; }
            if [ "${1:-}" = "-version" ]; then
                printf '%s\n' 'openjdk version "21.0.9"' >&2
                exit 0
            fi

            event server-start
            if [ "${FAKE_SERVER_MODE:-ready}" = "exit" ]; then
                printf '%s\n' 'simulated server failure' >&2
                exit 22
            fi
            : > "$state/mc.ready"
            printf '%s\n' 'Done (0.01s)! For help, type "help"'
            trap 'event server-term; rm -f "$state/mc.ready"; exit 0' TERM INT
            while IFS= read -r line; do
                if [ "$line" = "stop" ]; then
                    event server-stop
                    rm -f "$state/mc.ready"
                    printf '%s\n' 'Stopping server'
                    exit 0
                fi
            done
            event server-stdin-closed
            rm -f "$state/mc.ready"
            exit 0
            ''',
        )
        self._write_executable(
            self.node,
            r'''
            #!/bin/sh
            set -eu

            state=${FAKE_STATE_DIR:?}
            event() { printf '%s\n' "$1" >> "$state/events.log"; }
            event bot-start
            if [ "${FAKE_NODE_MODE:-ready}" = "exit" ]; then
                printf '%s\n' 'simulated bot failure' >&2
                exit 24
            fi
            printf '%s\n' 'Bot successfully spawned in world'
            printf '%s\n' 'Connected to Python Orchestrator successfully.'
            trap 'event bot-term; exit 0' TERM INT
            while :; do sleep 0.1; done
            ''',
        )

    def _launch(self, *args: str, **overrides: str) -> tuple[subprocess.Popen[str], Path]:
        env = self.env.copy()
        env.update(overrides)
        output = self.root / f"launcher-{len(self._processes)}.log"
        stream = output.open("w", encoding="utf-8")
        process = subprocess.Popen(
            ["bash", str(self.root / "start.sh"), *args],
            cwd=self.root,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._processes.append((process, stream))
        return process, output

    def _wait_for_text(self, process: subprocess.Popen[str], output: Path, expected: str, timeout: float = 10) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = output.read_text(encoding="utf-8", errors="replace") if output.exists() else ""
            if expected in text:
                return text
            if process.poll() is not None:
                self.fail(f"launcher exited early ({process.returncode}):\n{text}")
            time.sleep(0.05)
        text = output.read_text(encoding="utf-8", errors="replace") if output.exists() else ""
        self.fail(f"timed out waiting for {expected!r}:\n{text}")

    def _wait_for_exit(self, process: subprocess.Popen[str], output: Path, timeout: float = 12) -> tuple[int, str]:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            text = output.read_text(encoding="utf-8", errors="replace")
            self.fail(f"launcher did not exit within {timeout}s:\n{text}")
        return code, output.read_text(encoding="utf-8", errors="replace")

    def _events(self) -> list[str]:
        return (self.state / "events.log").read_text(encoding="utf-8").splitlines()

    def _run_dir(self) -> Path:
        candidates = [
            path
            for path in (self.root / "data" / "run").iterdir()
            if path.is_dir() and path.name != "start.lock"
        ]
        self.assertEqual(len(candidates), 1, candidates)
        return candidates[0]

    def test_bash_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(self.root / "start.sh")],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normal_startup_reaches_chat_ready_and_term_stops_in_dependency_order(self):
        process, output = self._launch()
        startup = self._wait_for_text(process, output, "READY — join Minecraft Java")

        self.assertIn("1/3 Starting Minecraft on 127.0.0.1:25566", startup)
        self.assertIn("2/3 Starting Python controller with persistent world memory", startup)
        self.assertIn("3/3 Joining the Mineflayer agent", startup)
        self.assertIn("Agent: FixtureBot. In game chat", startup)
        self.assertIn("repo with spaces", startup)

        run_dir = self._run_dir()
        self.assertIn("Done (", (run_dir / "server.log").read_text(encoding="utf-8"))
        self.assertIn(
            "WebSocket bridge server running and awaiting bot connection.",
            (run_dir / "controller.log").read_text(encoding="utf-8"),
        )
        self.assertIn("Bot successfully spawned in world", (run_dir / "bot.log").read_text(encoding="utf-8"))
        self.assertIn("Connected to Python Orchestrator successfully.", (run_dir / "bot.log").read_text(encoding="utf-8"))
        self.assertIn("Agent is ready for objectives.", (run_dir / "controller.log").read_text(encoding="utf-8"))

        process.send_signal(signal.SIGTERM)
        code, stopped = self._wait_for_exit(process, output)
        self.assertEqual(code, 143)
        self.assertIn("Saving and stopping Minecraft...", stopped)
        self.assertFalse((self.root / "data" / "run" / "start.lock").exists())

        events = self._events()
        for earlier, later in (("bot-term", "controller-term"), ("controller-term", "server-stop")):
            self.assertLess(events.index(earlier), events.index(later), events)
        self.assertEqual(events[:3], ["server-start", "controller-start", "bot-start"])

    def test_bot_exit_before_readiness_cleans_up_controller_and_server(self):
        process, output = self._launch(FAKE_NODE_MODE="exit")
        code, text = self._wait_for_exit(process, output)

        self.assertEqual(code, 1)
        self.assertIn("Mineflayer bot exited before it was ready", text)
        self.assertIn("Stopping this launcher's services...", text)
        self.assertIn("Saving and stopping Minecraft...", text)
        self.assertFalse((self.root / "data" / "run" / "start.lock").exists())

        events = self._events()
        self.assertEqual(events[:3], ["server-start", "controller-start", "bot-start"])
        self.assertNotIn("bot-term", events, events)
        self.assertLess(events.index("controller-term"), events.index("server-stop"), events)

    def test_occupied_minecraft_port_fails_before_any_child_or_lock_is_created(self):
        process, output = self._launch("--check", FAKE_OCCUPIED="mc")
        code, text = self._wait_for_exit(process, output)

        self.assertEqual(code, 1)
        self.assertIn("Minecraft port 25566 is already occupied", text)
        self.assertEqual(self._events(), [])
        self.assertFalse((self.root / "data" / "run" / "start.lock").exists())


if __name__ == "__main__":
    unittest.main()
