# Minecraft agent operations

An experimental Minecraft agent that connects a local Purpur server to a Mineflayer bot and a Python/LangGraph controller. It is being developed for the Nebius × NVIDIA Global AI Hackathon, with local Ollama used during development to avoid consuming hosted-inference credits.

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

## Prerequisites

- Python 3.12 in the project's `minecraft-agent` Conda environment (the dependency pins in `requirements.txt` match this environment)
- Node.js and npm; `npm ci` uses the committed Node lockfile
- Java 21 **JDK** (including `javac` and `jar`) for the bundled Purpur 1.20.1 server and local inventory plugin
- A local Ollama installation for low-cost development, or Nebius Token Factory credentials for the production path

The server runs in offline mode so the bot can use a local `AI_Agent` identity. Do not expose that configuration to an untrusted network.

## One-command local launch

Once the dependencies and `.env` are set up, launch everything from one terminal:

```bash
cd "/Users/jaskirtkaler/Github Repo/Minecraft-Agents"
./start.sh
```

The root launcher uses `/Users/jaskirtkaler/miniforge3/envs/minecraft-agent/bin/python` automatically; Conda activation is not required. It reuses an already-running Ollama/Jarvis service, or starts Ollama if needed. Its model-readiness check only inspects installed model tags: it does **not** download models or request inference. Graphiti can begin background memory extraction with the configured memory model once the agent joins.

It builds the local inventory plugin using the server's cached libraries (no dependency downloads), then starts the Minecraft server, Python controller, and Mineflayer bot, waiting for each to be ready. When it prints **READY**, join Minecraft Java using **127.0.0.1:25565** (or the printed address if `MC_PORT` differs). Send objectives in game chat, for example `mine 3 oak logs and drop them to me`, or `memory` to recall saved world progress.

Keep this terminal open. **Ctrl+C** stops the bot/controller and sends Minecraft a console `stop` command to save the world. It stops only processes it started; a reused Ollama service remains running. Fresh logs are kept under the ignored `data/run/<timestamp>-<pid>/` directory as `server.log`, `controller.log`, and `bot.log`.

The launcher binds Minecraft and the controller to localhost, preserves the existing memory world ID, and refuses to launch duplicate services on occupied ports. `./start.sh --check` validates configuration, dependencies, and port availability without starting services. For a different installation path, set `MINECRAFT_PYTHON` or `MINECRAFT_JAVA` to the appropriate executable.

The bundled server and bot remain on **1.20.1**; installed ViaVersion provides newer Java-client compatibility. A “connection refused” message means the selected address has no reachable server, not that a server-version upgrade is required.

### Early development: Peaceful, no hunger

The default `MC_TRAINING_MODE=true` makes loaded worlds Peaceful and keeps both players and the bot at full food/saturation. It also prevents exhaustion. Mining, crafting, tool durability and inventories still use Survival rules; this is not Creative. The local JarvisDebug plugin applies the settings on server startup/world load and player join, including existing saves. Restart using `./start.sh` to load a newly built plugin. Your world is not reset.

In training mode, the human players in JarvisDebug's `allowed-viewers` list (currently `Pilot6117`) can also use `/time` and `/weather`, for example `/time set day`, `/time set night`, `/weather clear`, or `/weather rain`. The plugin grants only the [time and weather command permissions](https://docs.papermc.io/paper/reference/permissions/), without making the player an operator. The configured bot is excluded. Grants are removed on disconnect/plugin shutdown and restored on rejoin while training mode is enabled. Setting `MC_TRAINING_MODE=false` and restarting stops these grants.

To introduce hunger/combat later: set `MC_TRAINING_MODE=false` in `.env`, restart, then run `/difficulty normal` as an operator (or `difficulty normal` in the server console). Also set `difficulty=normal` in `server/server.properties` for future starts. When starting the server manually, set `training-mode: false` in `server/plugins/JarvisDebug/config.yml` or pass `-Djarvis.training.mode=false` before `-jar`.

### Faster model decisions

The `1/20` counter is labelled **planning decision**: twenty is a maximum number of action-planning decisions, not a required cycle or hidden reasoning stages. Quantity interpretation, goal review and later reflection are separate bounded model calls. A decision can propose up to `AGENT_BATCH_SIZE=4` concrete tool actions. They execute sequentially with fresh world/session checks and completion checks. A failed action discards the unexecuted tail; the model then replans from actual observations. No task-specific plan is prescribed by the controller.

