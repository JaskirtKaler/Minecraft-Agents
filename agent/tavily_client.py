"""
Tavily Web Search Integration
Used for RAG (Retrieval Augmented Generation) to fetch Minecraft recipes and mechanics.
"""

import logging
from typing import Dict, Any, Optional
import httpx
from agent.config import config

logger = logging.getLogger("TavilyClient")

class TavilySearchClient:
    def __init__(self):
        self.api_key = config.tavily_api_key
        self.endpoint = "https://api.tavily.com/search"

        if not self.api_key:
            logger.warning("TAVILY_API_KEY is not set. Web RAG searches will be disabled until set in .env.")

    async def search_minecraft_wiki(self, query: str, max_results: int = 3) -> str:
        if not config.enable_tavily:
            logger.info("Tavily RAG is currently disabled (ENABLE_TAVILY=false). Skipping search.")
            return ""

        if not self.api_key:
            return "[Tavily Search Disabled] TAVILY_API_KEY is not configured in .env."

        search_query = f"Minecraft Wiki {query}"
        logger.info(f"Querying Tavily RAG: '{search_query}'...")

        payload = {
            "api_key": self.api_key,
            "query": search_query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": max_results
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()

                answer = data.get("answer")
                results = data.get("results", [])

                summary_parts = []
                if answer:
                    summary_parts.append(f"Direct Answer: {answer}")

                for i, res in enumerate(results, 1):
                    title = res.get("title", "")
                    content = res.get("content", "")
                    url = res.get("url", "")
                    summary_parts.append(f"\nSource [{i}]: {title} ({url})\n{content}")

                return "\n".join(summary_parts) if summary_parts else "No relevant Minecraft information found."
        except Exception as e:
            logger.error(f"Error querying Tavily API: {e}")
            return f"[Tavily Search Error] Failed to search: {e}"
