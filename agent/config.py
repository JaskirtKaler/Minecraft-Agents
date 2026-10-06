"""
Configuration settings for the Minecraft Agent system.
Loads configuration from environment variables (.env file).
"""

import os
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

class Config(BaseModel):
    # Nebius Token Factory Settings
    nebius_api_key: str = Field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", ""))
    nebius_base_url: str = Field(default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.nebius.ai/v1"))
    nebius_model: str = Field(default_factory=lambda: os.getenv("NEBIUS_MODEL", "nvidia/nemotron-4-340b-instruct"))

    # Tavily Web Search API Settings
    tavily_api_key: str = Field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    enable_tavily: bool = Field(default_factory=lambda: os.getenv("ENABLE_TAVILY", "false").lower() == "true")

    # WebSocket Bridge Server Settings
    ws_host: str = Field(default_factory=lambda: os.getenv("WS_HOST", "0.0.0.0"))
    ws_port: int = Field(default_factory=lambda: int(os.getenv("WS_PORT", "8765")))

    # Minecraft Server Connection Info (passed to bot if managed)
    mc_host: str = Field(default_factory=lambda: os.getenv("MC_HOST", "localhost"))
    mc_port: int = Field(default_factory=lambda: int(os.getenv("MC_PORT", "25565")))
    mc_username: str = Field(default_factory=lambda: os.getenv("MC_USERNAME", "AI_Agent"))
    mc_version: str = Field(default_factory=lambda: os.getenv("MC_VERSION", "1.20.1"))

    # World memory remains local even when the action planner uses Nebius.
    memory_enabled: bool = Field(default_factory=lambda: os.getenv("MEMORY_ENABLED", "true").lower() == "true")
    memory_dir: str = Field(default_factory=lambda: os.getenv("MEMORY_DIR", "data/memory"))
    graphiti_enabled: bool = Field(default_factory=lambda: os.getenv("GRAPHITI_ENABLED", "true").lower() == "true")
    memory_base_url: str = Field(default_factory=lambda: os.getenv("MEMORY_BASE_URL", "http://localhost:11434/v1"))
    memory_api_key: str = Field(default_factory=lambda: os.getenv("MEMORY_API_KEY", "local-ollama"))
    memory_model: str = Field(default_factory=lambda: os.getenv("MEMORY_MODEL", os.getenv("JARVIS_MODEL", "gemma4:26b")))
    memory_embedding_model: str = Field(default_factory=lambda: os.getenv("MEMORY_EMBEDDING_MODEL", "nomic-embed-text"))
    memory_embedding_dim: int = Field(default_factory=lambda: int(os.getenv("MEMORY_EMBEDDING_DIM", "768")))
    memory_ingest_timeout: float = Field(default_factory=lambda: float(os.getenv("MEMORY_INGEST_TIMEOUT", "180")))

    # Execution Loop Settings
    max_retries: int = Field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    code_timeout_ms: int = Field(default_factory=lambda: int(os.getenv("CODE_TIMEOUT_MS", "30000")))
    allow_experimental_code: bool = Field(default_factory=lambda: os.getenv("ALLOW_EXPERIMENTAL_CODE", "false").lower() == "true")

config = Config()