Local Ollama uses a constrained JSON schema for supported goal/tool names and required arguments, omits frozen goals from later replies, and defaults to shorter replies (`PLANNER_MAX_TOKENS=1536`, `LOCAL_PLANNER_THINKING=false`). Extended hidden thinking can be restored for harder objectives; increase the reply budget too. Hosted settings/model selection are unchanged. See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) and [thinking compatibility](https://docs.ollama.com/api/openai-compatibility).

Graph retrieval runs once per objective; live state remains fresh each decision. Read-only queries no longer trigger redundant chest verification, and chest goals at the same coordinates share one read. Repeated invalid decisions stop after `AGENT_MAX_PLAN_ERRORS=3` consecutive errors. `AGENT_MAX_NO_PROGRESS_ACTIONS=4` stops consecutive physical actions with no observed inventory, block-goal, position or proven tool-mutation change; read-only queries do not reset that counter. Exact-target before/after block or property receipts can establish a useful prerequisite effect outside final-goal cells, such as tilling soil or clearing headroom; success flags alone cannot. Such effects do not prove task completion. This guard is not a general detector of walking in circles. The same physical action is also rejected after two identical no-progress executions, even when read-only queries intervene. Partial progress remains saved rather than being reported as completion. Modern crafting-window clicks are paced by one server tick inside the guarded craft operation to reduce optimistic-inventory resync failures; this never automatically retries an uncertain craft.

Exact experience is saved immediately. Optional model reflection happens after `AGENT_REFLECTION_DELAY=10` idle seconds and is preempted by a new request, so it does not delay the completion reply. Interrupted reflections keep the exact outcome; restarting may lose a pending prose reflection, not the gameplay trace. Learning runs explicitly finish reflection **between** episodes. Request duration/token counts and per-decision/per-tool timings are recorded in logs/results; these do not imply a measured hardware tokens-per-second speedup. Longer inference waits emit occasional chat updates, and `status`/`stop` still bypass inference.

Player delivery uses an `item_dropped` completion goal, distinct from crafting/holding an item. Verification requires a successful, verified tool receipt for the exact item/count/recipient. It confirms a drop near the player, not pickup. These typed contracts do not independently prove that a model interpreted every natural-language request correctly.

Recorded isolated acceptance checks with local `gemma4:26b` after these changes: `data/practice/20261007-005708-daf83d12/report.json` confirms 3 cobblestone + 2 dirt collected and deposited in **1 planning decision / 4 actions** (5.1 seconds planning, 15.1 seconds tools); `data/practice/20261007-005811-308dd825/report.json` confirms 2 wooden swords in **8 decisions / 14 actions**. These are individual runs, not a statistically controlled speed benchmark or proof of general mastery. Earlier failed attempts are retained too.

## Local setup and launch order

From the repository root:

1. Activate the project's Python environment. If your shell has not initialized Conda, use the environment's Python path directly instead.

   ```bash
   conda activate minecraft-agent
   python -m pip install -r requirements.txt
   ```

   The checked project environment is `/Users/jaskirtkaler/miniforge3/envs/minecraft-agent`. The install command is only needed to bootstrap or synchronize it; do not use the system Python for the controller.

2. Install the Node dependencies deterministically.

   ```bash
   npm ci --prefix bot
   ```

3. Create local configuration without changing the tracked example.

   ```bash
   cp .env.example .env
   ```

4. For local development, start Ollama and make sure the model named by `NEBIUS_MODEL` and `JARVIS_MODEL` is available. The supplied example uses `gemma4:26b`; change both values if you use another local model.

   ```bash
   ollama serve
   ```

   Graphiti search also needs the small local embedding model (one-time download):

   ```bash
   ollama pull nomic-embed-text
   ```

5. In a second terminal, start the Minecraft server.

   ```bash
   cd server
   ./start.sh
   ```

6. In a third terminal, start the Python bridge/controller from the repository root.

   ```bash
   conda activate minecraft-agent
   python main.py
   ```

7. In a fourth terminal, start the Mineflayer bot.

   ```bash
   npm start --prefix bot
   ```

Wait for the controller to report that the bot has connected before issuing an objective. Set `ENABLE_VIEWER=true` in `.env` if you want the optional local first-person viewer on `VIEWER_PORT`.

## Local Ollama development and Nebius production

The environment names retain the project’s Nebius integration terminology, but the development example routes `AsyncOpenAI` to Ollama’s local OpenAI-compatible endpoint:

```dotenv
NEBIUS_BASE_URL=http://localhost:11434/v1
NEBIUS_MODEL=gemma4:26b
```

`NEBIUS_API_KEY` must remain non-empty because the current controller validates it before generation; Ollama ignores the placeholder. Jarvis recipe/strategy requests use Ollama’s native `/api/chat` endpoint.

For a production or hackathon demo, replace the local placeholder key, endpoint, and model ID with the Token Factory values from your Nebius project. Use at least one NVIDIA open-source model, make a real runtime Token Factory call (or deploy/run on Nebius AI Cloud), and keep the actual credential only in `.env` or your deployment secret store.

Jarvis/Ollama is a development backend, not the permanent agent architecture. Default chat now calls the model through the same client. Token Factory can use the tool loop by changing endpoint/model/key, but the hosted model must support JSON response mode and that path still needs an integration run. Local action/curriculum reasoning defaults off (`LOCAL_PLANNER_THINKING=false`) with `PLANNER_MAX_TOKENS=1536`; enable thinking and increase the budget together for harder objectives. Reflection disables hidden local thinking. Hosted reasoning defaults are unchanged.

The [Voyager approach](https://voyager.minedojo.org/) motivates curriculum, reusable experience and environment feedback. This implementation lets the model choose objectives and save procedural lessons, but does **not** implement Voyager's executable skill generation or weight training. It is experience/reflection learning, not an RL optimizer: logged rewards do not update policy weights. Reliability needs repeated varied runs, not a single successful episode.

## Persistent world memory

The controller automatically enables memory. It writes to the ignored `data/memory/` directory, resolved relative to the repository (not the launch directory). Keep this directory across controller/bot restarts. It may contain player names and coordinates; do not commit it.

Set `MC_WORLD_ID` in `.env` to a stable name for your world, such as `local-survival-v1`. Keep it unchanged when rejoining, but **change it when replacing/resetting the world**. If omitted, the bot uses `MC_HOST:MC_PORT`; this cannot distinguish a new world served at the same address. Dimensions have separate namespaces. Inventory belongs to the bot, not to nearby players.

The memory layers serve different purposes:

- SQLite saves timestamped position/inventory checkpoints, known block locations, and task results immediately. On each new bot session the controller restores the preceding checkpoint. Partial nearby scans never imply that an unseen resource was destroyed; an explicit loaded-block change records removal/replacement.
- Graphiti extracts searchable temporal facts from compact join/task/discovery episodes, using `MEMORY_MODEL` and `MEMORY_EMBEDDING_MODEL`. It runs sequentially in the background with a durable retry outbox. A failed/stopped model does not discard checkpoints or block gameplay. A graph commit is explicitly saved to disk before its episode is acknowledged.
- Graphiti indexing pauses during physical tasks while exact SQLite observations continue. Least-retried episodes are selected first, preventing one failed extraction from starving new task outcomes. Cancelling an HTTP request may not immediately stop all server-side model computation.
- The planner receives historical memory along with fresh live observations. Remembered coordinates are hints, not proof a block still exists. Failed/unverified objectives are saved as such; a toss near a player still does not prove player pickup.

Type `memory` in the controller terminal or Minecraft chat to recall progress. `memory status` also works in game chat. `status` reports the active stage or last result, and `stop` cancels physical work without waiting for the task lock. A busy graph returns exact checkpoint context immediately; background extraction can take some time with a large local model.

Graphiti inference is configured **independently** of the Nebius action planner, and defaults to local Ollama even if you later switch the planner to Nebius. No hosted inference is used for memory unless you explicitly change `MEMORY_BASE_URL` and credentials. All clients (extraction, embeddings, reranking) are configured explicitly. Graphiti telemetry defaults off.

Only one controller should use a given memory directory. The embedded backend requires Python 3.12+ on macOS/Linux; no Docker or external graph service is needed. If the Mac backend reports a missing OpenMP library, install `libomp` with Homebrew as described by [FalkorDB Lite](https://github.com/FalkorDB/falkordblite). Set `GRAPHITI_ENABLED=false` to use exact checkpoints without the graph, or `MEMORY_ENABLED=false` to disable both.

This follows Graphiti's [Ollama-compatible client configuration](https://help.getzep.com/graphiti/configuration/llm-configuration) and [JSON episode ingestion](https://help.getzep.com/graphiti/core-concepts/adding-episodes). LangGraph still controls the workflow; Graphiti now supplies long-term memory.

## Supported chat objectives

Use clear quantities and log species. Examples:

```text
mine 8 oak logs
get 10 blocks of oak wood
mine 8 oak logs and drop them to me
give 3 spruce logs to Pilot6117
get 3 cobblestone and put it in the chest
get 2 dirt and put it in the chest
get 10 oak logs and 10 dirt and 10 cobblestone and add them to the chest
mine 3 cobble stone and drop them to me
put 3 cobblestone in the chest
mine a staircase up 4 blocks
get yourself out of the hole
what is stone?
how do I get cobblestone?
how do I farm wheat?
status
inventory
stop
```

With the default model planner you can also try `craft 2 wooden swords`, `craft and place a chest`, or a multi-resource delivery. These are model objectives, not promises of mastered capabilities. It can ask about ambiguity or report missing materials instead of the former blanket refusal. `to me` includes the requester's name in context; a recipient must be visible. Handoff proves a nearby toss, not player pickup.

`status`, `stop`, `inventory` and `memory` work directly, including during inference. The model is instructed to treat knowledge questions/corrections as dialogue, not physical tasks. Restart the Python controller/bot after code changes; an already-running process still uses its loaded code.

In baseline mode, `get` uses carried items and collects shortfalls; `mine` collects additional items. Quantities are 1–64, plans at most 12 steps, and unsupported mixed batches are rejected. The model planner chooses composition/collection mode instead. Both retain partial progress without reporting it as full completion.

## Inspecting Jarvis's inventory

- Type `inventory`, `Jarvis inventory`, or `what are you carrying?` in normal game chat for a timestamped live list with counts and bot slot IDs, plus equipment. This is a read-only bridge query, not a model or memory guess, and works while a physical task is active. Longer responses are paced to avoid chat-spam kicks.
- Type `/jarvisinventory` (alias `/jarvisinv`) or **right-click `AI_Agent`** for a live, read-only in-game window. Pressing **E** alone still opens your own inventory. The display shows storage in rows 1–3, hotbar in row 4, and helmet/chestplate/leggings/boots/offhand in row 5. The held-item indicator refers to the hotbar; it is not an extra stack.
- GUI items are detached copies. Clicks in either inventory, shift/number/double/creative clicks, drag/drop, and offhand swaps are cancelled while the view is open. Close it to interact with your own inventory again. An item on your cursor must be put away before opening. Bot disconnects close the view instead of displaying an apparently empty inventory.

`./start.sh` builds and installs `server/plugins/JarvisDebug.jar` before starting the server. **Restart the launcher after these changes**; restarting only the bot does not load a server plugin. The root launcher passes `.env`'s `MC_USERNAME` to the plugin, so custom bot names work. For manual server launches, build with `bash server-plugins/jarvis-debug/build.sh` while the server is stopped, and configure `bot-username` in `server/plugins/JarvisDebug/config.yml` after its first launch.

The GUI only targets the configured bot, not arbitrary players. It is restricted to server operators and the `allowed-viewers` list (initially `Pilot6117`) in that generated config. This permission is for a trusted localhost/offline-mode server, not authentication suitable for an exposed server. The chat list follows the existing public game-chat behavior. GUI ordering uses Bukkit's storage/equipment slots; the printed chat IDs use Mineflayer's protocol inventory slots and therefore differ.

Plugin source is in `server-plugins/jarvis-debug/`. Its offline safety test uses actual installed Bukkit event classes with mocked inventories; it does not test packet handling or a real Minecraft client. Run it without starting Minecraft:

```bash
bash server-plugins/jarvis-debug/build.sh --test
```

## Resource navigation and knowledge

Resource mining uses exposed faces with safe standing space, not simply the geometrically closest block. Ordinary pathfinding cannot dig, build towers, or place bridges; direct mining beneath the bot remains prohibited. Cobblestone collection requires a harvest-capable pickaxe without Silk Touch.

Walking stops after 15 seconds without improving toward the goal, or a 45-second walking deadline. A valid route with a long detour can still trigger this conservative watchdog; it is not a universal navigation solver. Collection equips an available suitable tool. If a broken block does not increase inventory, the bot tries a bounded, non-destructive route onto the dropped item/mined cell and checks again. It stops with `PICKUP_NOT_VERIFIED` if recovery fails, rather than breaking more blocks blindly.

Chest preflight windows close only once. After an exact transfer, verification uses a newly reopened server container snapshot, including its player-inventory region, rather than stale/optimistic inventory state. `DEPOSIT_NOT_VERIFIED` means an uncertain outcome, not proof that nothing transferred; it is never blindly retried. Chest capacity accounts for all resources in a batch together.

An uphill chest/player approach that has no walking route can instead plan a **controlled staircase escape**. It preserves each step's support block, clears headroom (including jump clearance), and checks the block became air before walking/jumping. Only a small terrain whitelist can be excavated; containers, workstations, logs/planks, and other non-whitelisted blocks are not targets. Liquid pockets, falling-block hazards, unloaded cells, missing tools, and changed terrain stop the operation. Limits per attempt: 8 blocks of rise, 12 horizontal steps, an 8-block horizontal radius, and 24 blocks excavated. `mine a staircase up 4 blocks` is an explicit escape command; `get yourself out of the hole` defaults to 4 blocks up, not guaranteed surface detection. The planner is bounded and can fail to find a route even when a more elaborate route exists.

This recovery is intended for the project's open mining area. It cannot distinguish player-built stone/dirt from natural terrain; protected-area/build ownership is not implemented. A staircase may alter terrain before the chest can be opened to inspect room. Chest errors now distinguish missing chests, route failures, and inspected-but-full chests. Collected staircase cobblestone contributes toward `mine_and_deposit`'s collection target; the deposit still verifies exact chest and inventory deltas. `status` and `stop` remain available during escape.

The lookup provides block properties, tools, conditional drops and recipes, not every loot condition or datapack. Generic inspection, digging, placement and interactions let the model attempt harvesting/replanting/hoeing; this is not a tested farm manager. Protected-build ownership is not implemented. Prefer isolated practice before broader digging/placement in valuable builds.

Verification has limits: the model translates the request into criteria, so a misinterpreted request can produce wrong criteria. Current checks cover counts, block names and proximity, not crop maturity or architectural quality. Exact gains reject overshoots. A model-declared `inventory_gain` with `comparison: "at_least"` allows recipe surplus only when the request permits a minimum quantity; the default remains exact, and container/handoff gains remain exact. Criteria cannot be weakened after actions. Fresh crafting windows verify each recipe round; fresh transfer windows verify container and player counts.

### Target grounding and crafting quantities

The planner receives live `farmingTargets` separating the soil block, the planting position one block above it, the occupant and crop properties, and valid inventory-item/resulting-block pairs. Unknown cells remain unknown rather than becoming empty planting sites. `occupied_by_bot` distinguishes an empty block from a cell the bot's body obstructs. `inspect(position)` supplies the target plus observed below/above blocks; `inspect(item)` distinguishes item and block identifiers. Wheat seeds are inventory items that plant a wheat block, not a wheat-seeds block or harvested wheat item. The explicit crop mapping currently covers wheat, beetroot, carrots, and potatoes, with names checked against the installed version's registry.

Before freezing crop-block criteria, the controller checks the resulting block identifier and reads the actual soil beneath the target. Dirt/grass is allowed as a future tilling prerequisite; placing a crop requires actual farmland. New planting/building goals can use `must_change: true`, so an existing matching block alone is not proof of new work. Jarvis chooses the cells and actions. `walk_to` with `adjacent: true` now stops beside the target outside its column, instead of accepting a position inside an air cell intended for planting.

Carried tree saplings also expose live `plantingTargets`. The read-only `planting_sites(item,radius)` tool searches supported soil with an empty cell above it. Oak, birch, spruce, jungle, acacia, dark oak and cherry saplings use placement facts checked against the installed registry and bundled Mojang dirt tag. Placement is distinct from tree growth; light, headroom and species-specific spacing still affect growth. Mangrove propagules and non-tree plants are not covered by this mapping.

Taking saplings from a chest is a prerequisite, not proof of planting. `container_loss` checks the source chest's **before minus after** count; `container_gain` checks **after minus before** for deliveries. A chest-to-planting request needs source removal and new world blocks, not a final inventory gain. Read-only discovery can run before criteria are frozen, even if Jarvis supplies an invalid draft alongside the query. Physical mutations still need complete observed criteria.

`GOAL_REVIEW_ENABLED=true` first makes one bounded model call interpreting explicit requested quantities **without seeing the proposed plan**. Generic arithmetic/cardinality checks compare that interpretation with the proposed final predicates. A second bounded model call reviews the complete request, including source/destination constraints and non-quantity outcomes. It receives the predicates' executable meanings and reasons before its verdict. Keeping 24 new logs **after** delivering 24 demands 48 new logs, not 24; the independent quantity check rejects that extra held-stock requirement even if the critic approves it. A rejection asks Jarvis to correct its goals before mutation. The interpretation is reused throughout the objective, not regenerated after failures.

The quantity interpreter now distinguishes ordinary `get`/`fetch` from explicit fresh collection: getting 27 logs for a chest permits carried stock and requires a chest gain of 27; getting 27 **additional/new** logs requires both fresh collection and delivery. Explicit mine/gather/collect requests require fresh collection too. The interpreted quantities are logged before goals are accepted or physical actions execute. Two read-only local-model probes passed this distinction after a normal-world request had been incorrectly interpreted as fresh collection; this small probe does not establish reliable interpretation of arbitrary requests.

New collection is a separate event outcome: resources can subsequently be delivered or consumed. `collection_progress` counts actual before/after inventory increases during executed gathering/digging tools, not promised actions, chest withdrawals or final held inventory. Completion requires that collection evidence as well as all final goals. Thus delivering 24 from existing stock alone cannot satisfy a request for 24 newly gathered logs, and delivery does not invalidate the verified collection. Potential completion also refreshes container observations instead of accepting cached chest counts.

These model calls remain fallible: the quantity interpreter can misunderstand language, unspecified counts rely on the full reviewer, and an outcome check is not a proof of every Minecraft mechanic. Seeded practice therefore also uses a server audit defined independently of the model. Remaining quantities and the objective-start inventory are supplied as arithmetic feedback, not a selected action sequence. Explicit `withdraw`/`deposit` tools also receive limits derived from their frozen chest goal. Both the controller and the freshly opened server window reject a transfer that would overshoot; Jarvis must choose a corrected count. No silent quantity substitution or automatic action sequence occurs.

### Construction grounding

For a `regions_match` objective, the planner receives a `construction_knowledge` packet derived from the frozen desired geometry, exact current goal observations and current inventory. It separates empty targets needing non-air blocks, natural-looking obstructions, other occupied mismatches, already-matching cells and unknown cells. Bounded coordinate samples span categories, terrain families and heights; exact counts and per-Y summaries include cells omitted from the samples. Missing, unloaded or contradictory observations remain unknown, never presumed air. Historical baselines are not substituted for current observations.

Desired and observed blocks refer to the same absolute `(x,y,z)` cell. A floor at Y=78 occupies Y=78; its below-support cell is Y=77. All bounds are inclusive (`max-min+1`) and signs matter: Z=-13 is not Z=13. Desired blocks do not prove that adjacent placement support exists. The model can inspect narrower regions or neighboring cells when it needs more information. The bounding box is not permission to modify undeclared cells.

Material arithmetic subtracts already-matching blocks and sums all currently held stacks. Placement consumes materials, so a completed build does not need to leave its input cobblestone held. Unknown target cells make the remaining requirement an upper bound. Same-name block/item arithmetic is conditional on registry-confirmed placeability; crop blocks must not be confused with their seed items. For a new request to finish an existing build, `must_change:false` can require complete final geometry while retaining correct blocks. Accepted goals and their original baselines cannot be rewritten midway through an attempt.

The goal reviewer also receives the compiled final geometry partitioned into exact same-block boxes, including explicit required air. A `perimeter_xz` label does not itself prove a hollow room: a one-cell-deep box has every cell on its boundary, and an omitted interior is unconstrained rather than required empty. The reviewer must compare the actual compiled cells with the request; it remains a fallible model check, so the simulator separately audits the requested geometry. Spatial dimensions and counts of existing cells do not become invented quantities of new blocks to place.

After complete region goals are accepted and their current spatial facts have been surveyed, the first proposed physical action is deferred once. Jarvis receives those grounded observations before choosing its first mutations again; the controller does not select a build order or change the proposed coordinates. Subsequent construction batch history keeps compact receipts for every returned cell, including placed, cleared, skipped and failed coordinates, instead of silently truncating a 64-cell batch to eight cells. Full raw evidence remains in the saved trace. These are observation and verification improvements, not model-weight training or a guarantee of reliable construction.

Structurally valid construction drafts emitted during read-only discovery are retained as **unaccepted drafts**, not frozen goals. Jarvis must resubmit its complete criteria for grounding and review before physical work. Rejected drafts can also supply clearly labelled current facts for correction; unreadable cells stay unknown with observation errors rather than prematurely aborting the next planning decision. Neither kind of draft authorizes a mutation.

### Action checking and controlled own-placement repair

After grounding and intent review, accepted spatial cells are installed through a trusted controller-only handshake bound to an objective ID and the observed world/session/dimension. This handshake is not a model tool. Both planner and executor reject permanent construction batches that contradict a final cell, including required air and signed-coordinate scope. Rejection applies to the whole batch before its first mutation: coordinates/order are never silently filtered, substituted or reordered. Correct final solid cells cannot be demolished, including through convenience gathering tools. Legacy task/code execution is unavailable while a construction contract is active, preventing a parallel bypass of typed-tool checking.

`repair_batch {positions:[...],tool?:item}` is a separate explicit capability. The model chooses its positions and any later replacement; the executor supplies no correction sequence. Ownership comes only from actual verified air-to-block placements with an exact one-item inventory decrement, recorded behind an opaque task handle. Single ordinary structural placements inside the accepted geometry share that executor. Caller-supplied ownership/task claims are rejected. The read-only `construction_repair` packet prioritizes eligible owned mismatches rather than hiding them behind a large completed floor.

Before every repair, the executor checks that the original task is active, the cell is loaded and inside the frozen geometry, the placed block's full name/state/properties and observed revision still match its receipt, and the block is currently wrong for the final goal. Observed intervening changes—including same-name replacement or unloading—invalidate ownership. Correct blocks and pre-existing/player blocks are never authorized by these receipts. Repairs also require suitable carried tools, safe neighboring reach/footing and loaded hazard checks; they refuse liquids/falling blocks, fixtures/stateful mechanisms, or undermining the bot/another observed player. A stable ordinary cobblestone roof may remain above an interior repair. Actual removal must be observed as air; partial verified removals remain in receipts if a later cell fails. Removal does not assert pickup or overall construction completion.

Task completion, cancellation, disconnection, respawn/world change or replacement of the objective revokes the ledger. This first version is **session-local**, not permission to remove old mistakes after restarting. Old logs, Graphiti memory and model assertions cannot establish live ownership; older ambiguous blocks stay untouched. A fresh “finish/repair existing build” request uses `must_change:false`, retaining completed cells; an ongoing new-build attempt never rewrites its original baseline. Physical `diggable` observations describe block mechanics, not demolition permission. Desired orientation/property goals, temporary scaffolding, persistent ownership and general fixture repair are not implemented.

This is outcome consistency and controlled recovery, not reinforcement-learning updates or a security/authentication layer for the existing trusted local bridge. The model still must declare correct complete geometry and choose effective actions. Independent task audits remain necessary because the goal reviewer can misunderstand a request. Failed cancellation sends and unacknowledged cleanup are logged without losing the partial attempt or masking its original error.

For a single-cell `block_is(...,must_change:true)` goal, a verified exact-cell dig/repair/placement receipt can prove a real in-attempt change even when the final block matches its original baseline (for example air → mistaken own cobble → repaired air). Baselines are not rewritten. Only known changed before/after cell evidence counts; partial batches prove their verified changed cells, not skipped/failed ones. Ordinary single placement additionally needs actual one-item consumption; `already_correct:true` with zero inventory change cannot masquerade as new planting/building. Region `must_change:true` continues to require every requested final non-air cell to differ from its original baseline.

### Gathering time limits and continuation

Gathering no longer inherits the generic 60-second tool deadline. `shared/tool_timing.json` sets a hard limit of `min(300 seconds, 90 seconds + 8 seconds × requested count)` for each `mine_logs`/`mine_resource` call: 16 items receive 218 seconds and 64 receive 300 seconds. A separate 60-second inactivity limit watches actual inventory increases or measurable advance toward the current walking goal. Repeated status text does not extend it, and progress never extends the hard limit. The Python reply budget is the same tool limit plus 20 seconds; the overall objective limit remains `AGENT_TIMEOUT`.

After a gathering timeout, the controller waits up to 15 seconds for the matching expired operation to settle. Only an idle handshake and an unchanged bot/world session permit another model decision. Jarvis sees fresh inventory, partial gains and the unchanged original criteria, then chooses whether and how to continue. The failed batch's unexecuted actions are discarded. Unsettled work, player cancellation, uncertain transfer/craft timeouts and changed sessions still stop the objective; no blind retries occur. Log collection mines safely reachable overhead/beside targets without unnecessary walking. When walking is needed, its goal rejects standing above the log while allowing safe same-column positions below it; actual stance and reach are rechecked before digging. Mining underfoot remains forbidden.

Goal drafts rejected before physical work are not frozen. The next observation explicitly asks for a corrected, complete goal array; only accepted non-empty goals authorize later replies to omit it. A source-removal goal keeps the full requested chest count even when the model chooses smaller transfers. Existing carried items can reduce a fetch shortfall, but cannot reduce an explicit request to take a count from a chest.

Both explicit chest transfers and `deposit_item` now carry limits derived from the accepted goal baseline. A convenience deposit is bound to that goal's chest, rather than whichever chest is nearest or has room, and freshly opened container counts reject overshooting transfers before mutation. If several goal chests match the item, the model must choose an explicit `deposit` destination. A missing or full bound chest does not trigger an unrequested fallback. Exact new retained-inventory requests require an equality predicate; minimum-only predicates remain appropriate for explicit minimums and permitted recipe surplus.

Experience traces retain tool durations, execution budgets, timeout details, idle handshakes, goal-review verdicts and partial observations. SQLite task records are durable even when Graphiti indexing is delayed. Retrieved lessons include their checked goals and whether those goals passed a coverage review, without rewriting old records. Historical success labels only describe the criteria checked at the time; an older inventory-only sapling success does not establish that planting happened.

`recipes(item,count)` reports output per batch, rounded recipe rounds, total ingredients, material shortfalls, surplus output, and projected inventory. `craft_budget` accepts a model-proposed list of crafting steps and projects ingredient consumption through that list without executing or inventing prerequisites. For example, crafting eight planks and then four sticks consumes two planks, leaving six planks and four sticks. To finish with at least seven new planks and eight new sticks from logs alone, twelve produced planks leave eight after four are consumed. Final goals concern what remains held, not an earlier intermediate count.

The budget is arithmetic, not a promise that a station exists or execution will succeed. A `recipe_index` selects a preview variant; actual crafting still selects a currently craftable recipe at its supplied station. Jarvis must interpret the preview and choose or revise its actions. Placement support, tool reach, and fresh server outcome checks remain execution contracts, not hard-coded task sequences.

## Tests and checks

Run the Python controller/memory/planner contracts and Node gameplay-tool checks:

```bash
./check.sh
```

This selects the project `minecraft-agent` Conda Python and runs the Python, Node, shell/JavaScript syntax and cached Java plugin contracts. The October 10 controlled-repair check passed 337 Python tests (one opt-in live-memory test skipped), plus all Node and Java checks. Its private log is `data/construction-practice/offline-check-controlled-repair-complete-20261010.log`. Offline checks do not establish live repair competence.

### Autonomous model-led practice

Start your local Ollama service, then run from the repository root without a Minecraft client:

```bash
./learn.sh --episodes 3
./learn.sh --episodes 1 --objective "craft 2 wooden swords"
./learn.sh --episodes 3 --seed 42
ENABLE_TAVILY=false AGENT_MAX_STEPS=10 AGENT_TIMEOUT=300 ./learn.sh --suite grounding --episodes 6 --seed 320011
ENABLE_TAVILY=false AGENT_MAX_STEPS=15 AGENT_TIMEOUT=600 ./learn.sh --suite recovery --episodes 3 --seed 360121
```

Without `--objective`, the model chooses each next measurable task from current observations, previous lessons and per-objective attempt/success counts. There is no fixed task list or solution sequence. Each episode varies supplied resources, station availability, resource positions and crop ages in a small peaceful arena. The model checks recipes, selects tools/actions, receives errors and replans. Learning uses the configured local model or a compatible Token Factory backend, refusing a non-local endpoint unless you explicitly pass `--allow-hosted` (which can incur costs). It does not start Ollama or download a model.

`--suite grounding` instead supplies benchmark requests, never solution actions: finish with minimum new planks/sticks at two quantities, plant exactly two/three fresh wheat crops while preserving existing crops, and deposit exact cobblestone/dirt amounts at two quantities. All six episodes run on a real headless Minecraft server with Mineflayer, not the RL grid simulator. Seeds vary soil/standing height, farm and chest positions, resource positions, and starting log counts; task families also differ in height between quantity variants. Tools and seeds are supplied and crop growth is paused, so these are controlled mechanics tests rather than survival or general farming competence. Use a fresh seed for another layout; the seed fixes the fixture, not stochastic model decisions or the shared experience library.

For this suite, the server audits requested outcomes independently of Jarvis's declared criteria: final inventory after intermediate consumption, exact chest gains, and newly placed crops in initially empty cells with existing crops preserved. Passing requires both this audit and the model-goal check. A wrong interpretation cannot pass just by satisfying weaker self-declared goals. General chat/curriculum episodes still depend on the model's interpretation; this benchmark does not solve arbitrary intent verification.

`--suite recovery` supplies three requests: take and plant three saplings, collect 24 additional oak logs and deliver exactly 24, then take and plant five saplings. Starting stock intentionally differs from requested quantities. The server audits source chest losses, newly planted blocks, preservation of existing plants, fresh log collection and exact delivery. Heights, chest positions and garden soil vary with the seed. Jarvis chooses all actions; the timeout continuation itself is also covered by offline cancellation/partial-progress tests rather than forcing a timeout into every live episode.

For a targeted retest, use `--suite-task` with `--episodes 1`, for example `./learn.sh --suite recovery --suite-task distant_logs_24 --episodes 1 --seed 390151`. It selects a request/fixture, not a solution or action sequence.

Exact attempts and model-generated procedural lessons persist across runs in `data/learning/experience.sqlite3` (`LEARNING_DIR` overrides this). Retrieval provides past outcomes/strategies, not live coordinates to replay. World-specific observations still live in the separate world memory/Graphiti layer. Practice disables Graphiti ingestion for its isolated world, but shares the strategy library intentionally. Local graph extraction is paused while the gameplay model runs to avoid competing inference workloads.

Every run saves `report.json`, `model.jsonl` (actual model requests/responses), bot/server logs and the disposable world under `data/practice/`. Server-authoritative counts independently audit the model-declared goals; failed or disputed outcomes are not promoted as mastered skills. The logged binary reward is diagnostic, **not** gradient training. These files contain gameplay context/player names/coordinates and should remain private/ignored.

Controls: `AGENT_MAX_STEPS=20`, `AGENT_TIMEOUT=600` seconds for an objective, `MODEL_STEP_TIMEOUT=120` for one model request. Optional reflection has its own bounded request after execution. Generic tools process at most 64 items per call, with loaded-range and operation timeouts; larger work requires multiple model-selected calls. Ctrl+C preserves artifacts and stops only the owned practice server/bot. Your normal world, world memory and existing Ollama service are not reset or shut down.

This removes the hand-written task allowlist, not every execution contract. Building/farming are now attemptable compositions, not universally reliable capabilities. Successful episodes are evidence to accumulate; failures need investigation, not automatic “learned” labels. The current arena does not establish open-world survival/generalization. The model-based goal review is fallible, and broader intent verification and mature-crop/farm management remain unfinished.

Local-model evidence on October 6, 2026: a two-wooden-sword objective was independently confirmed after two earlier unsuccessful attempts. Three model-selected cobblestone episodes passed across varied setups; with progress history and reasoning enabled, the model then selected and completed a stone-pickaxe objective. The pickaxe run included collecting cobblestone, processing logs into planks/sticks, crafting/placing a table and crafting the pickaxe. These are controlled examples, not a general success-rate estimate or proof of autonomous farming/building. Reports and raw model traces remain in the ignored practice directories.

On October 8, 2026, local Jarvis (`gemma4:26b`) passed all six grounding-suite cases on fresh seeds: two final planks/sticks quantities, exact planting of two/three wheat crops at different heights, and two exact cobblestone/dirt chest deliveries. The first six-case attempt passed 5/6 because one correctly planted task had been misinterpreted as harvested-wheat inventory gain. The next run used clearer goal instructions and corrected adjacent walking; it also shared written lessons, so the result is not a frozen-policy comparison. Jarvis still used small crafting batches rather than the available draft-budget tool. The [run summary](../data/practice/20261009-051253-98366078/summary.md) records observations and remaining limits. No weights were trained or paid API called.

Later local recovery tests confirmed taking/planting three and five saplings, then a targeted 24-new-log collection and exact delivery. The log operation took 74 seconds, exceeding the old 60-second deadline without interruption. Results span two runs; the three-case run was 2/3 before the collection-evidence correction, and the corrected wood retest was 1/1. The [recovery summary](../data/practice/20261009-064003-5c9242ba/summary.md) preserves failures, source-count recovery and verification limits. Normal-world acceptance of the original 64-log request remains pending.

On October 10, a fresh three-case recovery suite passed 1/3: Jarvis gathered 24 new logs and deposited exactly 24, but both sapling cases were rejected before physical actions because the proposed source counts or complete goals were wrong. The 78-second gathering action confirmed the extended budget again; the suite does not establish reliable sapling planning. Goal-acceptance wording and the additional transfer/equality guards above were revised after this run started and need a new live acceptance run. The [retest findings](../data/practice/20261010-191533-ec2a98bf/summary.md) keep the failed cases with the successful collection rather than reporting an all-green result.

The same session found and corrected a log-approach regression: excluding the entire log column also excluded safe overhead mining. A separate zero-inference server run passed overhead-log collection/delivery, ordinary log collection/delivery and a partial-stack chest deposit (3/3). The overhead fixture supplied only three logs at y66–68 over stable ground; the oracle recorded exactly those three breaks, chest gain 3, health 20 and zero underfoot breaks. This verifies the mining primitive, not Jarvis's planning. Repeat it with `./practice.sh --cases overhead_logs,collect_logs,partial_stack --timeout 120`.

The concurrent normal-world request for 27 oak logs finished unsuccessfully: its quantity interpreter required 27 fresh logs on top of 8 carried logs, so inventory reached 35. Three return-to-chest walking attempts then failed with `NO_NAVIGATION_PROGRESS`; repeated proposals stopped without a deposit. Partial gathering gains and the failed result were saved. Real-terrain pickup/navigation and sapling planning remain open acceptance issues; the new source edits need a clean normal-stack restart after current gameplay finishes.

A later unattended run loaded the updated code and passed all three recovery requests with seed 401031 using local `gemma4:26b`: take/plant 3 saplings, gather/deliver 24 additional logs, and take/plant 5 saplings. Server audits confirmed exact source removals, new planting, preservation of existing plants, and exact delivery; health remained 20 with no underfoot breaks. The 24-log action took 86.266 seconds within its 282-second budget, and `deposit_item` delivered 24 while leaving the starting 6 logs held. In the five-sapling case, Jarvis proposed a repeated withdrawal that would exceed the fixed source count; the guard rejected it, then Jarvis selected the remaining 2. These are controlled peaceful layouts with tools supplied, not proof that the normal-world chest route is fixed. The [unattended findings](../data/practice/20261010-193457-9f174aca/summary.md) retain the recovery detail and limits. The run used 21 local inference calls and shared written experiences; it is not a frozen-policy comparison or weight training.

### Local simulated construction practice

The construction backend runs Python-only spatial practice without starting a Minecraft server, Mineflayer, Graphiti or a Minecraft client. It uses `/Users/jaskirtkaler/miniforge3/envs/minecraft-agent/bin/python` through `learn.sh` (or the `MINECRAFT_PYTHON` override). Start your existing local Ollama/Jarvis service for actual episodes; the offline check and task listing need no model service:

```bash
./learn.sh --backend simulator --suite construction --check
./learn.sh --backend simulator --suite construction --list
./learn.sh --backend simulator --suite construction --episodes 5 --seed 42 --timeout 600 --max-steps 12
./learn.sh --backend simulator --suite construction --task repair_shelter --episodes 2 --seed 203 --timeout 600 --max-steps 12
```

The five seeded task families are:

- `negative_floor`: build a correctly sized floor at signed coordinates, with enough starting material.
- `obstacle_floor`: clear observed natural obstructions, build the floor and leave the requested space above it empty.
- `footing_floor`: fill two explicit foundation holes without confusing the support height with the floor height.
- `shelter`: build a small floor, perimeter walls and full roof, with empty interior and a two-high doorway.
- `partial_row`: retain an already-correct cobblestone row and complete only the missing floor cells.

These are benchmark requests and layouts, not scripted solutions. Jarvis declares its own goals, chooses every tool action and reacts to actual synthetic observations/errors. An independent oracle audits every requested final cell, fixture preservation and unwanted changes; satisfying a weak self-declared goal cannot pass the task. `--check` tests fixture/oracle integrity with zero model calls or files written; it is not a Jarvis evaluation. `--seed` varies origins, heights, dimensions and obstructions. `--task partial_row --episodes 3` repeats one family on successive layouts, while `--model` selects an installed local Ollama model (default `JARVIS_MODEL` or `gemma4:26b`).

`--timeout 600` is the total run budget, not ten minutes per episode. `--max-steps 12` is the maximum number of planning decisions per episode, not required rounds or hidden reasoning stages. A timed-out or interrupted suite retains its partial results and reports how many requested cases were actually evaluated. Ctrl+C preserves the private artifacts.

This backend is local Ollama only at `http://localhost:11434/v1`; it does not use Nebius or Tavily, train neural-network weights, deploy an RL policy or access the normal world/memory. It models synthetic blocks, inventory consumption and adjacency support, **not** Mineflayer pathfinding, interaction reach, item pickup, protocol packets, lighting, growth, mobs or full Minecraft physics. Its `mine_resource(cobblestone)` is a finite synthetic dispenser, not evidence of verified stone mining or collection. Passing requires a subsequent real disposable-Minecraft test before making gameplay claims.

Artifacts live privately under `data/construction-practice/<UTC timestamp>-<unique ID>/`: `report.json`, actual local model requests/responses in `model.jsonl`, tool calls in `tools.jsonl`, episode layouts and a run-local experience directory. Attempts are recorded honestly; failed or incomplete builds are not labelled mastered. These experiences can inform later episodes in the same run but are not automatically promoted to the normal gameplay library, Graphiti or a deployed skill/policy. Shared written experience is separate from gradient training.

The optional `repair_shelter` family is not part of the default five-case suite. It starts with a completed protected shelter, then trusted harness setup creates two actual verified current-task misplaced cobbles in final-air cells after contract registration. The setup is labelled separately from model actions; its provenance is required by the independent oracle, so leaving an uninjected completed fixture untouched cannot falsely pass. Jarvis must select the repair tool itself. Simulator `diggable` now reflects physical breakability rather than the natural-only ordinary-dig permission; `protected` and backend repair facts supply permission separately. Simulated revisions/property fingerprints exercise stale-ownership rejection, but do not establish real server physics, pickup or protocol behavior.

On October 10, 2026, local `gemma4:26b` completed all four non-shelter families in two five-case runs: seeds 42–46 and 100–104, each **4/5 passed**. The earlier diagnostic run was 0/5. Source/prompt revisions occurred between runs and each suite shared its own written experiences, so these are development results, not a frozen-policy comparison. Reports are in run directories `20261010-215421-09b191ff` and `20261010-215938-da8d1cc6`.

After retaining discovery drafts and exposing compiled geometry to the reviewer, a focused shelter retest was **0/2** (`20261010-220423-04d1aade`). Seed 103 had the correct accepted hollow-room goal but finished at 74/80 matching cells: six wrongly placed cobblestone blocks occupied required air, and construction-block protection prevented removing them. Seed 104 failed on overlapping air/cobblestone goal declarations before building. That diagnostic motivated the action-checking/controlled-repair implementation above; knowledge/context improvements alone had not produced a reliable house builder. All these runs were synthetic and local-only, with no normal-world edits or weight training.

Controlled-repair development used explicit fixture seeds 203/204. The initial run (`20261010-221706-e9c217f1`) was **0/2**: Jarvis kept selecting ordinary dig. Separating physical `diggable` from permission and exposing `REPAIR_REQUIRED` feedback produced model-selected `repair_batch` on both layouts (`20261010-222050-f0948759`), restoring 80/80 and 100/100 cells. Its recorded task result was **1/2**, not 2/2: the second controller rejected real repair evidence for two extra `block_is(air,must_change:true)` goals whose original baseline was air. Exact per-cell mutation-proof support fixed that verifier edge without rewriting baselines. A fresh seed-204 retest (`20261010-222413-ff56096e`) was **1/1 passed**, 100/100 cells in four decisions, after Jarvis itself switched from rejected ordinary dig to repair. No preservation violations or completed-block demolitions occurred. These source/prompt revisions are development retests, not a frozen-policy benchmark; fixture repair does not prove autonomous live house construction.

The subsequent default construction regression (`20261010-222522-58c705ee`, seeds 300–304) evaluated all five families and passed **4/5**: negative floor 12/12 cells, obstacle floor 45/45, footing floor 14/14 and partial row 20/20. The shelter stopped at 34/80. Its accepted goal omitted some required wall cells despite model review; proposed wall batches then included those undeclared cells and were rejected whole. Repeated no-effect proposals stopped the attempt rather than modifying scope or weakening the original criteria. No preservation violations or completed-block demolitions occurred in any case. Goal completeness and useful replanning after rejection remain open planning issues; the guard's correctness is not a claim that Jarvis can complete a house.

### Headless regression baseline

Run these from the repository root, without launching the normal stack or a Minecraft client:

```bash
./practice.sh --list
./practice.sh
./practice.sh --cases batch,staircase
./practice.sh --cases held_deposit,partial_stack,multiple_stacks --repeat 3
```

The default regression suite covers 11 cases: exact held/partial/multiple-stack deposits, collection of logs/dirt/cobblestone, the three-resource batch, missing pickaxe, full chest, inaccessible stone, and an unsupported batch. `staircase` is an optional twelfth case. Expected safe refusals pass only when the expected error is returned and authoritative state shows no unrequested excavation or inventory change. Collection/delivery is graded on server chest counts, remaining inventory, excavation counts, health, and underfoot-mining violations—not just the bot's success message.

Each run creates `data/practice/<UTC timestamp>-<unique ID>/` with its own disposable Purpur world, SQLite memory, `report.json`, `server.log`, and `bot.log`. It copies cached server runtime artifacts, **not** the normal worlds or plugins; uses separate ephemeral loopback ports; disables Graphiti indexing and model inference; and starts only its owned server/bot. No Ollama or Nebius call is needed. The console-only `PracticeOracle` fixture plugin is built locally and installed only in this guarded practice server. Ctrl+C stops owned processes and preserves artifacts. Each run consumes disk space (cached runtime plus a small world); runs are retained for inspection rather than automatically deleted.

On October 6, 2026, a complete 12-case baseline run passed on the installed Purpur 1.20.1 server. These are small deterministic, peaceful fixtures with tools supplied and exposed ground-level resource blocks. Passing them does not establish natural-tree traversal, survival under enemies, farming, arbitrary navigation, or model-led success. Retain regression checks when tools change; they are not the model's learning curriculum.

After the controlled-repair source changes on October 10, a real disposable-server smoke run passed overhead-log collection/delivery, partial-stack chest deposit and controlled staircase escape (3/3, `20261010-223114-7d792f8d`). Server-authoritative checks confirmed the requested results, health 20 and no unsafe breaks. This used zero model calls and left the normal world untouched; these cases exercise existing gameplay primitives, not the new owned-repair path. Repeat with `./practice.sh --cases overhead_logs,partial_stack,staircase --timeout 120`.

`--timeout` sets a per-case deadline (default 90 seconds, allowed 5–180). A timed-out case cancels work and aborts the suite to avoid overlapping a reset with unsettled actions. Use `MINECRAFT_PYTHON` / `MINECRAFT_JAVA` for other installations; a Java 21 JDK and the existing cached Purpur runtime are required.

You can also validate JavaScript syntax without starting Minecraft:

```bash
node --check bot/index.js
node --check bot/skills.js
node --check bot/sandbox.js
node --check bot/state.js
```

This workspace may contain additional ignored scripts under `scratch/` for sandbox and mock-skill checks. They are useful while developing locally but are not part of the tracked, reproducible test suite or a substitute for a live-server integration test.

An opt-in Graphiti/Ollama check ingests a synthetic verified task into a temporary graph, closes/reopens it, recalls the fact, and checks replay/world isolation. It never connects to Minecraft or adds synthetic data to your real memory directory:

```bash
RUN_MEMORY_LIVE_TEST=1 python -m unittest discover -s tests -p test_memory_live.py -v
```

## Hackathon checklist

The [official rules](https://nebiusglobalaihackathon.devpost.com/rules) control. This checklist was last checked on 2026-10-04 (Pacific time); the published submission deadline is October 30, 2026 at 10:00 AM PDT.

- Build a working application in an eligible track that runs on Nebius Token Factory or Nebius AI Cloud and uses at least one NVIDIA open-source model.
- Keep a public, open-source repository with the MIT license, all required source/assets, and clear setup instructions.
- Document exactly where the NVIDIA model and Nebius Token Factory or AI Cloud are used. For this project, a live Token Factory call must be part of the production/demo path; local Ollama is a development-cost optimization, not proof of that requirement.
- Provide a working demo, hosted app, or test build; an English project description; and a public YouTube demonstration video under three minutes that shows the project functioning.
- If the project predates the submission period, explain the significant hackathon-period changes. Keep third-party integrations and media properly licensed.

The next demo milestone is repeated independently confirmed model-led crafting/delivery, followed by farming and terrain recovery across varied setups, then a Token Factory runtime integration and live-survival acceptance recording.

## License

[MIT](../LICENSE)
