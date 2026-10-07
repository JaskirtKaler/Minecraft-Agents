import asyncio
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent, validate_goals
from main import execute_chat_objective, execute_cli_objective
from agent.bridge import MineflayerBridge
from practice.learning import check_backend, grade


class ModelToolAgentTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, decisions, steps=6):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        library = ExperienceLibrary(directory.name)
        self.addCleanup(library.close)
        state = {'ready': True, 'world': {'id': 'test', 'sessionId': 's', 'dimension': 'overworld'},
                 'inventory': [], 'stats': {'position': {'x': 0, 'y': 64, 'z': 0}}}

        async def execute(tool):
            if tool['name'] == 'craft':
                state['inventory'] = [{'name': tool['args']['item'], 'count': tool['args']['count']}]
            return {'success': True, 'verified': True, 'data': {}}

        bridge = SimpleNamespace(get_state=AsyncMock(side_effect=lambda: json.loads(json.dumps(state))),
                                 execute_tool=AsyncMock(side_effect=execute), cancel_task=AsyncMock(),
                                 send_chat=AsyncMock(), latest_state=state)
        responses = [json.dumps(d) if isinstance(d, dict) else d for d in decisions]
        client = SimpleNamespace(generate_response=AsyncMock(side_effect=responses))
        settings = SimpleNamespace(learning_dir=directory.name, model_step_timeout=1, agent_max_steps=steps, agent_timeout=3)
        agent = ModelToolAgent(bridge, client, settings=settings, library=library)
        self.addAsyncCleanup(agent.aclose)
        return agent, bridge, client, state

    def craft_decision(self, count=2):
        return {'type': 'act', 'goals': [{'kind': 'inventory_gain', 'item': 'wooden_sword', 'count': count}],
                'action': {'name': 'craft', 'args': {'item': 'wooden_sword', 'count': count}}}

    async def test_model_controls_crafting_and_saved_experience_is_retrieved(self):
        agent, bridge, client, state = self.fixture([self.craft_decision(), {'lesson': 'Inspect recipes and prepare prerequisites before crafting.'}])
        result = await agent.run('build 2 wood swords')
        self.assertTrue(result['verified'])
        self.assertEqual(bridge.execute_tool.call_args.args[0]['name'], 'craft')
        self.assertEqual(result['data']['goal_evidence'][0]['observed'], 2)
        # Gameplay completes before the optional inference for a lesson.
        self.assertEqual(client.generate_response.await_count, 1)
        await agent.flush_reflections()
        recalled = agent.library.recall('craft wooden swords')
        self.assertTrue(recalled[0]['verified'])
        self.assertIn('prerequisites', recalled[0]['lesson'])
        self.assertEqual(client.generate_response.await_count, 2)

    async def test_one_model_decision_can_execute_known_prerequisites_and_final_craft(self):
        decision = self.craft_decision()
        del decision['action']
        decision['actions'] = [
            {'name': 'craft', 'args': {'item': 'stick', 'count': 4}},
            {'name': 'craft', 'args': {'item': 'wooden_sword', 'count': 2}}]
        agent, bridge, client, _ = self.fixture([decision])
        result = await agent.run('craft two swords')
        self.assertTrue(result['verified'])
        self.assertEqual(result['data']['model_steps'], 1)
        self.assertEqual(result['data']['tool_calls'], 2)
        self.assertEqual(client.generate_response.await_count, 1)

    async def test_batch_tail_is_discarded_on_failure_and_replanned(self):
        decision = self.craft_decision()
        del decision['action']
        decision['actions'] = [{'name': 'equip', 'args': {'item': 'missing_axe'}},
                               {'name': 'craft', 'args': {'item': 'wooden_sword', 'count': 2}}]
        agent, bridge, _, _ = self.fixture([decision, {'type': 'blocked', 'message': 'Missing prerequisites.'}])
        bridge.execute_tool.side_effect = None
        bridge.execute_tool.return_value = {'success': False, 'data': {'error_code': 'ITEM_NOT_FOUND'}}
        result = await agent.run('craft two swords')
        self.assertFalse(result['verified'])
        self.assertEqual(bridge.execute_tool.await_count, 1)

    async def test_errors_stop_early_instead_of_spending_twenty_rounds(self):
        agent, bridge, client, _ = self.fixture([{'type': 'act', 'action': {'name': 'invented', 'args': {}}}] * 3, steps=20)
        result = await agent.run('make something')
        self.assertFalse(result['verified'])
        self.assertEqual(result['data']['model_steps'], 3)
        self.assertEqual(client.generate_response.await_count, 3)
        bridge.execute_tool.assert_not_awaited()

    async def test_graph_memory_is_retrieved_once_per_objective(self):
        declare = self.craft_decision()
        declare['action'] = {'name': 'recipes', 'args': {'item': 'wooden_sword'}}
        agent, bridge, _, _ = self.fixture([declare, self.craft_decision()])
        agent.memory = SimpleNamespace(pause=AsyncMock(), resume=lambda: None, context=AsyncMock(return_value='past discoveries'))
        result = await agent.run('craft swords')
        self.assertTrue(result['verified'])
        agent.memory.context.assert_awaited_once()

    async def test_new_objective_preempts_idle_reflection_without_losing_exact_experience(self):
        agent, bridge, client, _ = self.fixture([self.craft_decision()])
        agent.settings.agent_reflection_delay = 0
        await agent.run('craft swords')
        reflecting = asyncio.Event()
        async def waiting(*args, **kwargs):
            reflecting.set()
            await asyncio.Future()
        client.generate_response.side_effect = waiting
        await asyncio.wait_for(reflecting.wait(), .5)
        agent.settings.agent_reflection_delay = 10
        client.generate_response.side_effect = [json.dumps({'type': 'answer', 'message': 'Hello'})]
        result = await asyncio.wait_for(agent.run('hello'), .5)
        self.assertEqual(result['status'], 'answer')
        self.assertTrue(agent.library.recall('swords')[0]['verified'])
        self.assertEqual(len(agent._reflections), 1)

    async def test_repeated_identical_queries_without_progress_do_not_spin(self):
        decision = {'type': 'act', 'action': {'name': 'recipes', 'args': {'item': 'wooden_sword'}}}
        agent, bridge, _, _ = self.fixture([decision] * 5, steps=20)
        result = await agent.run('make swords')
        self.assertFalse(result['verified'])
        self.assertEqual(bridge.execute_tool.await_count, 2)
        self.assertEqual(result['data']['model_steps'], 5)

    async def test_give_objective_does_not_recraft_after_verified_toss(self):
        goal = {'kind': 'item_dropped', 'item': 'stone_sword', 'count': 1, 'recipient': 'Alex'}
        decision = {'type': 'act', 'goals': [goal], 'actions': [
            {'name': 'craft', 'args': {'item': 'stone_sword', 'count': 1}},
            {'name': 'give_item', 'args': {'item': 'stone_sword', 'count': 1, 'recipient': 'Alex'}}]}
        agent, bridge, _, state = self.fixture([decision])
        async def execute(tool):
            if tool['name'] == 'craft':
                state['inventory'] = [{'name': 'stone_sword', 'count': 1}]
                return {'success': True, 'verified': True, 'data': {}}
            state['inventory'] = []
            return {'success': True, 'verified': True, 'data': {'item': 'stone_sword', 'recipient': 'Alex',
                    'delivery': 'dropped_near_recipient', 'before': 1, 'after': 0}}
        bridge.execute_tool.side_effect = execute
        result = await agent.run('make and drop me a stone sword', 'Alex')
        self.assertTrue(result['verified'])
        self.assertEqual(result['data']['tool_calls'], 2)
        self.assertEqual(state['inventory'], [])

    async def test_missing_or_unverified_handoff_receipt_does_not_pass(self):
        goal = {'kind': 'item_dropped', 'item': 'stone_sword', 'count': 1, 'recipient': 'Alex'}
        agent, bridge, _, state = self.fixture([])
        fake = {'action': {'name': 'give_item', 'args': {'item': 'stone_sword', 'count': 1, 'recipient': 'Alex'}},
                'result': {'success': True, 'verified': False, 'data': {'before': 1, 'after': 0}}}
        self.assertFalse((await agent.check([goal], [0], state, [fake]))[0])

    async def test_chest_checks_are_grouped_and_not_repeated_after_unrelated_actions(self):
        agent, bridge, _, state = self.fixture([])
        p = {'x': 3, 'y': 64, 'z': 3}
        goals = [{'kind': 'container_gain', 'item': item, 'count': 2, 'position': p}
                 for item in ['oak_log', 'cobblestone']]
        bridge.execute_tool.side_effect = None
        bridge.execute_tool.return_value = {'success': True, 'data': {'items': {'oak_log': 2, 'cobblestone': 2}}}
        self.assertFalse((await agent.check(goals, [0, 0], state, changed='craft'))[0])
        bridge.execute_tool.assert_not_awaited()
        self.assertTrue((await agent.check(goals, [0, 0], state, changed='deposit'))[0])
        bridge.execute_tool.assert_awaited_once()

    async def test_cached_chest_state_is_not_used_as_final_completion(self):
        agent, bridge, _, state = self.fixture([])
        chest = {'kind': 'container_gain', 'item': 'oak_log', 'count': 2, 'position': {'x': 3, 'y': 64, 'z': 3}}
        sword = {'kind': 'inventory_gain', 'item': 'wooden_sword', 'count': 2}
        state['inventory'] = [{'name': 'wooden_sword', 'count': 2}]
        prior = [{'goal': chest, 'before': 0, 'observed': 2, 'met': True}]
        bridge.execute_tool.side_effect = None
        bridge.execute_tool.return_value = {'success': True, 'data': {'items': {}}}
        met, _ = await agent.check([chest, sword], [0, 0], state, previous=prior, changed='craft')
        self.assertFalse(met)
        bridge.execute_tool.assert_awaited_once()

    async def test_done_claim_without_observed_success_never_passes(self):
        declare = self.craft_decision()
        declare['action'] = {'name': 'recipes', 'args': {'item': 'wooden_sword'}}
        agent, bridge, _, _ = self.fixture([declare, {'type': 'done'}, {'lesson': 'No swords were produced.'}], steps=2)
        result = await agent.run('craft 2 wooden swords')
        self.assertFalse(result['verified'])
        self.assertFalse(agent.library.recall('swords')[0]['verified'])
        self.assertEqual(bridge.execute_tool.await_count, 1)

    async def test_goals_cannot_be_weakened_after_failure(self):
        declare = self.craft_decision()
        declare['action'] = {'name': 'recipes', 'args': {'item': 'wooden_sword'}}
        agent, bridge, _, _ = self.fixture([declare, self.craft_decision(1), self.craft_decision(), {'lesson': 'Meet the full requested quantity.'}])
        result = await agent.run('craft 2 swords')
        self.assertTrue(result['verified'])
        self.assertEqual([c.args[0]['args'].get('count') for c in bridge.execute_tool.call_args_list], [None, 2])

    async def test_new_prerequisite_does_not_block_action_or_replace_final_criteria(self):
        declare = self.craft_decision()
        declare['action'] = {'name': 'recipes', 'args': {'item': 'wooden_sword'}}
        next_action = self.craft_decision()
        next_action['goals'].append({'kind': 'inventory_gain', 'item': 'crafting_table', 'count': 1})
        agent, bridge, _, _ = self.fixture([declare, next_action, {'lesson': 'Use prerequisites without changing the objective.'}])
        result = await agent.run('craft 2 swords')
        self.assertTrue(result['verified'])
        self.assertEqual(len(result['data']['goals']), 1)
        self.assertEqual(bridge.execute_tool.await_count, 2)

    async def test_physical_action_requires_goals_before_execution(self):
        missing = {'type': 'act', 'action': {'name': 'craft', 'args': {'item': 'wooden_sword', 'count': 2}}}
        agent, bridge, _, _ = self.fixture([missing, {'lesson': 'Declare success criteria first.'}], steps=1)
        self.assertFalse((await agent.run('craft 2 swords'))['verified'])
        bridge.execute_tool.assert_not_awaited()

    async def test_respawn_or_dimension_change_stops_old_objective(self):
        agent, bridge, _, state = self.fixture([self.craft_decision(), {'lesson': 'Do not continue old plans after respawning.'}])
        calls = 0
        async def get_state():
            nonlocal calls
            calls += 1
            snapshot = json.loads(json.dumps(state))
            if calls > 1:
                snapshot['world']['sessionId'] = 'respawned'
            return snapshot
        bridge.get_state.side_effect = get_state
        result = await agent.run('craft 2 swords')
        self.assertFalse(result['verified'])
        self.assertTrue(result['data']['interrupted'])
        bridge.execute_tool.assert_not_awaited()

    async def test_read_question_can_answer_after_inspecting_without_physical_actions(self):
        agent, bridge, _, _ = self.fixture([
            {'type': 'act', 'action': {'name': 'inspect', 'args': {'item': 'stone'}}},
            {'type': 'answer', 'message': 'Stone can drop cobblestone with a suitable pickaxe.'}])
        result = await agent.run('What is stone?')
        self.assertEqual(result['status'], 'answer')
        self.assertEqual(bridge.execute_tool.await_count, 1)

    async def test_game_chat_no_longer_rejects_crafting_before_model_inference(self):
        agent, bridge, _, _ = self.fixture([self.craft_decision(), {'lesson': 'Crafting succeeded.'}])
        controller = SimpleNamespace(tool_agent=agent, memory=None)
        await execute_chat_objective(controller, bridge, 'build 2 wood swords', 'Alex', asyncio.Lock())
        self.assertTrue(controller.dialogue['last']['Alex']['verified'])
        self.assertTrue(any('wooden_sword' in c.args[0] for c in bridge.send_chat.call_args_list))

    async def test_stop_cancels_pending_inference_not_only_bot_movement(self):
        agent, bridge, client, _ = self.fixture([])
        started = asyncio.Event()

        async def wait_model(*args, **kwargs):
            started.set()
            await asyncio.Future()
        client.generate_response.side_effect = wait_model
        controller = SimpleNamespace(tool_agent=agent, memory=None)
        lock = asyncio.Lock()
        job = asyncio.create_task(execute_chat_objective(controller, bridge, 'craft 2 swords', 'Alex', lock))
        await asyncio.wait_for(started.wait(), .5)
        await execute_chat_objective(controller, bridge, 'stop', 'Alex', lock)
        await asyncio.wait_for(job, .5)
        bridge.execute_tool.assert_not_awaited()
        self.assertIsNone(controller.dialogue['active'])
        self.assertFalse(controller.dialogue['last']['Alex']['verified'])

    async def test_chat_stop_also_cancels_terminal_objective_without_stopping_controller(self):
        agent, bridge, client, _ = self.fixture([])
        started = asyncio.Event()
        async def wait_model(*args, **kwargs):
            started.set()
            await asyncio.Future()
        client.generate_response.side_effect = wait_model
        controller = SimpleNamespace(tool_agent=agent, memory=None)
        lock = asyncio.Lock()
        job = asyncio.create_task(execute_cli_objective(controller, bridge, 'craft 2 swords', lock))
        await asyncio.wait_for(started.wait(), .5)
        await execute_chat_objective(controller, bridge, 'stop', 'Alex', lock)
        await asyncio.wait_for(job, .5)
        self.assertFalse(job.cancelled())
        self.assertFalse(lock.locked())
        bridge.execute_tool.assert_not_awaited()

    async def test_curriculum_is_chosen_by_model_without_acting(self):
        agent, bridge, client, state = self.fixture([{'objective': 'craft 4 sticks', 'reason': 'Learn material processing.'}])
        choice = await agent.choose_objective(state)
        self.assertEqual(choice['objective'], 'craft 4 sticks')
        bridge.execute_tool.assert_not_awaited()
        prompt = json.loads(client.generate_response.call_args.args[0][1]['content'])
        self.assertIn('experience', prompt)
        self.assertIn('practice_progress', prompt)


class GenericBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_timeout_cancels_and_discards_correlated_request(self):
        bridge = MineflayerBridge()
        bridge.active_client = SimpleNamespace(send=AsyncMock())
        with self.assertRaises(TimeoutError):
            await bridge.execute_tool({'name': 'craft', 'args': {}}, timeout=.01)
        messages = [json.loads(call.args[0]) for call in bridge.active_client.send.call_args_list]
        self.assertEqual([message['type'] for message in messages], ['execute_tool', 'cancel_task'])
        self.assertFalse(bridge.pending_requests)


class LearningContractsTests(unittest.TestCase):
    def test_server_counts_override_model_completion(self):
        goals = [{'kind': 'inventory_gain', 'item': 'wooden_sword', 'count': 2}]
        before = {'inventory': {}, 'health': 20}
        self.assertFalse(grade(goals, before, {'inventory': {'wooden_sword': 1}, 'health': 20})[0])
        self.assertFalse(grade(goals, before, {'inventory': {'wooden_sword': 3}, 'health': 20})[0])
        self.assertTrue(grade(goals, before, {'inventory': {'wooden_sword': 2}, 'health': 20})[0])

    def test_hosted_learning_requires_explicit_cost_opt_in(self):
        check_backend(SimpleNamespace(nebius_base_url='http://localhost:11434/v1'))
        with self.assertRaises(ValueError):
            check_backend(SimpleNamespace(nebius_base_url='https://example.org/v1'))

    def test_experience_survives_restart_and_can_be_demoted_by_server_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            key = library.save('world-a', 'craft swords', {'success': True, 'verified': True}, [], 'Prepare sticks.')
            library.save('world-b', 'craft swords', {'success': False, 'verified': False}, [], 'Missing table.')
            self.assertEqual(library.progress()[0]['confirmed'], 1)
            self.assertEqual(library.progress()[0]['attempts'], 2)
            library.close()
            reopened = ExperienceLibrary(directory)
            self.assertTrue(any(episode['verified'] for episode in reopened.recall('swords')))
            reopened.update(key, 'Client verification disagreed with server.', {'success': False, 'verified': False})
            self.assertFalse(any(episode['verified'] for episode in reopened.recall('swords')))
            reopened.close()

    def test_invalid_goal_schema_is_not_a_physical_action(self):
        with self.assertRaises(ValueError):
            validate_goals([{'kind': 'inventory_gain', 'item': 'sword', 'count': -1}])


if __name__ == '__main__':
    unittest.main()
