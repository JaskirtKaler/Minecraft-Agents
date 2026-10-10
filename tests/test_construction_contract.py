"""Generic batch contracts and shared budgets, without model or Minecraft calls."""
import json
from pathlib import Path
import subprocess
import unittest

from agent.planner_schema import tool_schema
from agent.tool_agent import TOOLS, READ_TOOLS
from agent.tool_timing import tool_timeout, POLICY

ROOT = Path(__file__).resolve().parents[1]


class ConstructionContractTests(unittest.TestCase):
    def test_batch_tool_schemas_are_physical_and_bounded(self):
        for name in ('place_batch', 'dig_batch'):
            self.assertIn(name, TOOLS)
            self.assertNotIn(name, READ_TOOLS)
            variant = tool_schema({name})['oneOf'][0]
            self.assertEqual(variant['properties']['name'], {'const': name})
            args = variant['properties']['args']
            self.assertFalse(args['additionalProperties'])
            self.assertIn('positions', args['required'])
            positions = args['properties']['positions']
            self.assertEqual((positions['minItems'], positions['maxItems']), (1, 64))
            self.assertEqual(positions['items']['required'], ['x', 'y', 'z'])
            for axis in ('x', 'y', 'z'):
                self.assertEqual(positions['items']['properties'][axis]['type'], 'integer')
            if name == 'place_batch':
                self.assertIn('item', args['required'])
            else:
                self.assertNotIn('tool', args['required'])

    def test_budget_matches_node_and_preserves_existing_tools(self):
        tools = []
        for name in ('place_batch', 'dig_batch'):
            for size in (1, 10, 32, 64, 65):
                tools.append({'name': name, 'args': {'item': 'cobblestone',
                    'positions': [{'x': i, 'y': 64, 'z': 0} for i in range(size)]}})
        tools += [{'name': 'inspect'}, {'name': 'mine_resource', 'args': {'count': 24}}]
        output = subprocess.check_output(['node', '-e',
            'const {toolTiming}=require("./bot/tool_timing"); '
            'console.log(JSON.stringify(JSON.parse(process.argv[1]).map(t=>toolTiming(t).timeoutMs)));',
            json.dumps(tools)], cwd=ROOT, text=True)
        self.assertEqual(json.loads(output), [round(tool_timeout(t) * 1000) for t in tools])
        self.assertEqual(tool_timeout({'name': 'place_batch', 'args': {'positions': [{}] * 32}}), 95)
        self.assertEqual(tool_timeout({'name': 'inspect'}), POLICY['shortMs'] / 1000)


if __name__ == '__main__':
    unittest.main()
