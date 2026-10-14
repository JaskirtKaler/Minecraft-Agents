"""Memory inference remains local and independent of the gameplay provider."""
import os
import unittest
from unittest.mock import patch

from agent.config import Config


class MemoryModelConfigTests(unittest.TestCase):
    def test_default_memory_model_is_the_local_nemotron(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Config()
        self.assertEqual(settings.memory_model, 'nemotron-3.5-lightning:30b-mlx')
        self.assertEqual(settings.memory_base_url, 'http://localhost:11434/v1')
        self.assertEqual(settings.memory_embedding_model, 'nomic-embed-text')
        self.assertEqual(settings.memory_embedding_dim, 768)

    def test_hosted_planner_does_not_silently_move_memory_to_hosted_inference(self):
        with patch.dict(os.environ, {'NEBIUS_MODEL': 'hosted-planner',
                                   'NEBIUS_BASE_URL': 'https://hosted.example/v1'}, clear=True):
            settings = Config()
        self.assertEqual(settings.nebius_model, 'hosted-planner')
        self.assertEqual(settings.memory_model, 'nemotron-3.5-lightning:30b-mlx')
        self.assertEqual(settings.memory_base_url, 'http://localhost:11434/v1')

    def test_explicit_memory_model_overrides_local_advisor(self):
        with patch.dict(os.environ, {'MEMORY_MODEL': 'memory-extractor',
                                   'JARVIS_MODEL': 'local-advisor'}, clear=True):
            settings = Config()
        self.assertEqual(settings.memory_model, 'memory-extractor')

    def test_explicit_advisor_is_memory_fallback_without_provider_coupling(self):
        with patch.dict(os.environ, {'JARVIS_MODEL': 'local-advisor',
                                   'NEBIUS_MODEL': 'different-planner'}, clear=True):
            settings = Config()
        self.assertEqual(settings.memory_model, 'local-advisor')


if __name__ == '__main__':
    unittest.main()
