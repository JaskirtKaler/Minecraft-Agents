"""Memory completion adaptation; no graph database or inference calls."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent.graphiti_memory import memory_api_adapter


class MemoryCompletionAdapterTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, endpoint):
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'), finish_reason='stop')])
        create = AsyncMock(return_value=reply)
        embeddings = object()
        api = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), embeddings=embeddings)
        return memory_api_adapter(api, endpoint), create, embeddings, reply

    async def test_local_json_completion_disables_hidden_reasoning_without_changing_schema(self):
        adapter, create, embeddings, reply = self.fixture('http://localhost:11434/v1')
        schema = {'type': 'json_schema', 'json_schema': {'name': 'facts', 'schema': {'type': 'object'}}}
        result = await adapter.chat.completions.create(model='local-extractor', max_tokens=4096,
                                                     response_format=schema, temperature=0)
        self.assertIs(result, reply)
        self.assertEqual(create.call_args.kwargs['reasoning_effort'], 'none')
        self.assertEqual(create.call_args.kwargs['response_format'], schema)
        self.assertEqual(create.call_args.kwargs['model'], 'local-extractor')
        self.assertEqual(create.call_args.kwargs['max_tokens'], 4096)
        self.assertIs(adapter.embeddings, embeddings)

    async def test_hosted_and_other_local_endpoints_do_not_receive_an_ollama_override(self):
        for endpoint in ('https://hosted.example/v1', 'http://127.0.0.1:8000/v1'):
            with self.subTest(endpoint=endpoint):
                adapter, create, _, _ = self.fixture(endpoint)
                await adapter.chat.completions.create(model='extractor', reasoning_effort='high')
                self.assertEqual(create.call_args.kwargs['reasoning_effort'], 'high')
                create.reset_mock()
                await adapter.chat.completions.create(model='extractor')
                self.assertNotIn('reasoning_effort', create.call_args.kwargs)

    async def test_empty_completion_logs_budget_diagnostics_without_fabricating_facts(self):
        adapter, _, _, reply = self.fixture('http://127.0.0.1:11434/v1')
        reply.choices[0].message.content = None
        reply.choices[0].finish_reason = 'length'
        reply.usage = SimpleNamespace(completion_tokens=4096)
        with self.assertLogs('GraphitiMemory', level='WARNING') as logged:
            result = await adapter.chat.completions.create(model='local-extractor')
        self.assertIs(result, reply, 'Graphiti must still reject the real empty reply; never invent facts.')
        self.assertIn('finish_reason=length', logged.output[0])
        self.assertIn('completion_tokens=4096', logged.output[0])

    async def test_endpoint_failure_propagates_without_a_fake_memory_success(self):
        adapter, create, _, _ = self.fixture('http://localhost:11434/v1')
        create.side_effect = RuntimeError('local service unavailable')
        with self.assertRaisesRegex(RuntimeError, 'service unavailable'):
            await adapter.chat.completions.create(model='local-extractor')


if __name__ == '__main__':
    unittest.main()
