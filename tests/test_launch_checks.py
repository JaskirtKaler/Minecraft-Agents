"""Configuration checks with synthetic settings; no server or model calls."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.launch_checks import prepare_agent_environment, required_ollama_models, settings_from_config


class LaunchConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "server").mkdir()
        (self.root / "server/eula.txt").write_text("# fixture\neula=true\n")
        self.config = SimpleNamespace(
            mc_host="localhost", mc_port=25565, ws_port=8765, mc_username="AI_Agent",
            mc_command_players=["FixturePlayer"], mc_version="1.20.1", mc_training_mode=True,
            memory_enabled=True, graphiti_enabled=True, memory_dir="data/memory", learning_dir="data/learning",
            nebius_base_url="http://localhost:11434/v1", nebius_model="planner",
            memory_base_url="http://localhost:11434/v1", memory_model="memory", memory_embedding_model="embedding",
        )
        self.environment = {"MC_WORLD_ID": "existing-world", "NEBIUS_API_KEY": "private-fixture-key"}

    def settings(self, agent_only=False):
        with patch("scripts.launch_checks.lan_addresses", return_value="192.168.1.12:25565"):
            return settings_from_config(self.config, agent_only, self.environment, self.root)

    def test_host_keeps_world_identity_with_loopback_controller_and_lan_game(self):
        result = self.settings()
        self.assertEqual(result["MC_HOST"], "127.0.0.1")
        self.assertEqual(result["MC_BIND_HOST"], "0.0.0.0")
        self.assertEqual(result["MC_WORLD_ID"], "existing-world")
        self.assertEqual(result["LAN_ADDRESSES"], "192.168.1.12:25565")
        self.assertNotIn("private-fixture-key", str(result))
        self.assertNotIn("NEBIUS_API_KEY", result)

    def test_host_rejects_remote_address_and_invalid_setup(self):
        for key, value in (("mc_host", "192.168.1.12"), ("mc_username", "bad name"),
                           ("mc_command_players", []), ("mc_command_players", ["*"]),
                           ("mc_version", "1.21"), ("mc_port", 0), ("ws_port", 65536),
                           ("ws_port", 25565)):
            with self.subTest(key=key, value=value):
                previous = getattr(self.config, key)
                setattr(self.config, key, value)
                with self.assertRaises(RuntimeError):
                    self.settings()
                setattr(self.config, key, previous)

    def test_host_requires_eula_without_changing_file(self):
        eula = self.root / "server/eula.txt"
        eula.write_text("eula=false\n")
        with self.assertRaisesRegex(RuntimeError, "EULA"):
            self.settings()
        self.assertEqual(eula.read_text(), "eula=false\n")

    def test_agent_accepts_remote_server_without_server_files(self):
        (self.root / "server/eula.txt").unlink()
        self.config.mc_host = "192.168.1.12"
        result = self.settings(agent_only=True)
        self.assertEqual(result["MC_HOST"], "192.168.1.12")
        self.assertEqual(result["LAN_ADDRESSES"], "")

    def test_new_agents_ignore_host_dotenv_memory_paths_but_preserve_explicit_overrides(self):
        environment = {"MC_USERNAME": "AI_Agent_2", "MC_WORLD_ID": "existing-world",
                       "MEMORY_DIR": "data/memory", "LEARNING_DIR": "data/learning"}
        prepare_agent_environment(environment, None, None)
        self.assertEqual(environment["MEMORY_DIR"], "data/agents/ai_agent_2/memory")
        self.assertEqual(environment["LEARNING_DIR"], "data/agents/ai_agent_2/learning")
        prepare_agent_environment(environment, "/app/data/memory", "/app/data/learning")
        self.assertEqual(environment["MEMORY_DIR"], "/app/data/memory")
        self.assertEqual(environment["LEARNING_DIR"], "/app/data/learning")

    def test_join_requires_world_identity_and_valid_username(self):
        for environment in ({"MC_USERNAME": "AI_Agent_2"},
                            {"MC_USERNAME": "bad name", "MC_WORLD_ID": "existing-world"}):
            with self.subTest(environment=environment), self.assertRaises(RuntimeError):
                prepare_agent_environment(environment, None, None)

    def test_only_local_ollama_models_are_checked(self):
        required = required_ollama_models(self.config, {"JARVIS_MODEL": "advisor"})
        self.assertEqual(required, ["planner", "advisor", "memory", "embedding"])
        self.config.nebius_base_url = "http://host.docker.internal:8000/v1"
        self.config.graphiti_enabled = False
        environment = {"JARVIS_ENDPOINT": "http://192.168.1.12:11434/api/chat"}
        self.assertEqual(required_ollama_models(self.config, environment), [])


if __name__ == "__main__":
    unittest.main()
