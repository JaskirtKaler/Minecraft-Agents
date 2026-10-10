"""Outcome/action conformance and controller metadata; no Minecraft/model calls."""
import copy
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.bridge import MineflayerBridge
from agent.construction_checks import construction_contract, action_conflict, spatial_cells, verified_final_cell_change
from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent
from practice.construction import SimulatedConstructionBridge, make_task, position, grade_task


class ConstructionChecksTests(unittest.TestCase):
    def setUp(self):
        self.goal = {'kind': 'regions_match', 'regions': [{'min': {'x': -3, 'y': 64, 'z': -8},
            'max': {'x': -1, 'y': 64, 'z': -8}, 'mode': 'solid', 'block': 'cobblestone'}],
            'exceptions': [{'position': {'x': -2, 'y': 64, 'z': -8}, 'block': 'air'}], 'must_change': False}

    def test_full_batch_rejected_not_filtered_or_reordered(self):
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [
            {'x': -3, 'y': 64, 'z': -8}, {'x': -2, 'y': 64, 'z': -8}]}}
        original = copy.deepcopy(action)
        failure = action_conflict(action, [self.goal], [])
        self.assertEqual(failure['desired'], 'air')
        self.assertEqual(action, original)
        action['args']['positions'] = [action['args']['positions'][0]]
        self.assertIsNone(action_conflict(action, [self.goal], []))
        action['args']['positions'][0]['z'] = 8
        self.assertIsNone(action_conflict(action, [self.goal], [])['desired'])

    def test_correct_final_blocks_are_preserved_without_using_baseline_as_current(self):
        action = {'name': 'dig', 'args': {'position': {'x': -3, 'y': 64, 'z': -8}}}
        self.assertIsNone(action_conflict(action, [self.goal], [{'goal': self.goal,
            'before': {'-3,64,-8': 'cobblestone'}, 'observed': {'-3,64,-8': 'dirt'}}]))
        self.assertIsNotNone(action_conflict(action, [self.goal], [{'goal': self.goal,
            'observed': {'-3,64,-8': 'cobblestone'}}]))

    def test_contract_conflicts_across_goals_rejected_and_payload_is_copy(self):
        preserved = {'kind': 'block_is', 'block': 'chest', 'position': {'x': -3, 'y': 64, 'z': -8}}
        with self.assertRaises(ValueError):
            spatial_cells([self.goal, preserved])
        world = {'id': 'w', 'sessionId': 's', 'dimension': 'overworld'}
        contract = construction_contract([self.goal], world, 'task')
        self.assertEqual(contract['desired_cells']['-2,64,-8'], 'air')
        contract['world']['id'] = 'other'
        self.assertEqual(world['id'], 'w')

    def test_no_hardcoded_plan_or_item_mapping_for_nonconstruction_tasks(self):
        action = {'name': 'place', 'args': {'item': 'wheat_seeds', 'position': {'x': 1, 'y': 64, 'z': 1}}}
        self.assertIsNone(action_conflict(action, [self.goal], []))
        self.assertIsNone(action_conflict({'name': 'place_batch', 'args': {'item': 'dirt',
            'positions': [action['args']['position']]}}, [{'kind': 'inventory_at_least', 'item': 'dirt', 'count': 1}], []))

    def test_own_repair_can_prove_air_change_without_rewriting_matching_baseline(self):
        p = {'x': -2, 'y': 64, 'z': -8}
        goal = {'kind': 'block_is', 'block': 'air', 'position': p, 'must_change': True}
        row = {'position': p, 'before': 'cobblestone', 'after': 'air', 'verified': True}
        action = {'name': 'repair_batch', 'args': {'positions': [p]}}
        entry = {'action': action, 'result': {'success': False, 'verified': False,
            'data': {'partial': {'cleared': [row]}}}}
        self.assertTrue(verified_final_cell_change(goal, [entry]))
        for replacement in ({**row, 'verified': False}, {**row, 'after': 'unknown'},
                            {**row, 'before': 'air'}, {**row, 'position': {**p, 'z': 8}}):
            wrong = {**entry, 'result': {**entry['result'], 'data': {'partial': {'cleared': [replacement]}}}}
            self.assertFalse(verified_final_cell_change(goal, [wrong]))
        self.assertFalse(verified_final_cell_change(goal, [{'action': action, 'result': {
            'success': True, 'verified': True, 'data': {'skipped': [row]}}}]))

    def test_single_placement_needs_actual_consumption_or_verified_cell_receipt(self):
        p = {'x': -2, 'y': 64, 'z': -8}
        goal = {'kind': 'block_is', 'block': 'cobblestone', 'position': p, 'must_change': True}
        action = {'name': 'place', 'args': {'item': 'cobblestone', 'position': p}}
        result = {'success': True, 'verified': True, 'data': {'name': 'cobblestone', 'position': p,
                  'already_correct': True, 'inventory_delta': 0}}
        self.assertFalse(verified_final_cell_change(goal, [{'action': action, 'result': result}]))
        result['data'].update(inventory_before=2, inventory_after=1)
        self.assertFalse(verified_final_cell_change(goal, [{'action': action, 'result': result}]),
                         'Contradictory no-op metadata cannot prove a new placement.')
        result['data'].pop('already_correct')
        result['data']['inventory_delta'] = -1
        self.assertTrue(verified_final_cell_change(goal, [{'action': action, 'result': result}]))
        result['data']['inventory_after'] = 2
        self.assertFalse(verified_final_cell_change(goal, [{'action': action, 'result': result}]))
        row = {'position': p, 'before': 'air', 'after': 'cobblestone', 'verified': True}
        result['data'] = {'placements': [row]}
        self.assertTrue(verified_final_cell_change(goal, [{'action': action, 'result': result}]))
        result['data'] = {'skipped': [row]}
        self.assertFalse(verified_final_cell_change(goal, [{'action': action, 'result': result}]))


class ConstructionControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_uses_controller_token_not_model_metadata_and_forgets_on_cancel(self):
        bridge = MineflayerBridge()
        bridge._request = AsyncMock(side_effect=[{'active': True, 'task_id': 'trusted'}, {'success': False}, {'active': False}])
        contract = {'task_id': 'trusted', 'world': {}, 'desired_cells': {'0,64,0': 'air'}}
        await bridge.set_construction_contract(contract)
        tool = {'name': 'repair_batch', 'args': {'positions': []}, 'constructionTaskId': 'forged'}
        await bridge.execute_tool(tool)
        envelope = bridge._request.call_args.args[0]
        self.assertEqual(envelope['constructionTaskId'], 'trusted')
        self.assertEqual(envelope['tool'], tool)
        await bridge.set_construction_contract(None)
        self.assertIsNone(bridge._construction_task_id)
        bridge._construction_task_id = 'old'
        await bridge.cancel_task()
        self.assertIsNone(bridge._construction_task_id)

    async def test_unknown_handshake_never_grants_authority(self):
        bridge = MineflayerBridge()
        bridge._request = AsyncMock(return_value={'active': True, 'task_id': 'wrong'})
        bridge._construction_task_id = 'old'
        with self.assertRaises(RuntimeError):
            await bridge.set_construction_contract({'task_id': 'new'})
        self.assertIsNone(bridge._construction_task_id)

    async def test_construction_error_response_propagates(self):
        bridge = MineflayerBridge()
        future = __import__('asyncio').get_running_loop().create_future()
        bridge.pending_requests['id'] = future
        await bridge._route_message({'type': 'construction_response', 'id': 'id',
                                    'error': {'message': 'World changed'}})
        with self.assertRaisesRegex(ValueError, 'World changed'):
            await future

    async def test_closing_socket_cancellation_forgets_authority_without_masking_failure(self):
        bridge = MineflayerBridge()
        bridge._construction_task_id = 'old'
        bridge.active_client = SimpleNamespace(send=AsyncMock(side_effect=ConnectionError('Socket closed')))
        with self.assertLogs('BridgeServer', level='WARNING'):
            await bridge.cancel_task()
        self.assertIsNone(bridge._construction_task_id)

    async def test_failed_cancellation_and_revocation_still_save_partial_attempt(self):
        task = make_task('negative_floor', 307)
        bridge = SimulatedConstructionBridge(task)
        original_execute, original_set = bridge.execute_tool, bridge.set_construction_contract
        async def execute(tool):
            if tool['name'] == 'place_batch':
                raise RuntimeError('Original transport failure')
            return await original_execute(tool)
        async def set_contract(contract):
            if contract is None:
                raise ConnectionError('Clear acknowledgment unavailable')
            return await original_set(contract)
        bridge.execute_tool = execute
        bridge.set_construction_contract = set_contract
        bridge.cancel_task = AsyncMock(side_effect=ConnectionError('Cancellation unavailable'))
        goal = {'kind': 'regions_match', 'must_change': True, 'regions': [
            {'min': position(task.minimum), 'max': position((task.maximum[0], task.minimum[1], task.maximum[2])),
             'block': 'cobblestone', 'mode': 'solid'}]}
        decision = {'type': 'act', 'goals': [goal], 'actions': [{'name': 'place_batch',
            'args': {'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}]}
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=3, agent_max_steps=3,
                                       agent_timeout=15, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=AsyncMock(return_value=json.dumps(decision))),
                                   settings=settings, library=library, auto_reflect=False)
            try:
                with self.assertLogs('ToolAgent', level='WARNING'):
                    result = await agent.run(task.objective)
                self.assertFalse(result['verified'])
                self.assertIn('Original transport failure', result['message'])
                self.assertEqual(result['data']['in_flight_action']['name'], 'place_batch')
                self.assertIn('construction_contract_identity', result['data'])
                self.assertEqual(library.progress()[0]['attempts'], 1)
            finally:
                await agent.aclose()
                library.close()

    async def test_loop_rejects_contradictory_action_then_model_chooses_repair(self):
        task = make_task('repair_shelter', 103)
        bridge = SimulatedConstructionBridge(task)
        floor_y = task.minimum[1]
        x0, _, z0 = task.minimum
        x1, roof_y, z1 = task.maximum
        door = (x0 + (x1 - x0 + 1) // 2, floor_y + 1, z1)
        goal = {'kind': 'regions_match', 'must_change': False, 'regions': [
            {'min': position((x0, floor_y, z0)), 'max': position((x1, floor_y, z1)), 'block': 'cobblestone', 'mode': 'solid'},
            {'min': position((x0, floor_y + 1, z0)), 'max': position((x1, roof_y - 1, z1)), 'block': 'cobblestone', 'mode': 'perimeter_xz'},
            {'min': position((x0, roof_y, z0)), 'max': position((x1, roof_y, z1)), 'block': 'cobblestone', 'mode': 'solid'},
            {'min': position((x0 + 1, floor_y + 1, z0 + 1)), 'max': position((x1 - 1, roof_y - 1, z1 - 1)), 'block': 'air', 'mode': 'solid'}],
            'exceptions': [{'position': position(door), 'block': 'air'},
                           {'position': position((door[0], door[1] + 1, door[2])), 'block': 'air'}]}
        goals = [goal] + [{'kind': 'block_is', 'block': 'air', 'position': position(p), 'must_change': True}
                         for p in task.owned_mistake_fixture]
        calls = 0
        async def reply(messages, **kwargs):
            nonlocal calls
            calls += 1
            payload = json.loads(messages[1]['content'])
            if calls == 1:
                return json.dumps({'type': 'act', 'goals': goals, 'actions': [{'name': 'place_batch',
                    'args': {'item': 'cobblestone', 'positions': [position(task.owned_mistake_fixture[0])]}}]})
            if calls == 2:
                self.assertTrue(payload['construction_repair']['active'])
                return json.dumps({'type': 'act', 'actions': [{'name': 'place_batch', 'args': {
                    'item': 'cobblestone', 'positions': [position(task.owned_mistake_fixture[0])]}}]})
            rejected = next(e for e in payload['recent_actions'] if 'result' in e)
            self.assertEqual(rejected['result']['data']['error_code'], 'CONSTRUCTION_GOAL_CONFLICT')
            return json.dumps({'type': 'act', 'actions': [{'name': 'repair_batch',
                'args': {'positions': [position(p) for p in task.owned_mistake_fixture]}}]})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=3, agent_max_steps=5,
                                       agent_timeout=15, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
                self.assertTrue(result['verified'], result['message'])
                self.assertTrue(all(e['before'] == 'air' and e['observed'] == 'air' and e['met']
                                    for e in result['data']['goal_evidence'][1:]), 'Original air baselines must be retained.')
                self.assertTrue(grade_task(task, bridge.snapshot())['passed'])
                self.assertFalse((await bridge.construction_status())['active'])
                self.assertEqual([e['action']['name'] for e in result['data']['trace']], ['place_batch', 'repair_batch'])
            finally:
                await agent.aclose()
                library.close()
