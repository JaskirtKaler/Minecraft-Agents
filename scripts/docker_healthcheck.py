"""Check the supervisor's ready processes without opening an extra bot connection."""

import os
from pathlib import Path


def healthy(marker: Path) -> bool:
    try:
        pids = marker.read_text().splitlines()
        if len(pids) != 3:
            return False
        for value in pids:
            pid = int(value)
            if pid <= 0:
                return False
            os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy(Path(os.getenv("MINECRAFT_READY_FILE", "/tmp/minecraft-agent.ready"))) else 1)
