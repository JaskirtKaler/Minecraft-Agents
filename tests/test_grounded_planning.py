from copy import deepcopy
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent, validate_goals
from agent.planner_schema import decision_schema, tool_schema
from practice.grounded_tasks import tasks, grade_task
from practice.learning import grade


class GroundingTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        library = ExperienceLibrary(temp.name)
        self.addCleanup(library.close)
        state = {'ready': True, 'world': {'id': 't', 'sessionId': 's', 'dimension': 'overworld'},
                 'inventory': [], 'stats': {'position': {'x': 0, 'y': 64, 'z': 0}}}
        async def inspect(tool):
            args = tool['args']
            if args.get('item') == 'wheat_seeds':
                return {'success': True, 'data': {'isItem': True, 'isBlock': False,
                    'planting': {'result_block': 'wheat'}}}
            if args.get('item') == 'wheat':
                return {'success': True, 'data': {'isBlock': True, 'plantedBy': [{'item': 'wheat_seeds'}]}}
            return {'success': True, 'data': {'name': 'air', 'below': {'name': 'farmland'}}}
        bridge = SimpleNamespace(execute_tool=AsyncMock(side_effect=inspect), get_state=AsyncMock(return_value=state),
                                 cancel_task=AsyncMock())
        agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=AsyncMock()), library=library,
            settings=SimpleNamespace(learning_dir=temp.name, model_step_timeout=1, agent_timeout=3, agent_max_steps=3,
                                     goal_review_enabled=False), auto_reflect=False)
        self.addAsyncCleanup(agent.aclose)
        return agent, bridge, state

    async def test_invalid_crop_item_is_rejected_before_goal_freeze(self):
        agent, bridge, state = self.fixture()
        bad = {'kind': 'block_is', 'block': 'wheat_seeds', 'position': {'x': 2, 'y': 65, 'z': 0}}
        with self.assertRaisesRegex(ValueError, 'resulting BLOCK'):
            await agent.ground_goals([bad], state)
        self.assertTrue(all(c.args[0]['name'] == 'inspect' for c in bridge.execute_tool.call_args_list))

    async def test_wrong_height_rejected_and_current_soil_accepted(self):
        agent, bridge, state = self.fixture()
        goal = {'kind': 'block_is', 'block': 'wheat', 'position': {'x': 2, 'y': 65, 'z': 0}, 'must_change': True}
        bridge.execute_tool.side_effect = None
        bridge.execute_tool.side_effect = [
            {'success': True, 'data': {'isBlock': True, 'plantedBy': [{'item': 'wheat_seeds'}]}},
            {'success': True, 'data': {'name': 'air', 'below': {'name': 'wheat'}}}]
        with self.assertRaisesRegex(ValueError, 'observed soil'):
            await agent.ground_goals([goal], state)
        bridge.execute_tool.side_effect = [
            {'success': True, 'data': {'isBlock': True, 'plantedBy': [{'item': 'wheat_seeds'}]}},
            {'success': True, 'data': {'name': 'air', 'below': {'name': 'farmland'}}}]
        self.assertEqual(await agent.ground_goals([goal], state), ['air'])

    async def test_minimum_is_new_inventory_gain_not_absolute_or_silent_exact_change(self):
        agent, _, state = self.fixture()
        state['inventory'] = [{'name': 'oak_planks', 'count': 6}]
        exact = {'kind': 'inventory_gain', 'item': 'oak_planks', 'count': 4}
        minimum = {**exact, 'comparison': 'at_least'}
        self.assertFalse((await agent.check([exact], [0], state))[0])
        self.assertTrue((await agent.check([minimum], [0], state))[0])
        self.assertFalse((await agent.check([minimum], [3], state))[0])

    async def test_old_crop_does_not_prove_new_planting(self):
        agent, bridge, state = self.fixture()
        goal = {'kind': 'block_is', 'block': 'wheat', 'position': {'x': 1, 'y': 64, 'z': 1}, 'must_change': True}
        bridge.execute_tool.side_effect = None
        bridge.execute_tool.return_value = {'success': True, 'data': {'name': 'wheat'}}
        self.assertFalse((await agent.check([goal], ['wheat'], state))[0])
        receipt = {'action': {'name': 'place'}, 'result': {'success': True, 'verified': True,
                  'data': {'name': 'wheat', 'position': goal['position']}}}
        self.assertTrue((await agent.check([goal], ['wheat'], state, [receipt]))[0])
        receipt['result']['verified'] = False
        self.assertFalse((await agent.check([goal], ['wheat'], state, [receipt]))[0])

    async def test_operator_cannot_be_weakened_after_actions(self):
        agent, bridge, state = self.fixture()
        goal = {'kind': 'inventory_gain', 'item': 'oak_planks', 'count': 4}
        decisions = [
            {'type': 'act', 'goals': [goal], 'action': {'name': 'equip', 'args': {'item': 'oak_log'}}},
            {'type': 'act', 'goals': [{**goal, 'comparison': 'at_least'}],
             'action': {'name': 'craft', 'args': {'item': 'oak_planks', 'count': 4}}},
            {'type': 'blocked', 'message': 'Exact requirement remains fixed.'}]
        agent.client.generate_response.side_effect = [json.dumps(d) for d in decisions]
        result = await agent.run('craft exactly four new planks')
        self.assertFalse(result['verified'])
        self.assertEqual(result['data']['goals'], [goal])
        self.assertTrue(all(call.args[0]['name'] in {'equip', 'inspect'} for call in bridge.execute_tool.call_args_list))


