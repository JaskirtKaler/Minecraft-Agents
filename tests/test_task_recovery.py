import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.bridge import MineflayerBridge
from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent, goal_meaning, goal_progress, quantity_contract, validate_goals, constrained_transfer
from agent.planner_schema import GOAL_REVIEW_SCHEMA
from agent.goal_contract import check_quantities, validate_requirements, collection_evidence
from agent.tool_timing import tool_timeout
from practice.learning import grade
from practice.grounded_tasks import recovery_tasks, grade_task


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, decisions, review=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        library = ExperienceLibrary(directory.name)
        self.addCleanup(library.close)
        state = {'ready': True, 'world': {'id': 'test', 'sessionId': 's', 'dimension': 'overworld'},
                 'inventory': [], 'stats': {'position': {'x': 0, 'y': 64, 'z': 0}}}
        bridge = SimpleNamespace(get_state=AsyncMock(side_effect=lambda: json.loads(json.dumps(state))),
            execute_tool=AsyncMock(), cancel_task=AsyncMock(), wait_for_operation_idle=AsyncMock(
                return_value={'busy': False, 'active': False}))
        client = SimpleNamespace(generate_response=AsyncMock(side_effect=[json.dumps(d) for d in decisions]))
        agent = ModelToolAgent(bridge, client, settings=SimpleNamespace(learning_dir=directory.name,
            model_step_timeout=1, agent_timeout=3, agent_max_steps=5, goal_review_enabled=review, goal_quantity_check_enabled=False),
            library=library, auto_reflect=False)
        self.addAsyncCleanup(agent.aclose)
        return agent, bridge, client, state

    async def test_withdrawal_only_goals_rejected_before_mutation(self):
        candidate = {'type': 'act', 'goals': [{'kind': 'inventory_gain', 'item': 'oak_sapling', 'count': 3}],
                     'actions': [{'name': 'withdraw', 'args': {'item': 'oak_sapling', 'count': 3,
                                   'position': {'x': 1, 'y': 64, 'z': 1}}}]}
        reject = {'covers_request': False, 'reason': 'Held items do not prove planted blocks.',
                  'missing_outcomes': ['New sapling blocks in the world and removal from the requested chest.']}
        agent, bridge, _, _ = self.fixture([candidate, reject, {'type': 'blocked', 'message': 'Need observed planting sites.'}], True)
        result = await agent.run('Take three oak saplings from the chest and plant them nearby')
        bridge.execute_tool.assert_not_awaited()
        self.assertFalse(result['verified'])
        self.assertEqual(result['data']['goals'], [])
        self.assertFalse(result['data']['intent_reviews'][0]['covers_request'])

    async def test_review_receives_actual_chest_direction_not_player_inventory_direction(self):
        reject = {'reason': 'Adding to the chest contradicts taking from it.',
                  'missing_outcomes': ['Source chest loss'], 'covers_request': False}
        agent, _, client, state = self.fixture([reject], True)
        goal = {'kind': 'container_gain', 'item': 'oak_sapling', 'count': 3,
                'position': {'x': 1, 'y': 64, 'z': 1}}
        review = await agent.review_goals('Take three saplings FROM the chest and plant them', '', [goal], state, [], lambda _: None)
        prompt = json.loads(client.generate_response.call_args.args[0][1]['content'])
        self.assertIn('ADD TO (chest AFTER - BEFORE)', prompt['executable_meanings'][0])
        self.assertFalse(review['covers_request'])
        self.assertEqual(list(GOAL_REVIEW_SCHEMA['properties']), ['reason', 'missing_outcomes', 'covers_request'])

    def test_remaining_source_quantity_uses_chest_not_held_items(self):
        goal = {'kind': 'container_loss', 'item': 'oak_sapling', 'count': 3,
                'position': {'x': 1, 'y': 64, 'z': 1}}
        progress = goal_progress([{'goal': goal, 'before': 7, 'observed': 4, 'met': True}])[0]
        self.assertEqual(progress['completed'], 3)
        self.assertEqual(progress['remaining'], 0)
        self.assertIn('REMOVE FROM', goal_meaning(goal))
        progress = goal_progress([{'goal': goal, 'before': 7, 'observed': 3, 'met': False}])[0]
        self.assertEqual(progress['overshoot'], 1)

    def test_quantity_contract_distinguishes_intermediate_and_final_inventory(self):
        state = {'inventory': [{'name': 'oak_log', 'count': 6}]}
        delivery = {'kind': 'container_gain', 'item': 'oak_log', 'count': 24,
                    'position': {'x': 2, 'y': 64, 'z': 3}}
        holding = {'kind': 'inventory_gain', 'item': 'oak_log', 'count': 24, 'comparison': 'at_least'}
        contract = quantity_contract([delivery, holding], state)['oak_log']
        self.assertEqual(contract['minimum_final_held'], 30)
        self.assertEqual(contract['minimum_net_new_held_plus_transferred'], 48)
        contract = quantity_contract([delivery, {'kind': 'inventory_at_least', 'item': 'oak_log', 'count': 6}], state)['oak_log']
        self.assertEqual(contract['minimum_final_held'], 6)
        self.assertEqual(contract['minimum_net_new_held_plus_transferred'], 24)
        loss = {**delivery, 'kind': 'container_loss'}
        with self.assertRaises(ValueError):
            validate_goals([delivery, loss])
        contract = quantity_contract([delivery, delivery, holding], state)['oak_log']
        self.assertEqual(contract['minimum_net_new_held_plus_transferred'], 48, 'Duplicate predicates are not duplicate transfers.')

    async def test_legacy_memory_keeps_raw_result_but_exposes_verification_scope(self):
        agent, _, _, _ = self.fixture([])
        goals = [{'kind': 'inventory_gain', 'item': 'oak_sapling', 'count': 3}]
        result = {'success': True, 'verified': True, 'data': {'goals': goals}}
        key = agent.library.save('test', 'Take saplings and plant them', result, [], 'Old completion label.')
        recalled = agent.library.recall('plant saplings')[0]
        self.assertTrue(recalled['verified'], 'Do not silently rewrite historical evidence.')
        self.assertEqual(recalled['checked_goals'], goals)
        self.assertFalse(recalled['goal_review_approved'])
        result['data']['intent_reviews'] = [{'goals': goals, 'covers_request': True}]
        agent.library.update(key, 'Reviewed completion.', result)
        self.assertTrue(agent.library.recall('plant saplings')[0]['goal_review_approved'])

    def test_transfer_rejects_overshoot_without_silently_changing_model_count(self):
        p = {'x': 2, 'y': 64, 'z': 3}
        goal = {'kind': 'container_loss', 'item': 'oak_sapling', 'count': 3, 'position': p}
        evidence = [{'goal': goal, 'before': 7, 'observed': 6, 'met': False}]
        action = {'name': 'withdraw', 'args': {'item': 'oak_sapling', 'count': 3, 'position': p}}
        with self.assertRaises(ValueError):
            constrained_transfer(action, [goal], [7], evidence)
        action['args']['count'] = 2
        tool = constrained_transfer(action, [goal], [7], evidence)
        self.assertEqual(tool['args']['count'], 2)
        self.assertEqual(tool['container_constraint']['minimum'], 4)

    def test_independent_quantities_reject_extra_held_delivery_and_missing_source(self):
        p = {'x': 2, 'y': 64, 'z': 3}
        state = {'inventory': [{'name': 'oak_log', 'count': 6}]}
        requirements = [{'outcome': 'collect_new', 'item': 'oak_log', 'count': 24, 'comparison': 'at_least'},
                        {'outcome': 'container_add', 'item': 'oak_log', 'count': 24, 'comparison': 'exact'}]
        delivery = {'kind': 'container_gain', 'item': 'oak_log', 'count': 24, 'position': p}
        bad = [delivery, {'kind': 'inventory_gain', 'item': 'oak_log', 'count': 24, 'comparison': 'at_least'}]
        with self.assertRaises(ValueError):
            check_quantities(requirements, bad, {'oak_log': 6}, quantity_contract(bad, state))
        good = [delivery, {'kind': 'inventory_at_least', 'item': 'oak_log', 'count': 6}]
        check_quantities(requirements, good, {'oak_log': 6}, quantity_contract(good, state))
        inflated = [delivery, {'kind': 'inventory_at_least', 'item': 'oak_log', 'count': 30}]
        with self.assertRaises(ValueError):
            check_quantities(requirements, inflated, {'oak_log': 6}, quantity_contract(inflated, state))
        check_quantities(requirements, [delivery], {'oak_log': 6}, quantity_contract([delivery], state))
        duplicate = {**delivery, 'position': {'z': 3, 'y': 64, 'x': 2}}
        check_quantities(requirements, [delivery, duplicate], {'oak_log': 6}, quantity_contract([delivery, duplicate], state))
        self.assertFalse(collection_evidence(requirements, [
            {'action': {'name': 'withdraw', 'args': {'item': 'oak_log'}}, 'inventory_change': {'oak_log': 24}},
            {'action': {'name': 'mine_logs', 'args': {'item': 'oak_log'}}, 'inventory_change': {'oak_log': 20}}
        ])[0]['met'], 'Old chest stock and insufficient fresh gathering do not fulfill collect_new.')
        source = [{'outcome': 'container_remove', 'item': 'oak_sapling', 'count': 3, 'comparison': 'exact'}]
        with self.assertRaises(ValueError):
            check_quantities(source, [{'kind': 'container_loss', 'item': 'oak_sapling', 'count': 1, 'position': p}], {}, {})
        with self.assertRaises(ValueError):
            validate_requirements({'requirements': [{'outcome': 'container_remove', 'item': 'oak_sapling',
                                                      'count': True, 'comparison': 'exact'}]})

    async def test_interpretation_never_sees_candidate_and_blocks_bad_goals_before_mutation(self):
        p = {'x': 2, 'y': 64, 'z': 3}
        candidate = {'type': 'act', 'goals': [
            {'kind': 'container_gain', 'item': 'oak_log', 'count': 24, 'position': p},
            {'kind': 'inventory_gain', 'item': 'oak_log', 'count': 24, 'comparison': 'at_least'}],
            'actions': [{'name': 'mine_logs', 'args': {'item': 'oak_log', 'count': 24}}]}
        requirements = [{'outcome': 'collect_new', 'item': 'oak_log', 'count': 24, 'comparison': 'at_least'},
                        {'outcome': 'container_add', 'item': 'oak_log', 'count': 24, 'comparison': 'exact'}]
        agent, bridge, client, state = self.fixture([candidate, {'requirements': requirements},
            {'type': 'blocked', 'message': 'Need corrected final criteria.'}], True)
        agent.settings.goal_quantity_check_enabled = True
        state['inventory'] = [{'name': 'oak_log', 'count': 6}]
        bridge.execute_tool.return_value = {'success': True, 'data': {'items': {}}}
        result = await agent.run('Get 24 additional oak logs and put 24 in the chest')
        self.assertFalse(result['verified'])
        self.assertEqual(result['data']['requested_quantities'], requirements)
        self.assertTrue(all(call.args[0]['name'] == 'container' for call in bridge.execute_tool.call_args_list))
        interpretation_prompt = json.loads(client.generate_response.call_args_list[1].args[0][1]['content'])
        self.assertEqual(set(interpretation_prompt), {'objective', 'requester'})

    async def test_overshoot_is_rejected_then_model_selects_remaining_quantity(self):
        p = {'x': 2, 'y': 64, 'z': 3}
        goal = {'kind': 'container_loss', 'item': 'oak_sapling', 'count': 3, 'position': p}
        def withdraw(n, declare=False):
            value = {'type': 'act', 'actions': [{'name': 'withdraw', 'args': {'item': 'oak_sapling', 'count': n, 'position': p}}]}
            if declare:
                value['goals'] = [goal]
            return value
        agent, bridge, _, state = self.fixture([withdraw(1, True), withdraw(3), withdraw(2)])
        stored = 7
        async def execute(tool):
            nonlocal stored
            if tool['name'] == 'container':
                return {'success': True, 'data': {'items': {'oak_sapling': stored}}}
            stored -= tool['args']['count']
            state['inventory'] = [{'name': 'oak_sapling', 'count': 7 - stored}]
            return {'success': True, 'verified': True, 'data': {}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('Take exactly three saplings from the chest')
        self.assertTrue(result['verified'], result['message'])
        self.assertEqual([c.args[0]['args']['count'] for c in bridge.execute_tool.call_args_list if c.args[0]['name'] == 'withdraw'], [1, 2])
        self.assertTrue(any('overshoot' in e['planner_error'] for e in result['data']['planner_errors']))
        self.assertIsNone(result['data']['in_flight_action'])

    async def test_delivery_from_existing_stock_does_not_prove_new_collection(self):
        p = {'x': 2, 'y': 64, 'z': 3}
        goal = {'kind': 'container_gain', 'item': 'oak_log', 'count': 2, 'position': p}
        candidate = {'type': 'act', 'goals': [goal], 'actions': [
            {'name': 'deposit', 'args': {'item': 'oak_log', 'count': 2, 'position': p}}]}
        requirements = [{'outcome': 'collect_new', 'item': 'oak_log', 'count': 2, 'comparison': 'exact'},
                        {'outcome': 'container_add', 'item': 'oak_log', 'count': 2, 'comparison': 'exact'}]
        review = {'covers_request': True, 'reason': 'Delivery is final; new collection is separately checked.', 'missing_outcomes': []}
        agent, bridge, _, state = self.fixture([candidate, {'requirements': requirements}, review,
            {'type': 'act', 'actions': [{'name': 'mine_logs', 'args': {'item': 'oak_log', 'count': 2}}]}], True)
        agent.settings.goal_quantity_check_enabled = True
        state['inventory'] = [{'name': 'oak_log', 'count': 5}]
        stored = 0
        async def execute(tool):
            nonlocal stored
            if tool['name'] == 'container':
                return {'success': True, 'data': {'items': {'oak_log': stored}}}
            if tool['name'] == 'deposit':
                stored = 2
                state['inventory'][0]['count'] = 3
            else:
                state['inventory'][0]['count'] += 2
            return {'success': True, 'verified': True, 'data': {}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('Collect exactly two new oak logs and deliver two to the chest')
        self.assertTrue(result['verified'], result['message'])
        self.assertEqual(result['data']['tool_calls'], 2, 'Must not stop at delivery of existing stock.')
        self.assertEqual(result['data']['collection_evidence'][0]['observed'], 2)

    async def test_full_planting_contract_does_not_finish_at_withdrawal(self):
        p = {'x': 2, 'y': 65, 'z': 1}
        chest = {'x': 1, 'y': 64, 'z': 1}
        goals = [{'kind': 'container_loss', 'item': 'oak_sapling', 'count': 1, 'position': chest},
                 {'kind': 'block_is', 'block': 'oak_sapling', 'position': p, 'must_change': True}]
        act = {'type': 'act', 'goals': goals, 'actions': [
            {'name': 'withdraw', 'args': {'item': 'oak_sapling', 'count': 1, 'position': chest}},
            {'name': 'place', 'args': {'item': 'oak_sapling', 'position': p}}]}
        approve = {'covers_request': True, 'reason': 'Both source removal and new planting are covered.', 'missing_outcomes': []}
        agent, bridge, _, state = self.fixture([act, approve], True)
        stored, planted = 3, False
        async def execute(tool):
            nonlocal stored, planted
            name = tool['name']
            if name == 'container':
                return {'success': True, 'data': {'items': {'oak_sapling': stored}}}
            if name == 'inspect':
                return {'success': True, 'data': {'isBlock': True, 'plantedBy': [{'soils': ['dirt'], 'preparation_soils': []}],
                    'name': 'oak_sapling' if planted else 'air', 'below': {'name': 'dirt'}}}
            if name == 'withdraw':
                stored -= 1
                state['inventory'] = [{'name': 'oak_sapling', 'count': 1}]
                return {'success': True, 'verified': True, 'data': {}}
            planted = True
            state['inventory'] = []
            return {'success': True, 'verified': True, 'data': {'name': 'oak_sapling', 'position': p}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('Take one oak sapling from the chest and plant it')
        self.assertTrue(result['verified'], result['message'])
        self.assertEqual(result['data']['tool_calls'], 2)
        self.assertTrue(planted)
        self.assertEqual(stored, 2)

    async def test_discovery_runs_before_freezing_guessed_targets(self):
        candidate = {'type': 'act', 'goals': [{'kind': 'block_is', 'block': 'invented',
            'position': {'x': 0, 'y': 999, 'z': 0}}], 'actions': [{'name': 'planting_sites', 'args': {'item': 'oak_sapling'}}]}
        agent, bridge, client, _ = self.fixture([candidate, {'type': 'blocked', 'message': 'Need clarification.'}], True)
        bridge.execute_tool.return_value = {'success': True, 'data': {'sites': []}}
        result = await agent.run('Plant saplings')
        self.assertEqual(result['data']['goals'], [])
        self.assertEqual(client.generate_response.await_count, 2, 'Discovery should not spend a coverage review.')
        bridge.execute_tool.assert_awaited_once()

    def gather_decision(self, count, goals=None):
        decision = {'type': 'act', 'actions': [{'name': 'mine_logs', 'args': {'item': 'oak_log', 'count': count}}]}
        if goals:
            decision['goals'] = goals
        return decision

    async def test_settled_timeout_keeps_baseline_and_replans_remaining_quantity(self):
        goals = [{'kind': 'inventory_gain', 'item': 'oak_log', 'count': 16}]
        agent, bridge, client, state = self.fixture([self.gather_decision(16, goals), self.gather_decision(8)])
        async def execute(tool):
            if bridge.execute_tool.await_count == 1:
                state['inventory'] = [{'name': 'oak_log', 'count': 8}]
                return {'success': False, 'data': {'error_code': 'TASK_TIMEOUT', 'operation_id': 'expired', 'after': 8}}
            state['inventory'] = [{'name': 'oak_log', 'count': 16}]
            return {'success': True, 'verified': True, 'data': {}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('Mine sixteen additional oak logs')
        self.assertTrue(result['verified'], result['message'])
        bridge.wait_for_operation_idle.assert_awaited_once_with('expired')
        self.assertTrue(result['data']['trace'][0]['progress_made'])
        self.assertEqual(result['data']['goal_evidence'][0]['before'], 0)
        prompt = json.loads(client.generate_response.call_args.args[0][1]['content'])
        self.assertEqual(prompt['current_inventory']['oak_log'], 8)

    async def test_unsettled_timeout_and_player_stop_never_start_another_action(self):
        goals = [{'kind': 'inventory_gain', 'item': 'oak_log', 'count': 16}]
        for code in ['TASK_TIMEOUT', 'CANCELLED']:
            agent, bridge, _, _ = self.fixture([self.gather_decision(16, goals), self.gather_decision(8)])
            bridge.execute_tool.return_value = {'success': False, 'data': {'error_code': code, 'operation_id': 'expired'}}
            bridge.wait_for_operation_idle.side_effect = TimeoutError('Still settling')
            result = await agent.run('Mine sixteen additional oak logs')
            self.assertFalse(result['verified'])
            self.assertTrue(result['data']['interrupted'])
            self.assertEqual(bridge.execute_tool.await_count, 1)
            if code == 'CANCELLED':
                bridge.wait_for_operation_idle.assert_not_awaited()

    async def test_session_change_after_settlement_stops_continuation(self):
        goals = [{'kind': 'inventory_gain', 'item': 'oak_log', 'count': 16}]
        agent, bridge, _, state = self.fixture([self.gather_decision(16, goals), self.gather_decision(8)])
        async def execute(tool):
            state['world']['sessionId'] = 'different'
            return {'success': False, 'data': {'error_code': 'TASK_TIMEOUT', 'operation_id': 'expired'}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('Mine sixteen logs')
        self.assertFalse(result['verified'])
        self.assertTrue(result['data']['interrupted'])
        self.assertEqual(bridge.execute_tool.await_count, 1)


class TimingBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpc_budget_exceeds_node_budget_and_idle_handshake_matches_operation(self):
        bridge = MineflayerBridge()
        bridge._request = AsyncMock(return_value={})
        tool = {'name': 'mine_logs', 'args': {'count': 16}}
        self.assertEqual(tool_timeout(tool), 218)
        await bridge.execute_tool(tool)
        self.assertEqual(bridge._request.call_args.kwargs['timeout'], 238)
        bridge._request.side_effect = [
            {'busy': True, 'active': False, 'operation_id': 'a'}, {'busy': False, 'active': False}]
        self.assertFalse((await bridge.wait_for_operation_idle('a'))['busy'])
        bridge._request.side_effect = None
        bridge._request.return_value = {'busy': True, 'active': True, 'operation_id': 'other'}
        with self.assertRaises(RuntimeError):
            await bridge.wait_for_operation_idle('a')
        with self.assertRaises(RuntimeError):
            await bridge.wait_for_operation_idle(None)

    def test_server_audit_rejects_withdrawal_without_planting(self):
        task = recovery_tasks(1)[0]
        before = {'health': 20, 'chest': {'oak_sapling': 7}, 'blocks': {'1,65,1': 'air'}}
        after = {'health': 20, 'chest': {'oak_sapling': 4}, 'blocks': before['blocks'], 'inventory': {'oak_sapling': 3}}
        self.assertTrue(grade_task(task, before, after))
        p = {'x': 1, 'y': 65, 'z': 1}
        source = {'kind': 'container_loss', 'item': 'oak_sapling', 'count': 3, 'position': p}
        self.assertTrue(grade([source], {'health': 20, 'chest': {'oak_sapling': 7}, 'chest_position': p},
            {'health': 20, 'containers': {'1,65,1': {'oak_sapling': 4}}})[0])

    def test_server_audit_does_not_count_existing_logs_as_new_collection(self):
        task = recovery_tasks(1)[1]
        before = {'health': 20, 'inventory': {'oak_log': 4}, 'chest': {}}
        after = {'health': 20, 'inventory': {}, 'chest': {'oak_log': 24}}
        self.assertIn('additional collected', '; '.join(grade_task(task, before, after)))
        after['inventory'] = {'oak_log': 4}
        self.assertEqual(grade_task(task, before, after), [])


if __name__ == '__main__':
    unittest.main()
