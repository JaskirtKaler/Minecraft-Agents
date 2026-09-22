"""
Main Entry Point for Autonomous LLM Minecraft Agent
Launches the WebSocket Bridge and manages interactive terminal & in-game chat objectives.
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
    """Interactive loop for CLI objectives and automated in-game Minecraft chat trigger."""
    agent_graph = MinecraftAgentGraph(bridge)

    print("\n" + "="*60)
    print(" 🎮 MINECRAFT LLM AGENT (Mineflayer + Nebius Token Factory) 🎮 ")
    print("="*60)
    print(f"• Nebius Model     : {config.nebius_model}")
    print(f"• Nebius Base      : {config.nebius_base_url}")
    print(f"• WebSocket Server : {config.ws_host}:{config.ws_port}")
    print("="*60)
    print("\nWaiting for Node.js Mineflayer Bot to connect via WebSocket...")
    
    # Wait for bot client to connect
    while not bridge.active_client:
        await asyncio.sleep(1)

    print("\n✅ Mineflayer Bot connected successfully! Agent is ready for objectives.")
    print("💡 YOU CAN NOW TALK TO THE AGENT DIRECTLY IN MINECRAFT GAME CHAT!")
    print("   Or type objectives below in this terminal (e.g. 'mine 3 oak logs', 'craft wooden planks').\n")

    # In-Game Chat Listener Callback
    def handle_bot_event(event_payload):
        if event_payload.get("event") == "chat":
            chat_data = event_payload.get("data", {})
            username = chat_data.get("username")
            message = chat_data.get("message", "").strip()

            if username == config.mc_username or not message:
                return

            logger.info(f"💬 [In-Game Chat Command from '{username}']: '{message}'")
            
            # Schedule async agent execution task
            asyncio.create_task(execute_chat_objective(agent_graph, bridge, message, username))

    bridge.event_callbacks.append(handle_bot_event)

    while True:
        try:
            user_input = await asyncio.to_thread(input, "Agent Objective > ")
            objective = user_input.strip()

            if not objective:
                continue

            if objective.lower() in ["exit", "quit"]:
                print("Shutting down Agent controller...")
                break

            await execute_cli_objective(agent_graph, objective)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break
        except Exception as e:
            logger.error(f"Error during objective execution: {e}")

async def execute_cli_objective(agent_graph: MinecraftAgentGraph, objective: str):
    """Executes objective from terminal input."""
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

async def execute_chat_objective(agent_graph: MinecraftAgentGraph, bridge: MineflayerBridge, message: str, username: str):
    """Executes objective triggered from in-game Minecraft chat."""
    try:
        await bridge.send_chat(f"Processing objective: '{message}'...")

        initial_state = {
            "objective": message,
            "bot_state": {},
            "code": "",
            "execution_result": None,
            "error_trace": "",
            "retry_count": 0,
            "rag_info": "",
            "status": "started"
        }

        final_state = await agent_graph.graph.ainvoke(initial_state)
        status = final_state.get("status")
        
        if status == "success":
            await bridge.send_chat(f"✅ Completed: '{message}'")
        else:
            err = final_state.get("error_trace", "Execution error")
            await bridge.send_chat(f"❌ Failed: {err[:50]}")
    except Exception as e:
        logger.error(f"Error processing in-game chat command: {e}")

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
