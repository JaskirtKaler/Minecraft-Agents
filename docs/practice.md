# Practice and evaluation

Use the commands below from the repository root. [Offline checks](../tests/README.md) run without Minecraft or model calls; real-server practice and model-led learning have separate entry points. See [recorded validation results](validation.md) for dated successes, failures, and their limits. Optional policy-weight training is documented in the [RL guide](rl.md).

## Autonomous model-led practice

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

## Local simulated construction practice

The construction backend runs Python-only spatial practice without starting a Minecraft server, Mineflayer, Graphiti or a Minecraft client. The launcher uses the shared Python selection described in the [repository guide](repository.md#launchers-and-runtime-discovery). Start your existing local Ollama/Jarvis service for actual episodes; the offline check and task listing need no model service:

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

## Headless regression baseline

Run these from the repository root, without launching the normal stack or a Minecraft client:

```bash
./practice.sh --list
./practice.sh
./practice.sh --cases batch,staircase
./practice.sh --cases held_deposit,partial_stack,multiple_stacks --repeat 3
```

The default regression suite covers 11 cases: exact held/partial/multiple-stack deposits, collection of logs/dirt/cobblestone, the three-resource batch, missing pickaxe, full chest, inaccessible stone, and an unsupported batch. `staircase` is an optional twelfth case. Expected safe refusals pass only when the expected error is returned and authoritative state shows no unrequested excavation or inventory change. Collection/delivery is graded on server chest counts, remaining inventory, excavation counts, health, and underfoot-mining violations—not just the bot's success message.

Each run creates `data/practice/<UTC timestamp>-<unique ID>/` with its own disposable Purpur world, SQLite memory, `report.json`, `server.log`, and `bot.log`. It copies cached server runtime artifacts, **not** the normal worlds or plugins; uses separate ephemeral loopback ports; disables Graphiti indexing and model inference; and starts only its owned server/bot. No Ollama or Nebius call is needed. The console-only `PracticeOracle` fixture plugin is built locally and installed only in this guarded practice server. Ctrl+C stops owned processes and preserves artifacts. Each run consumes disk space (cached runtime plus a small world); runs are retained for inspection rather than automatically deleted.

`--timeout` sets a per-case deadline (default 90 seconds, allowed 5–180). A timed-out case cancels work and aborts the suite to avoid overlapping a reset with unsettled actions. Use `MINECRAFT_PYTHON` / `MINECRAFT_JAVA` for other installations; a Java 21 JDK and the existing cached Purpur runtime are required.

You can also validate JavaScript syntax without starting Minecraft:

```bash
node --check bot/index.js
node --check bot/skills.js
node --check bot/sandbox.js
node --check bot/state.js
```

Ignored `scratch/` scripts are local experiments. Reproducible offline tests live under `tests/`; see [test commands](../tests/README.md).

An opt-in Graphiti/Ollama check ingests a synthetic verified task into a temporary graph, closes/reopens it, recalls the fact, and checks replay/world isolation. It never connects to Minecraft or adds synthetic data to your real memory directory:

```bash
RUN_MEMORY_LIVE_TEST=1 python -m unittest discover -s tests -p test_memory_live.py -v
```
