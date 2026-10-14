# Minecraft agent operations

Setup, LAN connections, chat commands, and memory configuration for the server host. For packaged agents, use [Docker](docker-agent.md); for Windows source development, use [WSL and Conda](windows-agent.md). See [architecture and capabilities](architecture.md) for implementation details.

## Prerequisites

- Python 3.12 in the project's `minecraft-agent` Conda environment (the dependency pins in `requirements.txt` match this environment)
- Node.js 22 or newer and npm; `npm ci` uses the committed Node lockfile
- Java 21 **JDK** (including `javac` and `jar`) for the bundled Purpur 1.20.1 server and local inventory plugin
- A local Ollama installation for low-cost development, or Nebius Token Factory credentials for the production path

The server runs in offline mode so the bot can use a local `AI_Agent` identity. Do not expose that configuration to an untrusted network.

## One-command local launch

Once the dependencies and `.env` are set up, launch everything from one terminal:

```bash
./start.sh
```

The launcher honors `MINECRAFT_PYTHON`, an active Conda environment other than `base`, the existing `minecraft-agent` Miniforge environment, then Python on PATH; see [runtime discovery](repository.md#launchers-and-runtime-discovery). It reuses an already-running Ollama/Jarvis service, or starts Ollama if needed. Its model-readiness check only inspects installed model tags: it does **not** download models or request inference. Graphiti can begin background memory extraction with the configured memory model once the agent joins.

It builds the local inventory plugin using the server's cached libraries (no dependency downloads), then starts the Minecraft server, Python controller, and Mineflayer bot, waiting for each to be ready. When it prints **READY**, join Minecraft Java using **127.0.0.1:25565** (or the printed address if `MC_PORT` differs). Address objectives to the bot in game chat, for example `@AI_Agent mine 3 oak logs and drop them to me`, or `@AI_Agent memory` to recall saved world progress.

Keep this terminal open. **Ctrl+C** stops the bot/controller and sends Minecraft a console `stop` command to save the world. It stops only processes it started; a reused Ollama service remains running. Fresh logs are kept under the ignored `data/run/<timestamp>-<agent>-<pid>/` directory as `server.log`, `controller.log`, and `bot.log`.

The launcher binds Minecraft to IPv4 interfaces (`MC_BIND_HOST=0.0.0.0`) so home-network clients can join, and keeps the controller on localhost. It preserves the existing memory world ID and refuses to launch duplicate services on occupied ports. `./start.sh --check` validates configuration, dependencies, and port availability and prints detected private IPv4 join addresses without starting services. For a different installation path, set `MINECRAFT_PYTHON` or `MINECRAFT_JAVA` to the appropriate executable.

The bundled server and bot remain on **1.20.1**; installed ViaVersion provides newer Java-client compatibility. A “connection refused” message means the selected address has no reachable server, not that a server-version upgrade is required.

### Home-network players and agents

Start the server once on the host computer with `./start.sh`. Keep `MC_HOST=localhost` there: this is the address its own bot uses. The separate `MC_BIND_HOST=0.0.0.0` setting lets other devices reach Minecraft. Manual launches from `server/start.sh` use `server-ip=0.0.0.0` in `server/server.properties`.

On another computer on the same home Wi-Fi or Ethernet network, open **Minecraft Java → Multiplayer → Add Server** and enter the **Home network players/agents** address printed at startup, including `:25565` (or your chosen `MC_PORT`). Use Direct Connect/Add Server; this dedicated server does not automatically appear as an “Open to LAN” world. Everyone needs a different Minecraft username. The configured limit is 20 players, including bots.

The per-IP connection delay is disabled (`connection-throttle: -1` in `server/bukkit.yml`) so agents on the same computer can join in quick succession.

No router port forwarding or UPnP is needed; Purpur's automatic UPnP forwarding remains disabled. The server retains offline-mode logins for the Mineflayer agents, so use this setup on your trusted home network. If a second device cannot connect, check that it is on the main network rather than an isolated guest network and that the host firewall permits Java/Minecraft TCP connections on the game port. The printed IP can change after reconnecting; a router DHCP reservation can keep it stable.

### Adding an agent without another server

For the Docker route on a Windows desktop, use [the Docker agent guide](docker-agent.md). It packages the same controller/bot, connects to an external model API, and preserves the agent's memory and learning in a named volume. The following steps are the direct source/Conda alternative.

Use the updated project source on the joining computer. The agent launcher supports macOS/Linux; on Windows use [WSL 2 with Ubuntu and Linux Conda](windows-agent.md). It needs Python 3.12+, Node/npm, and the project dependencies. Java, the `server/` folder, server plugins, and Docker are not required. From the copied project folder, create and activate the supplied Conda environment, then install the bot packages:

```bash
conda env create -f environment.yml
conda activate minecraft-agent
npm ci --prefix bot
cp .env.example .env
```

Only copy the example to `.env` on a new installation; preserve an existing configured `.env`. `environment.yml` installs the Python packages from the existing pinned `requirements.txt`, plus Python and Node/npm from conda-forge. Configure the model settings for that computer and start its model service first (for local Ollama, run `ollama serve` and use an installed model). The agent launcher checks local model availability but never starts/downloads a model service. An activated Conda environment is used automatically; `MINECRAFT_PYTHON` can override that selection.

In that computer's `.env`, use the host's printed LAN IP, a unique username, and the host's existing world identity, for example:

```dotenv
MC_HOST=192.168.50.63
MC_PORT=25565
MC_USERNAME=AI_Agent_2
MC_VERSION=1.20.1
MC_WORLD_ID=localhost:25565
MC_COMMAND_PLAYERS=Pilot6117
WS_HOST=127.0.0.1
WS_PORT=8765
WS_URL=ws://127.0.0.1:8765
```

The current host's world identity is `localhost:25565`, preserving its earlier default. Despite looking like an address, `MC_WORLD_ID` is a stable memory label: keep the host's value on every agent even when connecting through a LAN IP. Fresh hosts created from `.env.example` may instead use `local-survival-v1`; always match that host's actual value. Replace the example LAN IP with the current host address.

Start the existing Minecraft server on its host, then run this on the joining computer:

```bash
./start-agent.sh --host 192.168.50.63 --name AI_Agent_2 --check
./start-agent.sh --host 192.168.50.63 --name AI_Agent_2
```

The flags override `.env`. `--port` selects a nondefault game port. `--check` verifies configuration, server reachability, and the local bridge port without starting services. The launcher then starts the Python controller and Mineflayer bot, waits until both are ready, and keeps logs under `data/run/`. Ctrl+C stops only that pair; the shared Minecraft server and model service keep running. Use the root `./start.sh` only on the computer hosting Minecraft.

To run an additional agent on the hosting computer itself:

```bash
./start-agent.sh --host 127.0.0.1 --name AI_Agent_2 --bridge-port 8766
```

Choose another free bridge port for each further agent on the same computer (8767, 8768, etc.). Separate computers can all use 8765. A controller accepts only one bot; an accidental second bridge connection is rejected. Agent-only launches store memory and learning under `data/agents/<lowercase-username>/memory` and `data/agents/<lowercase-username>/learning`, independent of the original host agent's `data/memory`. To override these defaults, pass explicit `MEMORY_DIR` and `LEARNING_DIR` environment variables when launching. Existing `.env` memory paths are used by the host launcher but deliberately replaced with per-agent defaults by the agent-only launcher.

### Commands for individual agents

Use the exact Minecraft bot username at the beginning of each message (matching is case-insensitive):

```text
@AI_Agent collect 3 oak logs
@AI_Agent_2 collect 3 dirt
@AI_Agent_2 status
@AI_Agent_2 stop
@AI_Agent inventory
@AI_Agent_2 memory
```

`@AI_Agent_2: status` also works. Include the prefix on follow-up answers, such as `@AI_Agent_2 16` when asked for a quantity. Ordinary chat, mentions in the middle of a sentence, and commands for another agent are ignored before any planner call. Terminal objectives remain unprefixed. `/time`, `/weather`, and `/jarvisinventory` remain normal Minecraft server commands.

Only the human players named in each agent's `MC_COMMAND_PLAYERS` can issue chat commands. Initially this is `Pilot6117`; add other human usernames separated by commas and restart the controllers. Never add bots or wildcard entries. Minecraft offline mode does not identify which accounts are human, so the explicit human list prevents even an addressed message from another bot from triggering work. This is command routing for the trusted LAN, not account authentication. Different agents can receive different human lists. Independently addressed agents do not yet coordinate building ownership or shared objectives with one another.

To return to this-computer-only play, set `MC_BIND_HOST=127.0.0.1` in `.env` and restart the root launcher. For manual server launches, also restore `server-ip=127.0.0.1` in `server/server.properties`.

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

   For a new environment, run `conda env create -f environment.yml` first; it installs Python, Node, and the Python dependencies. The pip command above synchronizes an existing environment.

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

Type `memory` in the controller terminal or `@AI_Agent memory` in Minecraft chat to recall progress. `@AI_Agent memory status` also works in game chat. Address `status` and `stop` to the intended bot to inspect or cancel its work. A busy graph returns exact checkpoint context immediately; background extraction can take some time with a large local model.

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

`status`, `stop`, `inventory` and `memory` work directly, including during inference; prefix each game-chat command with `@AgentName`. The model is instructed to treat knowledge questions/corrections as dialogue, not physical tasks. Restart the Python controller/bot after code changes; an already-running process still uses its loaded code.

In baseline mode, `get` uses carried items and collects shortfalls; `mine` collects additional items. Quantities are 1–64, plans at most 12 steps, and unsupported mixed batches are rejected. The model planner chooses composition/collection mode instead. Both retain partial progress without reporting it as full completion.

## Inspecting Jarvis's inventory

- Type `@AI_Agent inventory` (using the intended bot's name) in game chat for a timestamped live list with counts and bot slot IDs, plus equipment. This is a read-only bridge query, not a model or memory guess, and works while a physical task is active. Longer responses are paced to avoid chat-spam kicks.
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

This runs the Python, Node, shell/JavaScript syntax and cached Java plugin contracts using shared interpreter discovery. See [test commands and coverage](../tests/README.md). Offline checks do not establish live gameplay competence.

[Practice and evaluation](practice.md) documents model-led learning, the construction simulator, and disposable-server regressions. [Recorded results](validation.md) retain the development history and failures.

## Demo requirements

See the [hackathon checklist](demo.md) and [implementation roadmap](roadmap.md).

## License

[MIT](../LICENSE)
