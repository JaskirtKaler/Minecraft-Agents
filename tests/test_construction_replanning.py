"""Observe/replan regressions, without inference or a scripted runtime plan."""
import json
import tempfile
from types import SimpleNamespace
import unittest

from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent, compiled_spatial_contracts
from practice.construction import SimulatedConstructionBridge, make_task, position


class ConstructionReplanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_outcome_correction_can_ask_without_freezing_or_executing(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        p = position(task.minimum)
        bad = {'kind': 'regions_match', 'regions': [
            {'min': p, 'max': p, 'block': 'cobblestone', 'mode': 'solid'},
            {'min': p, 'max': p, 'block': 'air', 'mode': 'solid'}]}
        replies = [
            {'type': 'act', 'goals': [bad], 'actions': [{'name': 'place', 'args': {'item': 'cobblestone', 'position': p}}]},
            {'type': 'blocked', 'message': 'Which final material should this overlapping cell have?'}]
        async def reply(messages, **kwargs):
            return json.dumps(replies.pop(0))
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=4,
                                       agent_timeout=10, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
            finally:
                await agent.aclose()
                library.close()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['data']['goals'], [])
        self.assertEqual(result['data']['trace'], [])
        self.assertIsNone(bridge._construction_contract)

    async def test_mixed_noop_batch_is_rejected_then_model_authors_new_batch(self):
        task = make_task('partial_row', 42)
        bridge = SimulatedConstructionBridge(task)
        correct = [p for p in task.expected if task.initial[p] == task.expected[p]]
        missing = [p for p in task.expected if task.initial[p] != task.expected[p]]
        goal = {'kind': 'regions_match', 'regions': [{
            'min': position(task.minimum), 'max': position((task.maximum[0], task.minimum[1], task.maximum[2])),
            'block': 'cobblestone', 'mode': 'solid'}], 'must_change': False}
        mixed = {'name': 'place_batch', 'args': {'item': 'cobblestone',
            'positions': [position(correct[0]), position(missing[0])]}}
        useful = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [position(p) for p in missing]}}
        packets = []
        async def reply(messages, **kwargs):
            packet = json.loads(messages[1]['content'])
            packets.append(packet)
            if len(packets) == 1:
                return json.dumps({'type': 'act', 'goals': [goal], 'actions': [mixed]})
            if len(packets) == 2:
                return json.dumps({'type': 'act', 'actions': [mixed]})
            failure = packet['recent_actions'][-2]['result']['data']
            self.assertEqual(failure['error_code'], 'CONSTRUCTION_NO_OP')
            self.assertTrue(failure['whole_batch_rejected'])
            self.assertFalse(any(c['tool']['name'] == 'place_batch' for c in bridge.calls))
            return json.dumps({'type': 'act', 'actions': [useful]})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=4,
                                       agent_timeout=10, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
            finally:
                await agent.aclose()
                library.close()
        self.assertTrue(result['verified'])
        issued = [c['tool'] for c in bridge.calls if c['tool']['name'] == 'place_batch']
        self.assertEqual(issued, [useful])
        self.assertEqual([e['action'] for e in result['data']['trace']], [mixed, useful])

    async def test_invalid_region_cannot_downgrade_to_sampled_cells_but_bounds_remain_free(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        p = position(task.minimum)
        bad = {'kind': 'regions_match', 'regions': [
            {'min': p, 'max': p, 'block': 'cobblestone', 'mode': 'solid'},
            {'min': p, 'max': p, 'block': 'air', 'mode': 'solid'}], 'must_change': True}
        full = {'kind': 'regions_match', 'regions': [{
            'min': position(task.minimum), 'max': position((task.maximum[0], task.minimum[1], task.maximum[2])),
            'block': 'cobblestone', 'mode': 'solid'}], 'must_change': True}
        proposed = {'name': 'place', 'args': {'item': 'cobblestone', 'position': p}}
        calls = []
        async def reply(messages, **kwargs):
            packet = json.loads(messages[1]['content'])
            calls.append(packet)
            if len(calls) == 1:
                return json.dumps({'type': 'act', 'goals': [bad], 'actions': [proposed]})
            if len(calls) == 2:
                self.assertIn('not frozen', packet['construction_plan_format'].lower())
                self.assertNotIn('actions', kwargs['response_schema']['oneOf'][0]['properties'])
                return json.dumps({'type': 'plan', 'goals': [
                    {'kind': 'block_is', 'block': 'cobblestone', 'position': p}], 'reason': 'A sampled subset is insufficient.'})
            if len(calls) == 3:
                self.assertFalse(any(c['tool']['name'] == 'place' for c in bridge.calls))
                return json.dumps({'type': 'plan', 'goals': [full], 'reason': 'Choose corrected complete bounds.'})
            return json.dumps({'type': 'act', 'actions': [{'name': 'place_batch', 'args': {
                'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}]})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=6,
                                       agent_timeout=10, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
            finally:
                await agent.aclose()
                library.close()
        self.assertTrue(result['verified'])
        self.assertEqual(result['data']['goals'], [full])
        self.assertEqual(len(result['data']['planner_errors']), 2)
        self.assertIn('sampled block_is', result['data']['planner_errors'][1]['planner_error'])
        self.assertEqual([e['action']['name'] for e in result['data']['trace']], ['place_batch'])

    def test_review_of_single_cell_exposes_exactly_one_cell_not_implicit_structure(self):
        p = {'x': -3, 'y': 64, 'z': -12}
        facts = compiled_spatial_contracts([{'kind': 'block_is', 'position': p, 'block': 'air'}])[0]
        self.assertEqual(facts['cell_count'], 1)
        self.assertEqual(facts['goal_kind'], 'block_is')
        self.assertEqual(facts['required_blocks']['air']['boxes'], [{'min': p, 'max': p}])
        self.assertIn('unconstrained', facts['semantics'])
