"""Runtime grounding/context regressions; no model calls or normal world writes."""
import copy
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.experience import ExperienceLibrary
from agent.tool_agent import ModelToolAgent, compact, compact_experience, repeated_without_progress, compiled_spatial_contracts
from agent.regions import compile_goal
from practice.construction import SimulatedConstructionBridge, make_task, position


class ConstructionContextTests(unittest.TestCase):
    def test_compiled_review_exposes_degenerate_perimeters_as_solid_not_hollow(self):
        goal = {'kind': 'regions_match', 'regions': [
            {'min': {'x': -4, 'y': 69, 'z': z}, 'max': {'x': 0, 'y': 70, 'z': z},
             'block': 'cobblestone', 'mode': 'perimeter_xz'} for z in range(-12, -7)],
            'exceptions': [{'position': {'x': -2, 'y': y, 'z': -8}, 'block': 'air'} for y in (69, 70)],
            'must_change': True}
        original = copy.deepcopy(goal)
        contract = compiled_spatial_contracts([goal])[0]
        self.assertTrue(contract['complete'])
        self.assertEqual(contract['required_blocks']['air']['required_cell_count'], 2)
        self.assertEqual(contract['required_blocks']['cobblestone']['required_cell_count'], 48)
        expanded = {}
        for block, facts in contract['required_blocks'].items():
            for box in facts['boxes']:
                expanded.update(compile_goal({'kind': 'regions_match', 'regions': [{**box, 'block': block, 'mode': 'solid'}]}))
        self.assertEqual(expanded, compile_goal(goal))
        self.assertEqual(expanded[(-2, 69, -10)], 'cobblestone')
        self.assertEqual(goal, original)

    def test_compiled_review_bounds_boxes_without_hiding_omissions(self):
        goal = {'kind': 'regions_match', 'regions': [
            {'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 4, 'y': 64, 'z': 0},
             'block': 'cobblestone', 'mode': 'solid'}],
            'exceptions': [{'position': {'x': x, 'y': 64, 'z': 0}, 'block': 'air'} for x in (1, 3)]}
        summary = compiled_spatial_contracts([goal, goal], max_boxes=2)
        self.assertEqual(sum(len(f['boxes']) for c in summary for f in c['required_blocks'].values()), 2)
        self.assertFalse(summary[0]['complete'])
        self.assertFalse(summary[1]['complete'])
        self.assertEqual(summary[1]['required_blocks']['air']['required_cell_count'], 2)
        self.assertEqual(summary[1]['required_blocks']['cobblestone']['required_cell_count'], 3)
        self.assertIn('unconstrained', summary[1]['semantics'])

    def test_full_batch_intent_is_not_silently_reduced_to_eight_positions(self):
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone',
                  'positions': [{'x': x, 'y': 78, 'z': -16} for x in range(64)]}}
        original = copy.deepcopy(action)
        result = compact(action)
        self.assertEqual(result, action)
        result['args']['positions'][0]['z'] = 16
        self.assertEqual(action, original)

    def test_all_batch_receipt_coordinates_are_kept_without_navigation_noise(self):
        positions = [{'x': x, 'y': 78, 'z': -7} for x in range(40)]
        entry = {'action': {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': positions}},
                 'result': {'success': False, 'verified': False, 'data': {
                     'error_code': 'BATCH_NOT_COMPLETE', 'partial': {
                         'placements': [{'position': p, 'before': 'air', 'after': 'cobblestone',
                                         'verified': True, 'route_cost': 3.14, 'stance': {'x': 1}} for p in positions],
                         'skipped': [{'position': {'x': 40, 'y': 78, 'z': -7},
                                      'reason': 'occupied_unrelated', 'observed': 'grass_block'}],
                         'receipts': [{'raw': 'independent audit retains full receipts'}],
                         'complete': False}}}}
        original = copy.deepcopy(entry)
        packed = compact(entry)
        summary = packed['result']['data']['partial']
        self.assertEqual(summary['placements']['count'], 40)
        self.assertTrue(summary['placements']['complete'])
        self.assertEqual([c['position'] for c in summary['placements']['cells']], positions)
        self.assertEqual(summary['skipped']['cells'][0]['observed'], 'grass_block')
        self.assertNotIn('route_cost', summary['placements']['cells'][0])
        self.assertNotIn('receipts', summary)
        self.assertIn('receipts', entry['result']['data']['partial'])
        self.assertFalse(packed['result']['verified'])
        self.assertEqual(entry, original)

    def test_dig_clearances_and_noop_cells_remain_distinct(self):
        entry = {'action': {'name': 'dig_batch', 'args': {'positions': [{'x': 1, 'y': 78, 'z': -9}]}},
                 'result': {'success': True, 'data': {
                     'cleared': [{'position': {'x': 1, 'y': 78, 'z': -9}, 'before': 'dirt', 'after': 'air'}],
                     'skipped': [{'position': {'x': 2, 'y': 78, 'z': -9}, 'observed': 'air', 'reason': 'already_air'}]}}}
        packed = compact(entry)['result']['data']
        self.assertEqual(packed['cleared']['cells'][0]['before'], 'dirt')
        self.assertEqual(packed['skipped']['cells'][0]['reason'], 'already_air')

    def test_experience_preserves_outcome_lessons_not_old_geometry_authority(self):
        goal = {'kind': 'regions_match', 'regions': [{'min': {'x': 11, 'y': 78, 'z': -16},
                 'max': {'x': 20, 'y': 78, 'z': -7}, 'block': 'cobblestone', 'mode': 'solid'}]}
        episode = {'objective': 'build a house', 'verified': False, 'lesson': 'Do not dig the support layer.',
                   'checked_goals': [goal], 'goal_review_approved': True,
                   'tools_used': ['inspect_region', 'dig_batch', 'inspect_region'], 'historical_world': 'old'}
        original = copy.deepcopy(episode)
        packed = compact_experience([episode])[0]
        self.assertFalse(packed['verified'])
        self.assertTrue(packed['goal_review_approved'])
        self.assertEqual(packed['checked_goal_summary'][0]['cell_count'], 100)
        self.assertNotIn('regions', packed['checked_goal_summary'][0])
        self.assertTrue(packed['historical_coordinates_are_not_current_targets'])
        self.assertEqual(packed['tools_used'], ['inspect_region', 'dig_batch'])
        self.assertEqual(episode, original)
        invalid = {**episode, 'checked_goals': [{'kind': 'regions_match', 'regions': []}]}
        self.assertTrue(compact_experience([invalid])[0]['checked_goal_summary'][0]['historical_goal_summary_unavailable'])

    def test_inspection_does_not_reset_identical_failed_physical_action_detection(self):
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [{'x': 1, 'y': 78, 'z': -17}]}}
        read = {'name': 'inspect_region', 'args': {'min': {'x': 1, 'y': 78, 'z': -16}, 'max': {'x': 1, 'y': 78, 'z': -7}}}
        trace = [{'action': action, 'progress_made': False}, {'action': read, 'progress_made': False},
                 {'action': copy.deepcopy(action), 'progress_made': False}, {'action': read, 'progress_made': False}]
        self.assertTrue(repeated_without_progress(action, trace))
        trace[-1]['progress_made'] = True
        self.assertFalse(repeated_without_progress(action, trace))


class ConstructionLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_prerequisite_changes_outside_goal_cells_are_not_no_effect(self):
        state = {'ready': True, 'world': {'id': 'test', 'sessionId': 's', 'dimension': 'overworld'},
                 'inventory': [], 'stats': {'position': {'x': 0, 'y': 64, 'z': 0}}}
        async def execute(tool):
            p = tool['args']['position']
            return {'success': True, 'verified': False, 'data': {
                'before': {'position': p, 'name': 'dirt', 'properties': {}},
                'after': {'position': p, 'name': 'farmland', 'properties': {'moisture': 0}}}}
        bridge = SimpleNamespace(get_state=AsyncMock(return_value=state), execute_tool=execute, cancel_task=AsyncMock())
        goal = {'kind': 'inventory_at_least', 'item': 'wheat', 'count': 1}
        decisions = [{'type': 'act', 'goals': [goal], 'action': {'name': 'use_on_block',
                     'args': {'item': 'stone_hoe', 'position': {'x': x, 'y': 63, 'z': 0}}}} for x in range(1, 4)]
        decisions.append({'type': 'blocked', 'message': 'Soil is prepared; the final crop outcome is not complete.'})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=6,
                                       agent_timeout=10, goal_review_enabled=False, agent_max_no_progress_actions=2)
            client = SimpleNamespace(generate_response=AsyncMock(side_effect=[json.dumps(d) for d in decisions]))
            agent = ModelToolAgent(bridge, client, settings=settings, library=library, auto_reflect=False)
            try:
                result = await agent.run('Prepare soil and obtain wheat.')
                self.assertFalse(result['verified'], 'Prerequisite effects must never prove the final goal.')
                self.assertEqual(result['data']['model_steps'], 4)
                self.assertEqual(result['data']['tool_calls'], 3)
                self.assertTrue(all(e['observed_mutation_effect'] and e['progress_made'] for e in result['data']['trace']))
            finally:
                await agent.aclose()
                library.close()

    async def run_mock(self, bridge, reply):
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=6,
                                       agent_timeout=10, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                return await agent.run(bridge.task.objective)
            finally:
                await agent.aclose()
                library.close()

    async def test_valid_discovery_draft_survives_inspection_but_does_not_authorize_actions(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        goal = {'kind': 'regions_match', 'regions': [{
            'min': position(task.minimum),
            'max': position((task.maximum[0], task.minimum[1], task.maximum[2])),
            'block': 'cobblestone', 'mode': 'solid'}], 'must_change': True}
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}
        calls = 0
        async def reply(messages, **kwargs):
            nonlocal calls
            calls += 1
            payload = json.loads(messages[1]['content'])
            if calls == 1:
                return json.dumps({'type': 'act', 'goals': [goal], 'actions': [
                    {'name': 'inspect_region', 'args': {'min': goal['regions'][0]['min'], 'max': goal['regions'][0]['max']}}]})
            if calls == 2:
                self.assertEqual(payload['goals'], [])
                self.assertEqual(payload['unaccepted_draft_goals'], [goal])
                self.assertIn('UNACCEPTED DISCOVERY DRAFT', payload['construction_knowledge']['goal_status'])
                self.assertNotIn('rejected_draft_goals', payload)
                self.assertFalse(any(c['tool']['name'] == 'place_batch' for c in bridge.calls))
                return json.dumps({'type': 'act', 'goals': [goal], 'actions': [action]})
            self.assertEqual(payload['goals'], [goal])
            return json.dumps({'type': 'act', 'actions': [action]})
        result = await self.run_mock(bridge, reply)
        self.assertTrue(result['verified'])
        self.assertEqual(calls, 3)
        self.assertEqual([entry['action']['name'] for entry in result['data']['trace']], ['inspect_region', 'place_batch'])

    async def test_unreadable_rejected_draft_is_unknown_and_can_be_corrected(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        good = {'kind': 'regions_match', 'regions': [{
            'min': position(task.minimum),
            'max': position((task.maximum[0], task.minimum[1], task.maximum[2])),
            'block': 'cobblestone', 'mode': 'solid'}], 'must_change': True}
        far = position((task.minimum[0] + 200, task.minimum[1], task.minimum[2]))
        bad = {'kind': 'regions_match', 'regions': [{'min': far, 'max': far, 'block': 'cobblestone', 'mode': 'solid'}],
               'must_change': True}
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}
        calls = 0
        async def reply(messages, **kwargs):
            nonlocal calls
            calls += 1
            payload = json.loads(messages[1]['content'])
            if calls == 1:
                return json.dumps({'type': 'act', 'goals': [bad], 'actions': [action]})
            if calls == 2:
                self.assertEqual(payload['goals'], [])
                self.assertEqual(payload['construction_knowledge']['categories']['unknown']['count'], 1)
                self.assertIn('unloaded or unknown', payload['draft_observation_errors'][0]['draft_observation_error'])
                self.assertFalse(any(c['tool']['name'] == 'place_batch' for c in bridge.calls))
                return json.dumps({'type': 'act', 'goals': [good], 'actions': [action]})
            return json.dumps({'type': 'act', 'actions': [action]})
        result = await self.run_mock(bridge, reply)
        self.assertTrue(result['verified'])
        self.assertEqual(calls, 3)
        self.assertTrue(any('draft_observation_error' in error for error in result['data']['planner_errors']))

    async def test_goal_reviewer_receives_actual_compiled_air_and_solid_coordinates(self):
        task = make_task('shelter', 45)
        bridge = SimulatedConstructionBridge(task)
        minimum, maximum = task.minimum, (task.maximum[0], task.minimum[1] + 3, task.maximum[2])
        # Deliberately wrong filled room, with only its doorway left air.
        goal = {'kind': 'regions_match', 'regions': [
            {'min': position(minimum), 'max': position(maximum), 'block': 'cobblestone', 'mode': 'solid'}],
            'exceptions': [{'position': position(p), 'block': 'air'} for p, block in task.expected.items()
                           if block == 'air' and p[2] == maximum[2]], 'must_change': True}
        async def respond(messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            facts = payload['compiled_spatial_contracts'][0]['required_blocks']
            self.assertEqual(facts['air']['required_cell_count'], 2)
            self.assertEqual(facts['cobblestone']['required_cell_count'], len(task.expected) - 2)
            self.assertIn('Doorway-only air does NOT cover an empty room', messages[0]['content'])
            return json.dumps({'covers_request': False, 'reason': 'Filled interior conflicts with the requested empty room.',
                               'missing_outcomes': ['Required air throughout the interior.']})
        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.client = SimpleNamespace(generate_response=respond)
        agent.settings = SimpleNamespace(model_step_timeout=2)
        review = await agent.review_goals(task.objective, 'ConstructionPractice', [goal], bridge.state(), [], lambda _: None)
        self.assertFalse(review['covers_request'])
        self.assertEqual(bridge.calls, [])

    async def test_rejected_spatial_draft_keeps_fresh_material_facts_without_authorizing_actions(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        goal = {'kind': 'regions_match', 'regions': [{
            'min': {'x': task.minimum[0], 'y': task.minimum[1], 'z': task.minimum[2]},
            'max': {'x': task.maximum[0], 'y': task.minimum[1], 'z': task.maximum[2]},
            'block': 'cobblestone', 'mode': 'solid'}], 'must_change': False}
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}
        planning_calls = 0
        async def respond(messages, **kwargs):
            nonlocal planning_calls
            payload = json.loads(messages[1]['content'])
            if 'current_inventory' not in payload:
                return json.dumps({'requirements': [{'outcome': 'place_new', 'item': 'cobblestone',
                                                     'count': len(task.expected), 'comparison': 'exact'}]})
            if 'proposed_goals' in payload:
                return json.dumps({'covers_request': True, 'reason': 'The entire requested new floor is covered.', 'missing_outcomes': []})
            planning_calls += 1
            if planning_calls == 1:
                return json.dumps({'type': 'act', 'goals': [goal], 'actions': [action]})
            if planning_calls == 2:
                facts = payload['construction_knowledge']
                self.assertIn('REJECTED DRAFT', facts['goal_status'])
                self.assertEqual(payload['goals'], [])
                self.assertEqual(facts['categories']['missing_nonair']['count'], len(task.expected))
                self.assertEqual(facts['materials']['cobblestone']['minimum_additional_same_name_items'], 0)
                self.assertFalse(any(c['tool']['name'] == 'place_batch' for c in bridge.calls))
                return json.dumps({'type': 'act', 'goals': [{**goal, 'must_change': True}], 'actions': [action]})
            self.assertEqual(payload['construction_knowledge']['goal_status'], 'accepted and frozen')
            return json.dumps({'type': 'act', 'actions': [action]})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=5,
                                       agent_timeout=10, goal_review_enabled=True)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=respond), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
                self.assertTrue(result['verified'])
                self.assertEqual(result['data']['tool_calls'], 1)
                self.assertEqual(planning_calls, 3)
            finally:
                await agent.aclose()
                library.close()

    async def test_first_region_mutation_is_chosen_after_grounding_not_before(self):
        task = make_task('negative_floor', 42)
        bridge = SimulatedConstructionBridge(task)
        floor = min(p[1] for p in task.expected)
        goal = {'kind': 'regions_match', 'regions': [{
            'min': {'x': task.minimum[0], 'y': floor, 'z': task.minimum[2]},
            'max': {'x': task.maximum[0], 'y': floor, 'z': task.maximum[2]}, 'block': 'cobblestone', 'mode': 'solid'}]}
        action = {'name': 'place_batch', 'args': {'item': 'cobblestone', 'positions': [position(p) for p in task.expected]}}
        calls = 0
        async def respond(messages, **kwargs):
            nonlocal calls
            calls += 1
            payload = json.loads(messages[1]['content'])
            if calls == 1:
                self.assertNotIn('construction_knowledge', payload)
                return json.dumps({'type': 'act', 'goals': [goal], 'actions': [action]})
            self.assertFalse(any(c['tool']['name'] in {'dig', 'dig_batch', 'place', 'place_batch'} for c in bridge.calls))
            self.assertEqual(payload['goals'], [goal])
            facts = payload['construction_knowledge']
            self.assertEqual(facts['categories']['missing_nonair']['count'], len(task.expected))
            self.assertEqual(facts['categories']['natural_obstructions']['count'], 0)
            self.assertEqual(facts['materials']['cobblestone']['held_same_name_items'], task.inventory['cobblestone'])
            return json.dumps({'type': 'act', 'actions': [action]})
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=4,
                                       agent_timeout=10, goal_review_enabled=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=respond), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run(task.objective)
                self.assertTrue(result['verified'])
                self.assertEqual(result['data']['model_steps'], 2)
                self.assertEqual(result['data']['trace'][0]['action'], action)
            finally:
                await agent.aclose()
                library.close()

    async def test_different_no_effect_actions_stop_without_spending_full_round_budget(self):
        state = {'ready': True, 'world': {'id': 'test', 'sessionId': 's', 'dimension': 'overworld'},
                 'inventory': [], 'stats': {'position': {'x': 0, 'y': 64, 'z': 0}}}
        bridge = SimpleNamespace(get_state=AsyncMock(return_value=state),
                                 execute_tool=AsyncMock(return_value={'success': False, 'data': {'error_code': 'MISSING_ITEM'}}),
                                 cancel_task=AsyncMock())
        goal = {'kind': 'inventory_at_least', 'item': 'cobblestone', 'count': 10}
        decisions = [{'type': 'act', 'goals': [goal], 'action': {'name': 'equip', 'args': {'item': f'missing_{i}'}}}
                     for i in range(6)]
        client = SimpleNamespace(generate_response=AsyncMock(side_effect=[json.dumps(d) for d in decisions]))
        with tempfile.TemporaryDirectory() as directory:
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=20,
                                       agent_timeout=10, goal_review_enabled=False, agent_max_no_progress_actions=4)
            agent = ModelToolAgent(bridge, client, settings=settings, library=library, auto_reflect=False)
            try:
                result = await agent.run('get ten cobblestone')
                self.assertFalse(result['verified'])
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(result['data']['tool_calls'], 4)
                self.assertTrue(all(not e['progress_made'] for e in result['data']['trace']))
                self.assertEqual(client.generate_response.await_count, 4)
            finally:
                await agent.aclose()
                library.close()


if __name__ == '__main__':
    unittest.main()
