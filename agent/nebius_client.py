"""
Nebius Token Factory Client
Interface for Nebius hosted open LLMs (NVIDIA Nemotron, Llama 3, etc.) using OpenAI SDK.
"""

import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from agent.config import config

logger = logging.getLogger("NebiusClient")

class NebiusLLMClient:
    def __init__(self):
        self.api_key = config.nebius_api_key
        self.base_url = config.nebius_base_url
        self.model = config.nebius_model

        if not self.api_key:
            logger.warning("NEBIUS_API_KEY is not set in environment. Set it in .env before generating code.")

        self.client = AsyncOpenAI(
            api_key=self.api_key or "dummy_key",
            base_url=self.base_url
        )

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2500
    ) -> str:
        """Generates a text completion or code snippet from Nebius Token Factory."""
        if not self.api_key:
            raise ValueError("NEBIUS_API_KEY is missing. Please add NEBIUS_API_KEY to your .env file.")

        try:
            logger.info(f"Sending prompt to Nebius Token Factory model: {self.model}...")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content or ""
            return content.strip()
        except Exception as e:
            logger.error(f"Error calling Nebius Token Factory API: {e}")
            raise e
