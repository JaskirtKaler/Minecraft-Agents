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
| Stacked resource requests | Fully validated typed `execute_plan` steps; shared chest capacity and required pickaxe are checked before collection. No silent execution of only one part of an unsupported batch. |
| Controlled staircase escape | Typed `escape_staircase`; uphill chest/player approach can invoke it automatically when walking fails. A real-server fixture demonstrates a 3-block uphill escape without underfoot mining. Other terrain still needs testing. |
| Read-only agent inventory | Chat `inventory` reads fresh bot state even during tasks. `/jarvisinventory` or right-clicking the bot opens a live server-side inventory window. Plugin compiled against the installed Purpur API with offline event-safety tests; live-client acceptance is pending. |
| Block/tool/drop/recipe knowledge | Read-only lookup from the installed version-matched `minecraft-data` registry. Drop entries are conditional possibilities, not unconditional promises. |
| Farming, crafting, placement, smelting, or multi-step builds | Not verified capabilities yet. Unsupported chat requests explain that limitation rather than execute generated code. Resource batches are supported, arbitrary builds are not. |
| Repeatable practice curriculum | `./practice.sh` runs real server/bot fixtures with server-authoritative grading, separate worlds/memory, and no inference calls. It is an evaluation baseline, not fine-tuning or autonomous skill invention. |
| Persistent environmental knowledge graph / long-term world memory | Local SQLite checkpoints + Graphiti temporal graph with embedded FalkorDB Lite. Rejoins recover prior progress; task outcomes and discoveries are ingested in the background. Live gameplay acceptance testing is still needed. |

Chat distinguishes tasks, knowledge questions, corrections, status, and cancellation. Missing quantities prompt a follow-up (1–64), scoped to the requesting player. Physical operations are serialized and expired operations cannot issue new guarded actions. Already-issued server actions may still have partial effects, so interrupted outcomes remain unverified.

## Architecture

```text
Minecraft chat or terminal
        │
        ▼
Python controller (main.py)
  ├─ typed parser + validated resource plans + serialized task routing
  ├─ exact world checkpoints / durable episode outbox (SQLite)
  ├─ Graphiti → embedded FalkorDB Lite → local Ollama extraction + embeddings
  └─ LangGraph planner for explicitly opted-in CLI experiments (unverified)
        │ WebSocket :8765
        ▼
Node Mineflayer bot
  ├─ version-matched game knowledge (drops, tools, block properties, recipes)
  ├─ safe typed resource / handoff / chest-transfer skills + batch execution
  ├─ no-progress walking watchdog + bounded dropped-item recovery
  └─ revocable operation lifecycle; generated JS disabled by default
        │
        ▼
Local Purpur Minecraft 1.20.1 server
```

`agent/intents.py` recognizes bounded resource, resource-batch, and escape tasks. `bot/knowledge.js` looks up version-matched mechanics; `bot/resources.js` selects exposed targets and verifies chest transfers. `bot/navigation.js` separates ordinary walking from controlled uphill excavation. The legacy planner is retained for explicitly opted-in terminal experiments (`ALLOW_EXPERIMENTAL_CODE=true` in `.env`). It is not a security sandbox, generated snippets remain unverified, and game chat never falls back to arbitrary generated JavaScript.

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

Jarvis/Ollama is a development backend, not the permanent agent architecture. Basic gameplay currently uses deterministic typed skills and does not require either model. The next planning integration should have Token Factory produce validated skill plans, use fresh observations plus relevant Graphiti history, and check each outcome before replanning. Changing the endpoint alone does **not** add that planner to the safe chat path: the existing model planner still generates experimental JavaScript.

The [Voyager approach](https://voyager.minedojo.org/) motivates a curriculum, reusable skills, and environment feedback. This project now has a reproducible basic curriculum and verified hand-written skills; automatic curriculum selection, learned skill generation, and weight fine-tuning are not implemented. A more capable model does not replace tested mechanics, navigation, or truthful success checks.

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

For `to me`, the requesting chat player becomes the recipient. A named recipient must be visible to the bot before a handoff can proceed. The natural-language parser limits each resource quantity to 1–64. Resource batches can collect supported resources or deliver them to one shared chest; repeated resource quantities are combined, still capped at 64. Plans have at most 12 collection/delivery steps. Mixed crafting/building/farming instructions and unknown batch resources are rejected before any part starts. Multi-resource player delivery is not yet parsed.

If you ask for "some cobblestone," the bot asks how many; reply with a number. A correction such as "when you mine stone it becomes cobblestone" is checked against game data and does not start another mining job.

`get 10 cobblestone and put it in the chest` uses matching items already carried and collects only the shortfall. `mine 10 cobblestone and put it in the chest` explicitly requests 10 **additional** items before delivery. The headless real-server regression begins with 12 cobblestone, deposits exactly 10, leaves 2, and breaks no blocks. Batch failures retain completed steps and the failing step; partial progress is not reported as full completion.

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

The knowledge lookup provides block properties, tool requirements, conditional drop candidates and basic recipe ingredients. It does not yet interpret every possible loot condition or custom server datapack. Farming knowledge includes crop-age conditions, but autonomous harvesting/replanting is not implemented. Resource/tool/path choices adapt to live observations and outcomes persist in memory; this is not model-weight training or automatic invention of new skills.

Examples such as `craft a chest`, `place a crafting table`, or `get 10 logs and then build a chest` are not verified features. Chat reports them as unsupported; only explicitly enabled CLI experiments can use the legacy unverified planner.

## Tests and checks

The committed Python intent-parser tests can run from a clean checkout:

```bash
python -m unittest discover -s tests -v
npm test --prefix bot
```

### Headless real-server practice

Run these from the repository root, without launching the normal stack or a Minecraft client:

```bash
./practice.sh --list
./practice.sh
./practice.sh --cases batch,staircase
./practice.sh --cases held_deposit,partial_stack,multiple_stacks --repeat 3
```

The default curriculum covers 11 cases: exact held/partial/multiple-stack deposits, collection of logs/dirt/cobblestone, the three-resource batch, missing pickaxe, full chest, inaccessible stone, and an unsupported batch. `staircase` is an optional twelfth case. Expected safe refusals pass only when the expected error is returned and authoritative state shows no unrequested excavation or inventory change. Collection/delivery is graded on server chest counts, remaining inventory, excavation counts, health, and underfoot-mining violations—not just the bot's success message.

Each run creates `data/practice/<UTC timestamp>-<unique ID>/` with its own disposable Purpur world, SQLite memory, `report.json`, `server.log`, and `bot.log`. It copies cached server runtime artifacts, **not** the normal worlds or plugins; uses separate ephemeral loopback ports; disables Graphiti indexing and model inference; and starts only its owned server/bot. No Ollama or Nebius call is needed. The console-only `PracticeOracle` fixture plugin is built locally and installed only in this guarded practice server. Ctrl+C stops owned processes and preserves artifacts. Each run consumes disk space (cached runtime plus a small world); runs are retained for inspection rather than automatically deleted.

On October 6, 2026, a complete 12-case run passed on the installed Purpur 1.20.1 server. These are small deterministic, peaceful fixtures with tools supplied and exposed ground-level resource blocks. Passing them does not establish natural-tree traversal, survival under enemies, farming, arbitrary terrain navigation, or autonomous learning. New skills and regression fixtures should be added together before enabling broader model-generated plans.

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

The strongest next demo milestone is a live-server recording of the typed `mine_and_give` path with its observable inventory evidence, followed by verified crafting/placement primitives and a persistent world-memory graph.

## License

[MIT](LICENSE)
