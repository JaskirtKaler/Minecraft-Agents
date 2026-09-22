"""
Local Jarvis Knowledge & Strategy Relay
Queries the local Jarvis/Ollama service (http://localhost:11434) for Minecraft mechanics,
crafting recipes, and self-correction assistance with zero external API costs.
"""

import logging
import httpx
import os
from agent.config import config

logger = logging.getLogger("JarvisRelay")

class JarvisClient:
    def __init__(self):
        self.endpoint = os.getenv("JARVIS_ENDPOINT", "http://localhost:11434/api/chat")
        self.model = os.getenv("JARVIS_MODEL", "gemma4:26b")
        logger.info(f"Jarvis Relay initialized -> {self.endpoint} (Model: {self.model})")

    async def query_knowledge(self, query: str, context: str = "") -> str:
        """
        Asks Jarvis for Minecraft crafting instructions, block mechanics, or error diagnosis.
        """
        logger.info(f"🧠 Asking Jarvis locally: '{query}'...")
        system_prompt = (
            "You are Jarvis, an expert Minecraft advisor. Provide concise, direct, step-by-step "
            "Minecraft crafting recipes, block locations, or coding advice to help an autonomous "
            "Mineflayer bot achieve its goal."
        )

        user_content = query
        if context:
            user_content += f"\nContext/Error: {context}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "stream": False,
            "options": {
                "temperature": 0.2
            }
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                message_content = data.get("message", {}).get("content", "")
                logger.info(f"✅ Jarvis responded: {message_content[:80]}...")
                return message_content.strip()
        except Exception as e:
            logger.error(f"Failed to query local Jarvis: {e}")
            return f"[Jarvis Relay Note] Could not connect to local Jarvis at {self.endpoint}: {e}"
