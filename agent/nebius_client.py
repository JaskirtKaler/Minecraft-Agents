"""
Nebius Token Factory Client
Interface for Nebius hosted open LLMs (NVIDIA Nemotron, Llama 3, etc.) using OpenAI SDK.
"""

import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from agent.config import config
from urllib.parse import urlparse
from time import perf_counter

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
        max_tokens: int = 2500,
        json_mode: bool = False,
        thinking: Optional[bool] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generates a text completion or code snippet from Nebius Token Factory."""
        if not self.api_key:
            raise ValueError("NEBIUS_API_KEY is missing. Please add NEBIUS_API_KEY to your .env file.")

        try:
            logger.info(f"Sending planner request: {self.model} via {self.base_url}...")
            options = {'response_format': {'type': 'json_object'}} if json_mode else {}
            endpoint = urlparse(self.base_url)
            local_ollama = endpoint.hostname in {'localhost', '127.0.0.1', '::1'} and endpoint.port == 11434
            if json_mode and response_schema and local_ollama:
                # Constrain local decoding to valid goal/tool names. Hosted
                # schema support is not assumed; retain its existing JSON mode.
                options['response_format'] = {'type': 'json_schema', 'json_schema': {
                    'name': 'minecraft_response', 'schema': response_schema}}
            local_thinking = config.local_planner_thinking if thinking is None else thinking
            if json_mode and local_ollama and not local_thinking:
                # Ollama's documented compatibility effort disables hidden
                # thinking for Boolean-thinking models. Hosted defaults stay unchanged.
                options['reasoning_effort'] = 'none'
            started = perf_counter()
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **options,
            )
            usage = getattr(response, 'usage', None)
            self.last_metrics = {'seconds': round(perf_counter() - started, 3),
                                 'prompt_tokens': getattr(usage, 'prompt_tokens', None),
                                 'completion_tokens': getattr(usage, 'completion_tokens', None)}
            logger.info('Planner response: %.1fs, prompt=%s output=%s tokens', self.last_metrics['seconds'],
                        self.last_metrics['prompt_tokens'], self.last_metrics['completion_tokens'])
            content = response.choices[0].message.content or ""
            if json_mode and not content.strip():
                raise ValueError(f'Model returned no JSON text (finish_reason={response.choices[0].finish_reason}); nothing was executed.')
            return content.strip()
        except Exception as e:
            logger.error(f"Error calling Nebius Token Factory API: {e}")
            raise e
