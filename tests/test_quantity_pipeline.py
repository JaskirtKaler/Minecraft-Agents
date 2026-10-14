"""Controller quantity grounding: no inference service or Minecraft writes."""
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.experience import ExperienceLibrary
from agent.regions import compile_goal
from agent.tool_agent import ModelToolAgent
from practice.construction import SimulatedConstructionBridge, make_task, position


class QuantityPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, task, objective, replies):
        bridge = SimulatedConstructionBridge(task)
        client = SimpleNamespace(generate_response=AsyncMock(side_effect=[json.dumps(r) for r in replies]))
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=3,
                                       agent_timeout=10, goal_review_enabled=True)
            agent = ModelToolAgent(bridge, client, settings=settings, library=library, auto_reflect=False)
            try:
                result = await agent.run(objective)
            finally:
                await agent.aclose()
                library.close()
        return result, bridge, client

    async def test_complete_66_block_geometry_is_not_rejected_as_a_20_item_request(self):
        task = make_task('shelter', 303)
        low, high = task.minimum, task.maximum
        x0, y0, z0 = low
        x1, _, z1 = high
        goal = {'kind': 'regions_match', 'regions': [
            {'min': position((x0, y0, z0)), 'max': position((x1, y0, z1)), 'block': 'cobblestone', 'mode': 'solid'},
            {'min': position((x0, y0 + 1, z0)), 'max': position((x1, y0 + 2, z1)), 'block': 'cobblestone', 'mode': 'perimeter_xz'},
            {'min': position((x0, y0 + 3, z0)), 'max': position((x1, y0 + 3, z1)), 'block': 'cobblestone', 'mode': 'solid'},
            {'min': position((x0 + 1, y0 + 1, z0 + 1)), 'max': position((x1 - 1, y0 + 2, z1 - 1)), 'block': 'air', 'mode': 'solid'},
        ], 'exceptions': [{'position': position(p), 'block': 'air'} for p, b in task.expected.items()
                         if b == 'air' and p[2] == z1], 'must_change': True}
        self.assertEqual(compile_goal(goal), task.expected)
        self.assertEqual(sum(b == 'cobblestone' for b in compile_goal(goal).values()), 66)
        result, bridge, client = await self.run_fake(task, task.objective, [
            {'type': 'act', 'goals': [goal], 'actions': [{'name': 'place_batch', 'args': {
                'item': 'cobblestone', 'positions': [position(low)]}}]},
            {'covers_request': True, 'reason': 'Complete fixture geometry.', 'missing_outcomes': []},
            {'type': 'blocked', 'message': 'Fake model stops before physical work.'},
        ])
        self.assertEqual(result['data']['goals'], [goal])
        self.assertEqual(result['data']['requested_quantities'], [])
        self.assertTrue(result['data']['quantity_source_facts']['safe_without_quantity_inference'])
        self.assertEqual(result['data']['planner_errors'], [])
        self.assertEqual(client.generate_response.await_count, 3)
        self.assertFalse(result['verified'], 'Goal acceptance is not build completion.')
        self.assertFalse(any(c['tool']['name'] == 'place_batch' for c in bridge.calls))

    async def test_explicit_quantity_cannot_be_omitted_before_goal_freeze(self):
        task = make_task('negative_floor', 42)
        goal = {'kind': 'inventory_gain', 'item': 'cobblestone', 'count': 2}
        result, bridge, client = await self.run_fake(task, 'Mine exactly 2 new cobblestone.', [
            {'type': 'act', 'goals': [goal], 'actions': [{'name': 'mine_resource', 'args': {
                'item': 'cobblestone', 'count': 2}}]},
            {'requirements': []},
            {'type': 'blocked', 'message': 'Need a corrected interpretation.'},
        ])
        self.assertEqual(result['data']['goals'], [])
        self.assertIn('omitted', result['data']['planner_errors'][0]['planner_error'])
        self.assertFalse(any(c['tool']['name'] == 'mine_resource' for c in bridge.calls))
        source_request = json.loads(client.generate_response.await_args_list[1].args[0][1]['content'])
        self.assertEqual(source_request['quantity_source_facts']['explicit_amounts'][0]['count'], 2)
        self.assertNotIn('goals', source_request)


if __name__ == '__main__':
    unittest.main()
