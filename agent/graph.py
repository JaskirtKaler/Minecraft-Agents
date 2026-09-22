"""
LangGraph Orchestrator Engine for Minecraft Agent
Defines the state machine for Observation -> RAG -> Nebius LLM Code Generation -> Mineflayer Execution -> Self-Correction Loop.
"""

import json
import logging
from typing import Dict, Any, TypedDict, Optional
from langgraph.graph import StateGraph, END

from agent.bridge import MineflayerBridge
from agent.nebius_client import NebiusLLMClient
from agent.tavily_client import TavilySearchClient
from agent.jarvis_client import JarvisClient
from agent.prompts import SYSTEM_PROMPT, REWRITE_PROMPT_TEMPLATE
from agent.config import config

logger = logging.getLogger("AgentGraph")

class AgentState(TypedDict):
    objective: str
    bot_state: Dict[str, Any]
    code: str
    execution_result: Optional[Dict[str, Any]]
    error_trace: str
    retry_count: int
    rag_info: str
    status: str

class MinecraftAgentGraph:
    def __init__(self, bridge: MineflayerBridge):
        self.bridge = bridge
        self.nebius = NebiusLLMClient()
        self.tavily = TavilySearchClient()
        self.jarvis = JarvisClient()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)

        # Add Nodes
        builder.add_node("observe", self.observe_node)
        builder.add_node("rag_search", self.rag_search_node)
        builder.add_node("generate_code", self.generate_code_node)
        builder.add_node("execute_code", self.execute_code_node)

        # Set Entry Point
        builder.set_entry_point("observe")

        # Edges
        builder.add_edge("observe", "rag_search")
        builder.add_edge("rag_search", "generate_code")
        builder.add_edge("generate_code", "execute_code")
        
        # Conditional Edge after execution
        builder.add_conditional_edges(
            "execute_code",
            self.should_retry_or_end,
            {
                "retry": "rag_search",
                "success": END,
                "max_retries_reached": END
            }
        )

        return builder.compile()

    async def observe_node(self, state: AgentState) -> AgentState:
        """Fetches current bot state from Mineflayer bridge."""
        logger.info(f"--- [Node: Observe State] Objective: '{state['objective']}' ---")
        try:
            bot_state = await self.bridge.get_state()
            state["bot_state"] = bot_state
            state["status"] = "observed"
        except Exception as e:
            logger.error(f"Failed to fetch bot state: {e}")
            state["bot_state"] = {"ready": False, "error": str(e)}
        return state

    async def rag_search_node(self, state: AgentState) -> AgentState:
        """Queries Tavily web search if items or error stack traces mention crafting or missing mechanics."""
        logger.info(f"--- [Node: RAG Search Check] ---")
        
        objective = state.get("objective", "")
        error_trace = state.get("error_trace", "")
        
        # Trigger Tavily if crafting/building is mentioned or an error occurred
        needs_search = any(kw in objective.lower() for kw in ["craft", "make", "build", "recipe", "smelt"]) or bool(error_trace)
        
        if needs_search and not state.get("rag_info"):
            query = f"Minecraft instructions or recipe for objective: {objective}"
            # Ask Jarvis locally (zero external API cost)
            jarvis_advice = await self.jarvis.query_knowledge(query, context=error_trace)
            
            # Optional Tavily fallback if enabled
            if config.enable_tavily:
                tavily_res = await self.tavily.search_minecraft_wiki(query)
                jarvis_advice += f"\n{tavily_res}"

            state["rag_info"] = jarvis_advice.strip()
            logger.info("Retrieved strategy & recipe guidance from Jarvis.")
        else:
            if "rag_info" not in state:
                state["rag_info"] = ""

        return state

    async def generate_code_node(self, state: AgentState) -> AgentState:
        """Generates or rewrites Mineflayer JS code snippet using Nebius Token Factory."""
        logger.info(f"--- [Node: Generate Mineflayer Code (Retry {state['retry_count']})] ---")

        bot_state_json = json.dumps(state.get("bot_state", {}), indent=2)
        objective = state.get("objective", "")
        error_trace = state.get("error_trace", "")
        previous_code = state.get("code", "")
        rag_info = state.get("rag_info", "")

        if error_trace and previous_code:
            # Self-Correction prompt
            prompt_content = REWRITE_PROMPT_TEMPLATE.format(
                objective=objective,
                previous_code=previous_code,
                error_trace=error_trace,
                rag_info=rag_info
            )
        else:
            # Initial generation prompt
            prompt_content = f"""
OBJECTIVE: {objective}

CURRENT GAME STATE:
{bot_state_json}

RELEVANT WIKI / RAG KNOWLEDGE:
{rag_info if rag_info else "None"}

Write executable Mineflayer JavaScript code snippet to accomplish the objective.
            """

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_content}
        ]

        try:
            generated_code = await self.nebius.generate_response(messages)
            state["code"] = generated_code
            state["status"] = "code_generated"
            logger.info("Successfully generated Mineflayer code snippet.")
        except Exception as e:
            logger.error(f"Code generation failed: {e}")
            state["error_trace"] = f"LLM Generation Error: {e}"
            state["code"] = ""

        return state

    async def execute_code_node(self, state: AgentState) -> AgentState:
        """Sends generated code snippet to Node.js Mineflayer execution sandbox."""
        logger.info(f"--- [Node: Execute Mineflayer Code Sandbox] ---")

        code = state.get("code", "")
        if not code:
            state["execution_result"] = {"success": False, "errorStack": "No code was generated."}
            state["error_trace"] = "No code was generated."
            return state

        try:
            res = await self.bridge.execute_code(code)
            state["execution_result"] = res
            
            if res.get("success"):
                logger.info("Code executed successfully in Mineflayer sandbox!")
                state["error_trace"] = ""
                state["status"] = "success"
            else:
                error_stack = res.get("errorStack") or res.get("stderr") or "Unknown execution error"
                logger.warning(f"Sandbox execution failed: {error_stack}")
                state["error_trace"] = error_stack
                state["status"] = "failed"
                state["retry_count"] += 1
        except Exception as e:
            logger.error(f"Bridge execution error: {e}")
            state["error_trace"] = str(e)
            state["status"] = "failed"
            state["retry_count"] += 1

        return state

    def should_retry_or_end(self, state: AgentState) -> str:
        """Conditional routing logic."""
        if state.get("status") == "success":
            logger.info("Objective completed successfully! Ending loop.")
            return "success"
        
        if state.get("retry_count", 0) < config.max_retries:
            logger.info(f"Execution failed. Triggering Self-Correction (Attempt {state['retry_count'] + 1}/{config.max_retries})...")
            return "retry"

        logger.error(f"Max retries ({config.max_retries}) reached. Objective failed.")
        return "max_retries_reached"
