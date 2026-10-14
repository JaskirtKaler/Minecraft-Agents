"""Exercise container readiness with real processes and an ephemeral PID marker."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.docker_healthcheck import healthy


class DockerHealthcheckTests(unittest.TestCase):
    def test_a_ready_pair_becomes_unhealthy_when_a_child_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ready"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                marker.write_text(f"{os.getpid()}\n{os.getpid()}\n{child.pid}\n")
                self.assertTrue(healthy(marker))
                child.terminate()
                child.wait(timeout=5)
                self.assertFalse(healthy(marker))
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)

    def test_missing_or_invalid_ready_state_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ready"
            self.assertFalse(healthy(marker))
            for value in ("", "1\n2\n", "bad\n2\n3\n", "0\n2\n3\n", "-1\n2\n3\n"):
                with self.subTest(value=value):
                    marker.write_text(value)
                    self.assertFalse(healthy(marker))


if __name__ == "__main__":
    unittest.main()
