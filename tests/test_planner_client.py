"""Request contracts only; no local or hosted model calls."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent import nebius_client
from agent.nebius_client import NebiusLLMClient
from agent.planner_schema import decision_schema, tool_schema
from agent.tool_agent import TOOLS


class PlannerClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, endpoint):
        client = object.__new__(NebiusLLMClient)
        client.api_key, client.base_url, client.model = 'test-only', endpoint, 'test-model'
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"type":"done"}'), finish_reason='stop')]))
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return client, create

    async def test_local_json_reflection_disables_thinking_without_changing_model(self):
        client, create = self.client('http://localhost:11434/v1')
        await client.generate_response([], json_mode=True, thinking=False)
        options = create.call_args.kwargs
        self.assertEqual(options['model'], 'test-model')
        self.assertEqual(options['response_format'], {'type': 'json_object'})
        self.assertEqual(options['reasoning_effort'], 'none')

    async def test_hosted_json_does_not_receive_ollama_reasoning_override(self):
        client, create = self.client('https://hosted.example/v1')
        await client.generate_response([], json_mode=True, thinking=False)
        self.assertNotIn('reasoning_effort', create.call_args.kwargs)

    async def test_local_action_reasoning_and_legacy_text_mode(self):
        client, create = self.client('http://localhost:11434/v1')
        with patch.object(nebius_client.config, 'local_planner_thinking', True):
            await client.generate_response([], json_mode=True)
        self.assertNotIn('reasoning_effort', create.call_args.kwargs)
        await client.generate_response([])
        self.assertNotIn('response_format', create.call_args.kwargs)

    async def test_truncated_empty_json_reply_is_an_error_not_an_action(self):
        client, create = self.client('http://localhost:11434/v1')
        create.return_value.choices[0].message.content = None
        create.return_value.choices[0].finish_reason = 'length'
        with self.assertRaisesRegex(ValueError, 'finish_reason=length'):
            await client.generate_response([], json_mode=True)

    async def test_local_structured_schema_and_latency_metrics(self):
        client, create = self.client('http://localhost:11434/v1')
        schema = {'type': 'object', 'properties': {'type': {'const': 'done'}}}
        await client.generate_response([], json_mode=True, response_schema=schema)
        self.assertEqual(create.call_args.kwargs['response_format']['json_schema']['schema'], schema)
        self.assertEqual(create.call_args.kwargs['reasoning_effort'], 'none')
        self.assertGreaterEqual(client.last_metrics['seconds'], 0)

    async def test_hosted_schema_support_is_not_assumed(self):
        client, create = self.client('https://hosted.example/v1')
        await client.generate_response([], json_mode=True, response_schema={'type': 'object'})
        self.assertEqual(create.call_args.kwargs['response_format'], {'type': 'json_object'})

    async def test_schema_requires_quantities_and_omits_frozen_goals(self):
        initial = decision_schema(TOOLS)['oneOf'][0]
        continuing = decision_schema(TOOLS, include_goals=False)['oneOf'][0]
        self.assertIn('goals', initial['properties'])
        self.assertNotIn('goals', continuing['properties'])
        self.assertNotIn('action', continuing['properties'])
        self.assertFalse(continuing['additionalProperties'])
        self.assertIn('actions', continuing['required'])
        for tool in tool_schema(TOOLS)['oneOf']:
            name = tool['properties']['name']['const']
            if name in {'craft', 'mine_logs', 'mine_resource', 'give_item', 'deposit', 'withdraw', 'deposit_item'}:
                self.assertIn('count', tool['properties']['args']['required'])
