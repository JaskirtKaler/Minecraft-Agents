"""The bridge and Node share one bounded timing policy."""
import json
from pathlib import Path

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'shared/tool_timing.json').read_text())
GATHER = {'mine_logs', 'mine_resource'}


def tool_timeout(tool):
    if tool.get('name') not in GATHER:
        return POLICY['shortMs'] / 1000
    count = tool.get('args', {}).get('count', 1)
    count = min(64, max(1, count)) if type(count) is int else 1
    return min(POLICY['gatherMaxMs'], POLICY['gatherBaseMs'] + count * POLICY['gatherPerItemMs']) / 1000
