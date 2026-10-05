"""
Main Entry Point for Autonomous LLM Minecraft Agent
Launches the WebSocket Bridge and manages interactive terminal & in-game chat objectives.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
from agent.config import config
from agent.bridge import MineflayerBridge
from agent.graph import MinecraftAgentGraph
from agent.intents import is_explicit_task_request, parse_resource_task
from agent.world_memory import WorldMemory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("MainController")

async def run_agent_loop(bridge: MineflayerBridge, memory=None, chat_only=False):
    """Interactive loop for CLI objectives and automated in-game Minecraft chat trigger."""
    agent_graph = MinecraftAgentGraph(bridge, memory=memory)
    objective_lock = asyncio.Lock()

    print("\n" + "="*60)
    print(" 🎮 MINECRAFT LLM AGENT (Mineflayer + Nebius Token Factory) 🎮 ")
    print("="*60)
    print(f"• Planner Model    : {config.nebius_model}")
    print(f"• Planner Base     : {config.nebius_base_url}")
    print(f"• World Memory     : {memory.directory if memory else 'disabled'}")
    print(f"• WebSocket Server : {config.ws_host}:{config.ws_port}")
    print("="*60)
    print("\nWaiting for Node.js Mineflayer Bot to connect via WebSocket...")
    
    # Wait for bot client to connect
    while not bridge.active_client:
        await asyncio.sleep(1)

    print("\n✅ Mineflayer Bot connected successfully! Agent is ready for objectives.")
    print("💡 YOU CAN NOW TALK TO THE AGENT DIRECTLY IN MINECRAFT GAME CHAT!")
    if not chat_only:
        print("   Or type objectives below in this terminal (e.g. 'mine 3 oak logs', 'craft wooden planks').\n")
        print("   Type 'memory' for saved progress or 'memory status' for graph health.\n")

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

    if chat_only:
        print("Chat-only mode: send objectives in Minecraft. Stop the launcher with Ctrl+C.")
        # Background launchers have no interactive stdin. Keep the bridge alive
        # without an input() worker that could hang shutdown or exit on EOF.
        await asyncio.Event().wait()
        return

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
        if objective.lower() in {"memory", "memory status"}:
            if not agent_graph.memory:
                print("World memory is disabled.")
            elif objective.lower() == "memory status":
                print(json.dumps(agent_graph.memory.status(), indent=2))
            else:
                state = await bridge.get_state()
                print(await agent_graph.memory.context(state, "world progress locations tasks"))
            return
        task = parse_resource_task(objective)
        if task:
            print(f"\n🔧 Running verified task: {task['name']}...")
            result = await execute_recorded_task(agent_graph, bridge, objective, task)
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
            if message.lower() in {"memory", "what do you remember?", "what do you remember"}:
                if not agent_graph.memory:
                    await bridge.send_chat("World memory is disabled.")
                else:
                    state = await bridge.get_state()
                    recalled = await agent_graph.memory.context(state, "world progress tasks locations")
                    # Exact checkpoint summaries first; keep chat bounded.
                    for line in recalled.splitlines()[:5]:
                        await bridge.send_chat(line[:220])
                return
            task = parse_resource_task(message, requester=username)
            if task:
                await bridge.send_chat(f"Starting verified task: {task['name']}.")
                result = await execute_recorded_task(agent_graph, bridge, message, task, username)
                if result.get("success") and result.get("verified"):
                    await bridge.send_chat(f"✅ {result.get('message', 'Task verified.')}")
                else:
                    await bridge.send_chat(f"❌ {result.get('message', 'Verified task failed.')[:180]}")
                return

            if not is_explicit_task_request(message, config.mc_username):
                return

            await bridge.send_chat(f"Planning objective: '{message}'...")
            final_state = await run_planner_objective(agent_graph, message, username)
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


async def execute_recorded_task(agent_graph, bridge, objective, task, requester=None):
    """Persist outcome semantics; raw execution must never become verified memory."""
    state = await bridge.get_state()
    try:
        result = await bridge.execute_task(task)
    except Exception as exc:
        result = {"success": False, "verified": False, "status": "unknown",
                  "message": f"Interrupted/unknown outcome: {exc}"}
        save_task_outcome(agent_graph, objective, task, result, state, requester)
        raise
    save_task_outcome(agent_graph, objective, task, result, bridge.latest_state or state, requester)
    return result


def save_task_outcome(agent_graph, objective, task, result, state, requester=None):
    """A disk/memory fault must not change the task's actual execution result."""
    if agent_graph.memory:
        try:
            agent_graph.memory.record_task(objective, task, result, state, requester)
        except Exception:
            logger.exception("Task outcome could not be saved to memory; game result is unchanged")


async def run_planner_objective(agent_graph: MinecraftAgentGraph, objective: str, requester=None):
    """Run the legacy free-form planner with explicit unverified-result semantics."""
    initial_state = {
        "objective": objective,
        "bot_state": {},
        "code": "",
        "execution_result": None,
        "error_trace": "",
        "retry_count": 0,
        "rag_info": "",
        "memory_context": "",
        "status": "started"
    }
    print(f"\n🧭 Running planner for objective: '{objective}'...")
    try:
        final = await agent_graph.graph.ainvoke(initial_state)
    except Exception as exc:
        save_task_outcome(agent_graph, objective, None, {
            "success": False, "verified": False, "status": "unknown", "message": f"Planner interrupted: {exc}",
        }, agent_graph.bridge.latest_state, requester)
        raise
    save_task_outcome(agent_graph, objective, None, {
        "success": final.get("status") in {"success", "unverified"},
        "verified": False, "status": final.get("status"),
        "message": final.get("error_trace") or "Generated-code attempt finished; objective not verified.",
    }, agent_graph.bridge.latest_state or final.get("bot_state", {}), requester)
    return final


def report_cli_planner_outcome(objective: str, final_state: dict):
    """Report planner status without mistaking code completion for task completion."""
    status = final_state.get("status")
    if status == "success":
        print(f"\n✨ OBJECTIVE COMPLETED SUCCESSFULLY: '{objective}' ✨\n")
    elif status == "unverified":
        print(f"\n⚠️ ATTEMPT FINISHED BUT IS UNVERIFIED: '{objective}'\n")
    else:
        print(f"\n❌ OBJECTIVE FAILED: '{objective}' (Reason: {final_state.get('error_trace', 'Unknown')})\n")

async def main(chat_only=False):
    loop = asyncio.get_running_loop()
    if chat_only:
        controller_task = asyncio.current_task()
        loop.add_signal_handler(signal.SIGTERM, controller_task.cancel)
    memory = None
    server = None
    try:
        bridge = MineflayerBridge(host=config.ws_host, port=config.ws_port)
        memory = WorldMemory(config) if config.memory_enabled else None
        if memory:
            bridge.state_callbacks.append(memory.observe)
            bridge.event_callbacks.append(memory.record_event)
            memory.start()
        server = await bridge.start()
        await run_agent_loop(bridge, memory, chat_only=chat_only)
    finally:
        if server:
            server.close()
            await server.wait_closed()
        if memory:
            await memory.close()
        if chat_only:
            loop.remove_signal_handler(signal.SIGTERM)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Minecraft agent controller")
    parser.add_argument("--chat-only", action="store_true", help="Accept game-chat objectives without terminal input")
    args = parser.parse_args()
    try:
        asyncio.run(main(chat_only=args.chat_only))
    except (KeyboardInterrupt, asyncio.CancelledError):
        sys.exit(0)
