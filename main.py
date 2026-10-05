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
from agent.intents import is_explicit_task_request, parse_resource_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("MainController")

async def run_agent_loop(bridge: MineflayerBridge):
    """Interactive loop for CLI objectives and automated in-game Minecraft chat trigger."""
    agent_graph = MinecraftAgentGraph(bridge)
    objective_lock = asyncio.Lock()

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
            asyncio.create_task(
                execute_chat_objective(agent_graph, bridge, message, username, objective_lock)
            )

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

            await execute_cli_objective(agent_graph, bridge, objective, objective_lock)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break
        except Exception as e:
            logger.error(f"Error during objective execution: {e}")

async def execute_cli_objective(
    agent_graph: MinecraftAgentGraph,
    bridge: MineflayerBridge,
    objective: str,
    objective_lock: asyncio.Lock,
):
    """Executes objective from terminal input."""
    async with objective_lock:
        task = parse_resource_task(objective)
        if task:
            print(f"\n🔧 Running verified task: {task['name']}...")
            result = await bridge.execute_task(task)
            if result.get("success") and result.get("verified"):
                print(f"\n✨ OBJECTIVE VERIFIED SUCCESSFULLY: {result.get('message', objective)} ✨\n")
            else:
                print(f"\n❌ VERIFIED TASK FAILED: {result.get('message', 'Unknown error')}\n")
            return

        final_state = await run_planner_objective(agent_graph, objective)
        report_cli_planner_outcome(objective, final_state)


async def execute_chat_objective(
    agent_graph: MinecraftAgentGraph,
    bridge: MineflayerBridge,
    message: str,
    username: str,
    objective_lock: asyncio.Lock,
):
    """Executes objective triggered from in-game Minecraft chat."""
    try:
        # A normal conversation must not become arbitrary generated JavaScript.
        # Physical tasks are serialized because Mineflayer pathfinder only has one
        # active goal at a time.
        async with objective_lock:
            task = parse_resource_task(message, requester=username)
            if task:
                await bridge.send_chat(f"Starting verified task: {task['name']}.")
                result = await bridge.execute_task(task)
                if result.get("success") and result.get("verified"):
                    await bridge.send_chat(f"✅ {result.get('message', 'Task verified.')}")
                else:
                    await bridge.send_chat(f"❌ {result.get('message', 'Verified task failed.')[:180]}")
                return

            if not is_explicit_task_request(message, config.mc_username):
                return

            await bridge.send_chat(f"Planning objective: '{message}'...")
            final_state = await run_planner_objective(agent_graph, message)
            status = final_state.get("status")

            if status == "success":
                await bridge.send_chat(f"✅ Completed: '{message}'")
            elif status == "unverified":
                await bridge.send_chat("⚠️ The attempt finished, but I could not verify the requested world state.")
            else:
                err = final_state.get("error_trace", "Execution error")
                await bridge.send_chat(f"❌ Failed: {err[:180]}")
    except Exception as e:
        logger.error(f"Error processing in-game chat command: {e}")


async def run_planner_objective(agent_graph: MinecraftAgentGraph, objective: str):
    """Run the legacy free-form planner with explicit unverified-result semantics."""
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
    print(f"\n🧭 Running planner for objective: '{objective}'...")
    return await agent_graph.graph.ainvoke(initial_state)


def report_cli_planner_outcome(objective: str, final_state: dict):
    """Report planner status without mistaking code completion for task completion."""
    status = final_state.get("status")
    if status == "success":
        print(f"\n✨ OBJECTIVE COMPLETED SUCCESSFULLY: '{objective}' ✨\n")
    elif status == "unverified":
        print(f"\n⚠️ ATTEMPT FINISHED BUT IS UNVERIFIED: '{objective}'\n")
    else:
        print(f"\n❌ OBJECTIVE FAILED: '{objective}' (Reason: {final_state.get('error_trace', 'Unknown')})\n")

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
