"""Portable interpreter and server-only dispatch using executable recorders."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "arguments.bin"
        self.env = os.environ.copy()
        for key in ("MINECRAFT_PYTHON", "MINECRAFT_JAVA", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "JAVA_HOME"):
            self.env.pop(key, None)
        self.env["RUNTIME_TEST_CAPTURE"] = str(self.capture)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/runtime.sh", self.root / "scripts/runtime.sh")
        for name in ("learn.sh", "practice.sh", "rl.sh"):
            shutil.copy2(ROOT / name, self.root / name)
        self.python = self.recorder("conda environment/bin/python")

    def recorder(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nfor argument do printf "%s\\0" "$argument"; done > "$RUNTIME_TEST_CAPTURE"\n')
        path.chmod(0o755)
        return path

    def invoke(self, script, *args, **environment):
        self.capture.unlink(missing_ok=True)
        result = subprocess.run(["bash", str(self.root / script), *args], cwd=self.root,
                                env={**self.env, **environment}, capture_output=True, text=True, timeout=5)
        captured = self.capture.read_bytes().split(b"\0")[:-1] if self.capture.exists() else None
        return result, [part.decode() for part in captured] if captured is not None else None

    def test_all_practice_launchers_use_active_conda_and_preserve_arguments(self):
        for script, module, extra in (("practice.sh", "practice.runner", []),
                                      ("learn.sh", "practice.runner", ["--learn"]),
                                      ("rl.sh", "rl.runner", [])):
            with self.subTest(script=script):
                result, arguments = self.invoke(script, "--check", CONDA_PREFIX=str(self.python.parents[1]))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(arguments, ["-m", module, *extra, "--check"])

    def test_explicit_interpreter_wins_over_conda_and_missing_override_fails(self):
        other = self.recorder("chosen interpreter")
        self.python.unlink()
        result, arguments = self.invoke("practice.sh", "--list", MINECRAFT_PYTHON=str(other),
                                        CONDA_PREFIX=str(self.python.parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments, ["-m", "practice.runner", "--list"])
        result, arguments = self.invoke("practice.sh", MINECRAFT_PYTHON=str(self.root / "missing python"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(arguments)
        self.assertIn("Python not found", result.stderr)

    def test_auto_activated_conda_base_does_not_override_project_discovery(self):
        result = subprocess.run(["bash", "-c", 'source "$RUNTIME_TEST_HELPER"; minecraft_python'],
                                env={**self.env, "RUNTIME_TEST_HELPER": str(self.root / "scripts/runtime.sh"),
                                     "CONDA_DEFAULT_ENV": "base", "CONDA_PREFIX": str(self.python.parents[1])},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(result.stdout.strip(), str(self.python))
        self.assertFalse(self.capture.exists())

    def server_fixture(self):
        server = self.root / "server"
        (server / "plugins").mkdir(parents=True)
        shutil.copy2(ROOT / "server/start.sh", server / "start.sh")
        for name in ("server.jar", "plugins/ViaVersion.jar", "plugins/ViaBackwards.jar"):
            (server / name).write_bytes(b"fixture jar")
        return server

    def test_server_only_launch_preserves_eula_and_uses_selected_java(self):
        server = self.server_fixture()
        eula = server / "eula.txt"
        eula.write_text("# explicitly accepted fixture\neula=true\n")
        before = eula.read_bytes()
        java = self.recorder("chosen java")
        result, arguments = self.invoke("server/start.sh", "--port", "25566", MINECRAFT_JAVA=str(java))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments, ["-Xms2G", "-Xmx2G", "-jar", "server.jar", "--nogui", "--port", "25566"])
        self.assertEqual(eula.read_bytes(), before)

    def test_server_uses_java_home_when_no_explicit_override_is_set(self):
        server = self.server_fixture()
        (server / "eula.txt").write_text("eula=true\n")
        java = self.recorder("jdk with spaces/bin/java")
        result, arguments = self.invoke("server/start.sh", JAVA_HOME=str(java.parents[1]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments[-1], "--nogui")

    def test_server_refuses_unaccepted_eula_or_missing_bootstrap_without_launching(self):
        server = self.server_fixture()
        eula = server / "eula.txt"
        eula.write_text("eula=false\n")
        java = self.recorder("chosen java")
        result, arguments = self.invoke("server/start.sh", MINECRAFT_JAVA=str(java))
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(arguments)
        self.assertEqual(eula.read_text(), "eula=false\n")
        (server / "server.jar").unlink()
        result, arguments = self.invoke("server/start.sh", MINECRAFT_JAVA=str(java))
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(arguments)
        self.assertIn("Missing bundled", result.stderr)
        self.assertFalse((server / "server.jar").exists())


if __name__ == "__main__":
    unittest.main()
