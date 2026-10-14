# Architecture and capabilities

## Current status

This is an active prototype. The table distinguishes controlled real-server tests from acceptance in the user's survival world.

| Capability | Current status |
| --- | --- |
| Local Purpur server, Mineflayer bot, WebSocket bridge, and in-game chat | Wired end to end. |
| Mine named log types | Typed `mine_logs` skill. Recorded gameplay and headless real-server fixtures demonstrate oak collection; appropriate axes and bounded dropped-item recovery are used. Full-tree and other-species generalization still need tests. |
| Give a named player an exact count of held items | End-to-end wired through `give_item`; mock/unit-tested, not yet live-server tested. It verifies the bot's inventory decreased after a toss near the player, **not** that the player picked the item up. |
| Mine logs and give them to the chat requester | Demonstrated in the recorded local run. Verification proves toss near the player, not player pickup. |
| Logs, dirt, and cobblestone collection / chest delivery | Headless Purpur + Mineflayer tests pass individual collection, held-item shortfalls, exact partial-stack/multiple-stack transfers, and a batch of 10 of each resource. Each transfer reopens the chest to verify fresh inventory and container deltas. Survival-world acceptance still matters. |
| Stacked resource requests | The model now decomposes the request and declares criteria for the whole objective. Typed `execute_plan` remains a tested regression baseline. General model-led batch acceptance is still needed. |
| Controlled staircase escape | Typed `escape_staircase`; uphill chest/player approach can invoke it automatically when walking fails. A real-server fixture demonstrates a 3-block uphill escape without underfoot mining. Other terrain still needs testing. |
| Read-only agent inventory | Chat `inventory` reads fresh bot state even during tasks. `/jarvisinventory` or right-clicking the bot opens a live server-side inventory window. Plugin compiled against the installed Purpur API with offline event-safety tests; live-client acceptance is pending. |
| Block/tool/drop/recipe knowledge | Read-only lookup from the installed version-matched `minecraft-data` registry. Drop entries are conditional possibilities, not unconditional promises. |
| Model-led crafting, placement, digging and interactions | Generic tools are available to chat and terminal planning. Recipes come from Minecraft's registry, not item-specific action scripts. The model decides dependencies and recovery; broad reliability is not established. Smelting lacks a furnace-control tool. |
| Practice and experience-based learning | `./learn.sh` lets the model select objectives, act in varied isolated setups, reflect, and retrieve persistent lessons. It makes real inference calls. `./practice.sh` remains a fixed, zero-inference regression baseline. Neither updates model weights. |
| Construction grounding and simulated practice | Exact desired/observed cell facts, material shortfalls and per-height summaries support model-selected construction. `./learn.sh --backend simulator --suite construction` provides five synthetic task families with independent full-cell grading. It does not establish Mineflayer or normal-world building competence. |
| Persistent environmental knowledge graph / long-term world memory | Local SQLite checkpoints + Graphiti temporal graph with embedded FalkorDB Lite. Rejoins recover prior progress; task outcomes and discoveries are ingested in the background. Live gameplay acceptance testing is still needed. |

Normal chat no longer rejects crafting/building/farming verbs before asking the model. Status, inventory, memory and cancellation retain direct control routes. Physical operations are serialized and expired operations cannot issue new guarded actions. Already-issued server actions may still have partial effects, so interrupted outcomes remain unverified.

## Architecture

```text
Minecraft chat or terminal
        │
        ▼
Python controller (main.py)
  ├─ model-led observe → choose tool → act → verify → replan loop
  ├─ experience library (SQLite): outcomes, reusable lessons, retrieval
  ├─ exact world checkpoints / durable episode outbox (SQLite)
  ├─ Graphiti → embedded FalkorDB Lite → local Ollama extraction + embeddings
  └─ typed regression baseline + legacy opted-in JS experiments (unverified)
        │ WebSocket :8765
        ▼
Node Mineflayer bot
  ├─ version-matched game knowledge (drops, tools, block properties, recipes)
  ├─ safe typed resource / handoff / chest-transfer skills + batch execution
  ├─ generic recipe / craft / dig / place / interaction / container tools
  ├─ no-progress walking watchdog + bounded dropped-item recovery
  └─ revocable operation lifecycle; generated JS disabled by default
        │
        ▼
Local Purpur Minecraft 1.20.1 server
```

`agent/tool_agent.py` is the default planner (`TASK_PLANNER=model`). It uses fresh world state, registry tools, world memory and retrieved experience to choose actions. There is no wooden-sword-specific executor: `bot/tools.js` crafts any available registry recipe, while the model decides ingredients and stations. Model-declared criteria are frozen before physical actions; observations determine completion.

`TASK_PLANNER=baseline` restores `agent/intents.py`'s resource parser. Resource/navigation skills remain optional convenience tools with conservative policies; the model can instead compose generic tools. Primitive implementation and verification are still programmed—removing the task allowlist does not make API contracts, tool ranges or cancellation optional. The legacy JS planner is only available in explicitly opted-in baseline CLI experiments (`ALLOW_EXPERIMENTAL_CODE=true`); game chat never executes arbitrary generated JavaScript.

