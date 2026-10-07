# Minecraft Agents

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

To introduce hunger/combat later: set `MC_TRAINING_MODE=false` in `.env`, restart, then run `/difficulty normal` as an operator (or `difficulty normal` in the server console). Also set `difficulty=normal` in `server/server.properties` for future starts. When starting the server manually, set `training-mode: false` in `server/plugins/JarvisDebug/config.yml` or pass `-Djarvis.training.mode=false` before `-jar`.

### Faster model decisions

The `1/20` counter is now labelled **planning decision**: twenty is a maximum number of model calls, not a required cycle or hidden reasoning stages. A model decision can propose up to `AGENT_BATCH_SIZE=4` concrete tool actions. They execute sequentially with fresh world/session checks and completion checks. A failed action discards the unexecuted tail; the model then replans from actual observations. No task-specific plan is prescribed by the controller.

Local Ollama uses a constrained JSON schema for supported goal/tool names and required arguments, omits frozen goals from later replies, and defaults to shorter replies (`PLANNER_MAX_TOKENS=1536`, `LOCAL_PLANNER_THINKING=false`). Extended hidden thinking can be restored for harder objectives; increase the reply budget too. Hosted settings/model selection are unchanged. See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) and [thinking compatibility](https://docs.ollama.com/api/openai-compatibility).

Graph retrieval runs once per objective; live state remains fresh each decision. Read-only queries no longer trigger redundant chest verification, and chest goals at the same coordinates share one read. Repeated invalid decisions stop after `AGENT_MAX_PLAN_ERRORS=3` consecutive errors, and identical no-progress actions are not repeatedly executed. Modern crafting-window clicks are paced by one server tick inside the guarded craft operation to reduce optimistic-inventory resync failures; this never automatically retries an uncertain craft.

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

Jarvis/Ollama is a development backend, not the permanent agent architecture. Default chat now calls the model through the same client. Token Factory can use the tool loop by changing endpoint/model/key, but the hosted model must support JSON response mode and that path still needs an integration run. No hosted credits were used to develop the loop. Local action/curriculum reasoning defaults on (`LOCAL_PLANNER_THINKING=true`) with `PLANNER_MAX_TOKENS=4096`; turning it off reduces latency but may weaken decisions. Reflection disables hidden local thinking. Hosted reasoning defaults are unchanged.

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

Verification has limits: the model translates the request into criteria, so a misinterpreted request can produce wrong criteria. Current checks cover counts, block names and proximity, not crop maturity or architectural quality. Exact gains reject overshoots. Fresh crafting windows verify each recipe round; fresh transfer windows verify container and player counts.

## Tests and checks

Run the Python controller/memory/planner contracts and Node gameplay-tool checks:

```bash
python -m unittest discover -s tests -v
npm test --prefix bot
```

### Autonomous model-led practice

Start your local Ollama service, then run from the repository root without a Minecraft client:

```bash
./learn.sh --episodes 3
./learn.sh --episodes 1 --objective "craft 2 wooden swords"
./learn.sh --episodes 3 --seed 42
```

Without `--objective`, the model chooses each next measurable task from current observations, previous lessons and per-objective attempt/success counts. There is no fixed task list or solution sequence. Each episode varies supplied resources, station availability, resource positions and crop ages in a small peaceful arena. The model checks recipes, selects tools/actions, receives errors and replans. Learning uses the configured local model or a compatible Token Factory backend, refusing a non-local endpoint unless you explicitly pass `--allow-hosted` (which can incur costs). It does not start Ollama or download a model.

Exact attempts and model-generated procedural lessons persist across runs in `data/learning/experience.sqlite3` (`LEARNING_DIR` overrides this). Retrieval provides past outcomes/strategies, not live coordinates to replay. World-specific observations still live in the separate world memory/Graphiti layer. Practice disables Graphiti ingestion for its isolated world, but shares the strategy library intentionally. Local graph extraction is paused while the gameplay model runs to avoid competing inference workloads.

Every run saves `report.json`, `model.jsonl` (actual model requests/responses), bot/server logs and the disposable world under `data/practice/`. Server-authoritative counts independently audit the model-declared goals; failed or disputed outcomes are not promoted as mastered skills. The logged binary reward is diagnostic, **not** gradient training. These files contain gameplay context/player names/coordinates and should remain private/ignored.

Controls: `AGENT_MAX_STEPS=20`, `AGENT_TIMEOUT=600` seconds for an objective, `MODEL_STEP_TIMEOUT=120` for one model request. Optional reflection has its own bounded request after execution. Generic tools process at most 64 items per call, with loaded-range and operation timeouts; larger work requires multiple model-selected calls. Ctrl+C preserves artifacts and stops only the owned practice server/bot. Your normal world, world memory and existing Ollama service are not reset or shut down.

This removes the hand-written task allowlist, not every execution contract. Building/farming are now attemptable compositions, not universally reliable capabilities. Successful episodes are evidence to accumulate; failures need investigation, not automatic “learned” labels. The current arena does not establish open-world survival/generalization, and an independent intent checker/property-based crop verification remains future work.

Local-model evidence on October 6, 2026: a two-wooden-sword objective was independently confirmed after two earlier unsuccessful attempts. Three model-selected cobblestone episodes passed across varied setups; with progress history and reasoning enabled, the model then selected and completed a stone-pickaxe objective. The pickaxe run included collecting cobblestone, processing logs into planks/sticks, crafting/placing a table and crafting the pickaxe. These are controlled examples, not a general success-rate estimate or proof of autonomous farming/building. Reports and raw model traces remain in the ignored practice directories.

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

[MIT](LICENSE)
