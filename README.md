# Minecraft Agents

A Minecraft assistant built with Mineflayer, a Python controller, and persistent world memory for the Nebius × NVIDIA Global AI Hackathon.

Development uses local Ollama/Jarvis. The intended demo uses Nemotron through Nebius Token Factory for conversation and planning, with a lightweight local controller executing the plan. Mineflayer supplies structured game observations and controls; video and computer vision are not required.

## Current implementation

The existing model-led tool loop can attempt gathering, crafting, delivery, and terrain recovery. It checks observed outcomes and supports direct `status`, `stop`, `inventory`, and `memory` commands. Controlled tests are not evidence that it can reliably handle every survival-world task.

World checkpoints and Graphiti memory survive rejoins. Model-led practice records outcomes and written lessons. An optional small navigation experiment now performs local demonstration/PPO weight training, but its policy is not enabled in the normal agent and has not established reliable Minecraft performance. The roadmap separates these experiments from the proposed planner and execution split.

## Local launch

Use the `minecraft-agent` Conda environment, Node/npm, and a Java 21 JDK. The launcher automatically selects `/Users/jaskirtkaler/miniforge3/envs/minecraft-agent/bin/python`; override `MINECRAFT_PYTHON` on another installation.

From the project root, with dependencies and `.env` already configured:

```bash
./start.sh --check
./start.sh
```

Wait for `READY`, then join Minecraft Java at **127.0.0.1:25565**. The server/bot use Minecraft 1.20.1, with ViaVersion for newer client compatibility. Training mode is Peaceful with no hunger, while retaining Survival inventories and crafting. Ctrl+C saves the world and stops only services the launcher started.

For a fresh checkout, follow [manual setup](docs/operations.md#local-setup-and-launch-order) first. After reviewing and accepting Minecraft's EULA, the initial Purpur server startup populates the library cache required by the offline plugin builders; stop it cleanly before using the root launcher. See [runtime setup](docs/repository.md#fresh-checkout).

## Checks and practice

Run the offline checks without starting Minecraft or a model:

```bash
./check.sh
```

Keep gameplay regression and model-led practice distinct:

```bash
./practice.sh --list
./practice.sh --cases batch,staircase
./learn.sh --episodes 3
./learn.sh --suite grounding --episodes 6 --seed 320011
./learn.sh --suite recovery --episodes 3 --seed 360121
```

`practice.sh` uses real disposable servers but no model inference. `learn.sh` uses the configured local model, shares the experience library, and refuses hosted inference unless explicitly allowed. The grounding suite supplies six crafting, planting, and delivery requests with varied quantities and seeded layouts; Jarvis chooses every action, and server checks grade the requested outcomes independently of its declared goals. Neither command trains neural-network weights. Detailed controls and verification limits are in the [practice guide](docs/operations.md#autonomous-model-led-practice).

For optional local policy-weight training, start with `./rl.sh --check`. Use `./rl.sh --evaluate-only --checkpoint ...` to test frozen weights on new layouts without training. The [RL guide](docs/rl.md) gives the full commands and distinguishes simulator practice, demonstration warm-starts, and real disposable-Minecraft evaluation. These experiments make no LLM/API calls and never automatically replace the normal controller.

## Project layout

- `agent/` contains planning, bridge clients, dialogue, observations, and world memory.
- `bot/` contains Mineflayer perception, game knowledge, tools, navigation, and operation lifecycles.
- `practice/` contains disposable-world fixtures and model-led practice.
- `rl/` contains the optional small navigation policy, training, and evaluation backends.
- `server-plugins/` contains the inventory debugger and server-authoritative practice checks.
- `tests/` contains offline contracts and tool regressions.
- `docs/` contains the roadmap, observation contract, operations, and repository cleanup guide.
- `scripts/` contains maintenance helpers. Root launch/practice commands remain stable.
- `server/` contains local server configuration and runtime files; worlds stay at their existing paths.
- `data/` contains private logs, practice worlds, experiences, and memory. Never delete it as routine source cleanup.

## Development plan

Start with [the staged roadmap](docs/roadmap.md). The first milestone is repository organization and a versioned, read-only Mineflayer observation interface. Later milestones separate language planning from execution, make task progress resumable, improve independent grading and reusable procedures, and evaluate a narrowly scoped RL component.

Keep local models as the default. Hosted evaluation needs an explicit budget and configuration; Minecraft mechanics do not require a Tavily search on every action. The final hackathon path must use an NVIDIA open-source model and Nebius at runtime, as specified by the [official rules](https://nebiusglobalaihackathon.devpost.com/rules).

## Further reading

- [Operations and troubleshooting](docs/operations.md)
- [Architecture and implementation roadmap](docs/roadmap.md)
- [Structured observation contract](docs/observations.md)
- [Local policy training and evaluation](docs/rl.md)
- [Repository cleanup and data preservation](docs/repository.md)

## License

[MIT](LICENSE)
