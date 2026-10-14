"""External evidence reaches the real planner without becoming world truth."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import httpx

from agent.experience import ExperienceLibrary
from agent.tavily_client import TavilySearchClient, TavilySettings
from agent.tool_agent import ModelToolAgent, validate_knowledge_action
from practice.knowledge_probe import run as run_probe
from practice.construction import SimulatedConstructionBridge, make_task


class KnowledgePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_imported_paid_probe_fails_closed_without_explicit_optin(self):
        with self.assertRaisesRegex(ValueError, 'Explicit'):
            await run_probe()

    def test_malformed_or_private_lookup_is_rejected_before_export(self):
        action = {'name': 'knowledge_lookup', 'args': {'query': 'oak sapling growth requirements',
                  'missing_fact': 'The registry does not describe growth.'}}
        self.assertEqual(validate_knowledge_action(action), action['args'])
        for text in ('Pilot6117 oak sapling', 'sapling at x=-24 y=64 z=-10', 'path /Users/alice/world',
                     'tvly-private-key growth', 'server 127.0.0.1 growth'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_knowledge_action({'name': 'knowledge_lookup', 'args': {**action['args'], 'query': text}}, 'Pilot6117')
        with self.assertRaises(ValueError):
            validate_knowledge_action({'name': 'dig', 'args': action['args']})

    async def test_model_selected_lookup_reaches_next_decision_and_restart_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'tavily.json'
            requests = []
            def handle(request):
                requests.append(request)
                return httpx.Response(200, json={'results': [{
                    'title': 'Sapling', 'url': 'https://minecraft.wiki/w/Sapling',
                    'content': 'Example externally sourced growth evidence; not a live block observation.'}]})
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=4,
                                       agent_timeout=10, goal_review_enabled=False, mc_version='1.20.1')
            selected = {'name': 'knowledge_lookup', 'args': {
                'query': 'oak sapling growth light and space requirements',
                'missing_fact': 'The registry does not expose tree growth conditions.'}}
            for expected_requests in (1, 0):
                # A separate client simulates a process restart; cache remains.
                knowledge = TavilySearchClient(TavilySettings(enabled=True, api_key='test-only', cache_path=cache),
                                               transport=httpx.MockTransport(handle))
                bridge = SimulatedConstructionBridge(make_task('negative_floor', 42))
                before = dict(bridge.blocks)
                packets = []
                async def reply(messages, **kwargs):
                    packet = json.loads(messages[1]['content'])
                    packets.append(packet)
                    if len(packets) == 1:
                        self.assertTrue(packet['knowledge_access']['enabled'])
                        self.assertIn('knowledge_lookup', json.dumps(kwargs['response_schema']))
                        return json.dumps({'type': 'act', 'actions': [selected]})
                    result = packet['recent_actions'][-1]['result']
                    self.assertFalse(result['verified'])
                    facts = result['data']['knowledge']
                    self.assertTrue(facts['untrusted'])
                    self.assertEqual(facts['version'], '1.20.1')
                    self.assertEqual(facts['from_cache'], expected_requests == 0)
                    self.assertEqual(facts['sources'][0]['url'], 'https://minecraft.wiki/w/Sapling')
                    return json.dumps({'type': 'answer', 'message': 'Wiki evidence requires a live-state recheck.'})
                library = ExperienceLibrary(directory)
                agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                       library=library, auto_reflect=False, knowledge=knowledge)
                try:
                    result = await agent.run('Research sapling growth without changing any blocks.')
                finally:
                    await agent.aclose()
                    library.close()
                self.assertEqual(result['status'], 'answer')
                self.assertEqual(result['data']['knowledge_requests_used'], expected_requests)
                self.assertEqual(result['data']['goals'], [])
                self.assertEqual(bridge.blocks, before)
                self.assertFalse(any(c['tool']['name'] == 'knowledge_lookup' for c in bridge.calls))
            self.assertEqual(len(requests), 1)

    async def test_disabled_lookup_is_not_in_schema_and_never_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = SimulatedConstructionBridge(make_task('negative_floor', 42))
            calls = []
            async def reply(messages, **kwargs):
                calls.append(messages)
                self.assertNotIn('knowledge_lookup', json.dumps(kwargs['response_schema']))
                if len(calls) == 1:
                    return json.dumps({'type': 'act', 'actions': [{'name': 'knowledge_lookup', 'args': {
                        'query': 'sapling growth', 'missing_fact': 'No external search is enabled.'}}]})
                return json.dumps({'type': 'answer', 'message': 'External lookup is disabled.'})
            library = ExperienceLibrary(directory)
            settings = SimpleNamespace(learning_dir=directory, model_step_timeout=2, agent_max_steps=3,
                                       agent_timeout=10, enable_tavily=False)
            agent = ModelToolAgent(bridge, SimpleNamespace(generate_response=reply), settings=settings,
                                   library=library, auto_reflect=False)
            try:
                result = await agent.run('What conditions do saplings need?')
            finally:
                await agent.aclose()
                library.close()
            self.assertEqual(result['data']['knowledge_requests_used'], 0)
            self.assertEqual(result['data']['trace'], [])
            self.assertEqual(len(result['data']['planner_errors']), 1)
