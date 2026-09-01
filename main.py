"""
Main Entry Point for Autonomous LLM Minecraft Agent
Launches the WebSocket Bridge and manages interactive / autonomous objectives.
"""

import asyncio
import logging
import sys
from agent.config import config
from agent.bridge import MineflayerBridge
from agent.graph import MinecraftAgentGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("MainController")

async def run_agent_loop(bridge: MineflayerBridge):
    """Interactive loop to prompt user for objectives or run autonomous goal sequence."""
    agent_graph = MinecraftAgentGraph(bridge)

    print("\n" + "="*60)
    print(" 🎮 MINECRAFT LLM AGENT (Mineflayer + Nebius Token Factory) 🎮 ")
    print("="*60)
    print(f"• Nebius Model  : {config.nebius_model}")
    print(f"• Nebius Base   : {config.nebius_base_url}")
    print(f"• WebSocket Server : {config.ws_host}:{config.ws_port}")
    print("="*60)
    print("\nWaiting for Node.js Mineflayer Bot to connect via WebSocket...")
    
    # Wait for bot client to connect
    while not bridge.active_client:
        await asyncio.sleep(1)

    print("\n✅ Mineflayer Bot connected successfully! Agent is ready for objectives.")
    print("Type an objective (e.g. 'mine 3 oak logs', 'craft wooden planks', 'come to player') or 'exit'.\n")

    while True:
        try:
            user_input = await asyncio.to_thread(input, "Agent Objective > ")
            objective = user_input.strip()

            if not objective:
                continue

            if objective.lower() in ["exit", "quit"]:
                print("Shutting down Agent controller...")
                break

            initial_state = {
                "objective": objective,
                "bot_state": {},
                "code": "",
                "execution_result": None,
                "error_trace": "",
                "retry_count": 0,
                "rag_info": "",
                "status": "started"
            }

            print(f"\n🚀 Running Agent Graph for objective: '{objective}'...")
            final_state = await agent_graph.graph.ainvoke(initial_state)
            
            status = final_state.get("status")
            if status == "success":
                print(f"\n✨ OBJECTIVE COMPLETED SUCCESSFULLY: '{objective}' ✨\n")
            else:
                print(f"\n❌ OBJECTIVE FAILED: '{objective}' (Reason: {final_state.get('error_trace', 'Unknown')})\n")

        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break
        except Exception as e:
            logger.error(f"Error during objective execution: {e}")

async def main():
    bridge = MineflayerBridge(host=config.ws_host, port=config.ws_port)
    server = await bridge.start()
    
    try:
        await run_agent_loop(bridge)
    finally:
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
