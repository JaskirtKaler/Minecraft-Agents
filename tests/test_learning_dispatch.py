"""Offline Bash dispatch checks; a fake Python never loads models or Minecraft."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = Path("/bin/bash")


@unittest.skipUnless(BASH.is_file(), "Bash required for shell-dispatch checks")
class LearningDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning dispatch fixture ")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name) / "project with spaces"
        self.fixture.mkdir()
        self.launcher = self.fixture / "learn.sh"
        shutil.copy2(ROOT / "learn.sh", self.launcher)
        self.capture = Path(self.temp.name) / "captured argv.bin"
        self.cwd_capture = Path(self.temp.name) / "captured cwd.txt"
        self.fake_python = Path(self.temp.name) / "fake python executable"
        # This is only a shell argument recorder. It cannot execute Python code,
        # import a practice module, launch Minecraft or contact a model service.
        self.fake_python.write_text(
            '#!/bin/sh\n'
            'set -eu\n'
            ': "${LEARN_TEST_CAPTURE:?}" "${LEARN_TEST_CWD:?}"\n'
            'for argument do\n'
            '    printf "%s\\0" "$argument"\n'
            'done > "$LEARN_TEST_CAPTURE"\n'
            '/bin/pwd -P > "$LEARN_TEST_CWD"\n'
            'exit "${LEARN_TEST_EXIT:-0}"\n', encoding="utf-8")
        self.fake_python.chmod(self.fake_python.stat().st_mode | stat.S_IXUSR)
        self.env = os.environ.copy()
        self.env.update(MINECRAFT_PYTHON=str(self.fake_python),
                        LEARN_TEST_CAPTURE=str(self.capture), LEARN_TEST_CWD=str(self.cwd_capture),
                        LEARN_TEST_EXIT="0")

    def invoke(self, *args, exit_code=0):
        self.capture.unlink(missing_ok=True)
        self.cwd_capture.unlink(missing_ok=True)
        result = subprocess.run([str(BASH), str(self.launcher), *args], cwd=Path(self.temp.name),
                                env={**self.env, "LEARN_TEST_EXIT": str(exit_code)},
                                capture_output=True, text=True, timeout=5)
        captured = self.capture.read_bytes().split(b"\0")[:-1] if self.capture.exists() else None
        return result, [part.decode() for part in captured] if captured is not None else None

    def assert_dispatch(self, arguments, expected):
        result, captured = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(captured, expected)
        self.assertEqual(self.cwd_capture.read_text().strip(), str(self.fixture.resolve()))

    def test_default_empty_arguments_work_on_macos_bash_with_nounset(self):
        # macOS /bin/bash is 3.2: expanding an initialized but EMPTY array under
        # set -u fails unless the launcher uses the compatible guarded syntax.
        self.assert_dispatch([], ["-m", "practice.runner", "--learn"])

    def test_explicit_minecraft_without_other_arguments_uses_same_default(self):
        self.assert_dispatch(["--backend", "minecraft"], ["-m", "practice.runner", "--learn"])

    def test_existing_minecraft_arguments_pass_through_without_reordering(self):
        arguments = ["--suite", "grounding", "--episodes", "3", "--seed", "-12", "--check"]
        self.assert_dispatch(arguments, ["-m", "practice.runner", "--learn", *arguments])
        self.assert_dispatch(["--backend", "minecraft", *arguments],
                             ["-m", "practice.runner", "--learn", *arguments])

    def test_construction_simulator_empty_extra_arguments_work_on_macos_bash(self):
        self.assert_dispatch(["--backend", "simulator", "--suite", "construction"],
                             ["-m", "practice.construction"])

    def test_construction_check_does_not_use_real_server_runner(self):
        self.assert_dispatch(["--backend", "simulator", "--suite", "construction", "--check"],
                             ["-m", "practice.construction", "--check"])

    def test_construction_arguments_preserve_negative_seed_and_exact_strings(self):
        extras = ["--episodes", "5", "--seed", "-123", "--timeout", "240", "--max-steps", "8",
                  "--task", "partial_row", "--model", "local model with spaces"]
        self.assert_dispatch(["--backend", "simulator", "--suite", "construction", *extras],
                             ["-m", "practice.construction", *extras])

    def test_empty_string_argument_is_not_confused_with_an_empty_array(self):
        self.assert_dispatch(["--objective", ""], ["-m", "practice.runner", "--learn", "--objective", ""])
        self.assert_dispatch(["--backend", "simulator", "--suite", "construction", "--model", ""],
                             ["-m", "practice.construction", "--model", ""])

    def test_backend_and_suite_order_does_not_change_dispatch(self):
        variations = [
            ["--suite", "construction", "--backend", "simulator", "--check"],
            ["--check", "--suite", "construction", "--backend", "simulator"],
            ["--check", "--backend", "simulator", "--suite", "construction"],
        ]
        for arguments in variations:
            with self.subTest(arguments=arguments):
                self.assert_dispatch(arguments, ["-m", "practice.construction", "--check"])

    def test_missing_or_unknown_backend_is_rejected_before_python(self):
        for arguments in (["--backend"], ["--backend", "unknown"], ["--backend", "--suite", "construction"]):
            with self.subTest(arguments=arguments):
                result, captured = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIsNone(captured)
                self.assertIn("backend", result.stderr)

    def test_missing_or_unknown_simulator_suite_is_rejected_before_python(self):
        variations = [
            ["--backend", "simulator"],
            ["--backend", "simulator", "--suite"],
            ["--backend", "simulator", "--suite", "grounding"],
            ["--backend", "simulator", "--suite", "--check"],
        ]
        for arguments in variations:
            with self.subTest(arguments=arguments):
                result, captured = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIsNone(captured)
                self.assertIn("suite", result.stderr)

    def test_exec_preserves_evaluation_failure_exit_code(self):
        result, captured = self.invoke("--backend", "simulator", "--suite", "construction", "--check", exit_code=7)
        self.assertEqual(captured, ["-m", "practice.construction", "--check"])
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
