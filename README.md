# Minecraft Agents

An experimental Minecraft agent that connects a local Purpur server to a Mineflayer bot and a Python/LangGraph controller. It is being developed for the Nebius × NVIDIA Global AI Hackathon, with local Ollama used during development to avoid consuming hosted-inference credits.

## Current status

This is an active prototype. The table distinguishes what the current code path supports from what still needs a live-server demonstration.

| Capability | Current status |
| --- | --- |
| Local Purpur server, Mineflayer bot, WebSocket bridge, and in-game chat | Wired end to end. |
| Mine named log types | End-to-end wired through a typed `mine_logs` skill; mock/unit-tested, not yet demonstrated against this repository's live server. It verifies that the bot's inventory increased by the requested amount. |
| Give a named player an exact count of held items | End-to-end wired through `give_item`; mock/unit-tested, not yet live-server tested. It verifies the bot's inventory decreased after a toss near the player, **not** that the player picked the item up. |
| Mine logs and give them to the chat requester | End-to-end wired through `mine_and_give`; mock/unit-tested, not yet live-server tested. |
| Crafting, placing blocks, chests, smelting, or multi-step build tasks | Not a verified capability yet. These can enter the legacy planner, which is explicitly reported as unverified rather than as a completed world task. |
| Persistent environmental knowledge graph / long-term world memory | Not implemented. The current LangGraph graph controls workflow; it is not a knowledge graph of the Minecraft world. |

Normal conversation is ignored. Physical objectives are serialized so two pathfinding jobs do not run at the same time. Only the typed log-resource tasks receive an objective-specific verification result today.

## Architecture

```text
Minecraft chat or terminal
        │
        ▼
Python controller (main.py)
  ├─ typed parser + serialized task routing
  └─ LangGraph planner for unsupported requests (unverified)
        │ WebSocket :8765
        ▼
Node Mineflayer bot
  ├─ deterministic skills: mine_logs / give_item / mine_and_give
  └─ legacy generated-JavaScript sandbox
        │
        ▼
Local Purpur Minecraft 1.20.1 server
```

`agent/intents.py` recognizes bounded log tasks. `bot/skills.js` does the Mineflayer work with reachable pathfinder goals, structured results, and inventory evidence. The legacy planner is retained for experimentation, but a generated snippet finishing is not treated as evidence that the requested world state exists.

## Prerequisites

- Python 3.12 in the project's `minecraft-agent` Conda environment (the dependency pins in `requirements.txt` match this environment)
- Node.js and npm; `npm ci` uses the committed Node lockfile
- Java 21 for the bundled Purpur 1.20.1 server
- A local Ollama installation for low-cost development, or Nebius Token Factory credentials for the production path

The server runs in offline mode so the bot can use a local `AI_Agent` identity. Do not expose that configuration to an untrusted network.

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

## Supported chat objectives

Use clear quantities and log species. Examples:

```text
mine 8 oak logs
get 10 blocks of oak wood
mine 8 oak logs and drop them to me
give 3 spruce logs to Pilot6117
```

For `to me`, the requesting chat player becomes the recipient. A named recipient must be visible to the bot before a handoff can proceed. The natural-language parser intentionally limits this path to one through 64 items and does not partially execute a request that also asks to craft, build, smelt, or place blocks.

Examples such as `craft a chest`, `place a crafting table`, or `get 10 logs and then build a chest` are not verified features. If they are admitted to the legacy planner, the controller reports an unverified attempt rather than a successful task.

## Tests and checks

The committed Python intent-parser tests can run from a clean checkout:

```bash
python -m unittest discover -s tests -v
```

You can also validate JavaScript syntax without starting Minecraft:

```bash
node --check bot/index.js
node --check bot/skills.js
node --check bot/sandbox.js
node --check bot/state.js
```

This workspace may contain additional ignored scripts under `scratch/` for sandbox and mock-skill checks. They are useful while developing locally but are not part of the tracked, reproducible test suite or a substitute for a live-server integration test.

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