class TaskAuditTests(unittest.TestCase):
    def test_suite_varies_quantities_and_seeds_but_contains_no_action_plans(self):
        suite = tasks(310000)
        self.assertEqual(len(suite), 6)
        self.assertEqual([t.seed for t in suite], [310000, 310001, 310002, 310004, 310005, 310006])
        for index in range(3):
            self.assertNotEqual(suite[index].seed % 3, suite[index + 3].seed % 3)
        self.assertEqual(suite, tasks(310000))
        self.assertNotEqual(suite[0].requirements, suite[3].requirements)
        self.assertNotEqual(suite[1].requirements, suite[4].requirements)
        self.assertTrue(all(not hasattr(t, 'actions') for t in suite))

    def test_task_request_overrides_weaker_model_goals(self):
        task = tasks(0)[0]
        before = {'health': 20, 'inventory': {}}
        after = {'health': 20, 'inventory': {'oak_planks': 6, 'stick': 4}}
        self.assertEqual(grade_task(task, before, after), [])
        after['inventory']['oak_planks'] = 2
        self.assertTrue(grade_task(task, before, after))

    def test_exact_chest_audit_rejects_surplus_and_unrelated_transfers(self):
        task = tasks(0)[2]
        before = {'health': 20, 'chest': {}}
        after = {'health': 20, 'chest': {'cobblestone': 3, 'dirt': 2}}
        self.assertEqual(grade_task(task, before, after), [])
        self.assertTrue(grade_task(task, before, {**after, 'chest': {**after['chest'], 'stick': 1}}))
        self.assertTrue(grade_task(task, before, {**after, 'chest': {'cobblestone': 4, 'dirt': 2}}))
        self.assertTrue(grade_task(task, before, {**after, 'broken': [{'block': 'oak_log'}]}))

    def test_plant_audit_counts_new_active_cells_and_preserves_existing_crops(self):
        task = tasks(0)[1]
        before = {'health': 20, 'blocks': {'1,64,1': 'air', '2,64,1': 'air', '3,64,1': 'wheat'}}
        after = {'health': 20, 'blocks': {p: 'wheat' for p in before['blocks']},
                 'placed': [{'block': 'wheat', 'x': x, 'y': 64, 'z': 1} for x in (1, 2)]}
        self.assertEqual(grade_task(task, before, after), [])
        self.assertTrue(grade_task(task, before, {**after, 'placed': []}))
        self.assertTrue(grade_task(task, before, {**after, 'broken': [{'block': 'wheat', 'x': 3, 'y': 64, 'z': 1}]}))
        self.assertTrue(grade_task(task, before, {**after, 'placed': after['placed'] + [
            {'block': 'wheat', 'x': 9, 'y': 64, 'z': 1}]}))

    def test_server_minimum_matches_controller_and_shifted_chest_baseline(self):
        minimum = {'kind': 'inventory_gain', 'comparison': 'at_least', 'item': 'oak_planks', 'count': 4}
        self.assertTrue(grade([minimum], {'health': 20, 'inventory': {'oak_planks': 1}},
                              {'health': 20, 'inventory': {'oak_planks': 6}})[0])
        position = {'x': 4, 'y': 66, 'z': 2}
        goal = {'kind': 'container_gain', 'position': position, 'item': 'dirt', 'count': 2}
        self.assertTrue(grade([goal], {'health': 20, 'chest': {'dirt': 5}, 'chest_position': position},
                              {'health': 20, 'containers': {'4,66,2': {'dirt': 7}}})[0])

    def test_schema_and_runtime_reject_transfer_comparison_and_invalid_booleans(self):
        validate_goals([{'kind': 'inventory_gain', 'item': 'stick', 'count': 4, 'comparison': 'at_least'}])
        with self.assertRaises(ValueError):
            validate_goals([{'kind': 'container_gain', 'item': 'stick', 'count': 4, 'comparison': 'at_least',
                             'position': {'x': 0, 'y': 0, 'z': 0}}])
        with self.assertRaises(ValueError):
            validate_goals([{'kind': 'block_is', 'block': 'wheat', 'must_change': 1, 'position': {'x': 0, 'y': 0, 'z': 0}}])
        with self.assertRaises(ValueError):
            validate_goals([{'kind': 'inventory_gain', 'item': 'stick', 'count': 4, 'comparison': {}}])
        with self.assertRaisesRegex(ValueError, 'distinct target cells'):
            validate_goals([{'kind': 'block_is', 'block': 'wheat', 'position': {'x': 1, 'y': 65, 'z': 1}}] * 2)
        branches = decision_schema({'craft_budget'})['oneOf'][0]['properties']['goals']['items']['oneOf']
        inventory = branches[0]
        self.assertIn('comparison', inventory['properties'])
        self.assertNotIn('comparison', inventory['required'])
        budget = tool_schema({'craft_budget'})['oneOf'][0]
        self.assertEqual(budget['properties']['args']['required'], ['steps'])


if __name__ == '__main__':
    unittest.main()
