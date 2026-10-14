# Repository organization

Root commands remain stable. Source organization preserves existing worlds, memory, experiences, private configuration, logs, and installed dependencies.

## Source map

| Location | Responsibility |
| --- | --- |
| `main.py` | Controller lifecycle, terminal commands, and addressed game-chat dispatch |
| `agent/graph.py`, `agent/tool_agent.py` | Planner selection and model-led tool execution |
| `agent/bridge.py`, `agent/chat_routing.py` | Controller/bot protocol and human command routing |
| `agent/goal_contract.py`, `agent/construction_*.py` | Frozen completion criteria, geometry, and construction checks |
| `agent/memory_store.py`, `agent/world_memory.py`, `agent/graphiti_memory.py` | Durable checkpoints, world history, and optional graph indexing |
| `agent/experience.py` | Saved procedural lessons and outcome retrieval |
| `bot/index.js` | Mineflayer connection and request dispatch |
| `bot/state.js`, `bot/observations.js`, `bot/grounding.js`, `bot/knowledge.js` | Fresh observations and version-matched mechanics |
| `bot/tools.js`, `bot/skills.js`, `bot/resources.js`, `bot/navigation.js` | Bounded physical actions and verification |
| `bot/construction.js`, `bot/construction_controller.js` | Construction scope and current-task placement ownership |
| `bot/operations.js` | Cancellation, progress, and operation lifetime |
| `shared/` | Contracts consumed by both Python and Node |
| `practice/` | Disposable-server fixtures, model-led practice, and the construction simulator |
| `rl/` | Optional navigation policy training and evaluation |
| `server-plugins/` | Java inventory/training plugin and practice oracle source |
| `server/` | Bundled server configuration, bootstrap jars, and local runtime |
| `scripts/` | Shared launch checks, interpreter discovery, Docker health, and maintenance |
| `tests/` | Offline contracts and regression entry points |
| `docs/` | Setup, architecture, practice, recorded evidence, and roadmap |

Keep gameplay code with its domain. Avoid moving a public module just to shorten a directory listing; cross-language contracts belong in `shared/`. Legacy baseline and opted-in generated-JavaScript paths remain supported and isolated from the default model-led planner.

## Launchers and runtime discovery

- `start.sh`: foreground host supervisor, also used internally by agent-only launches.
- `start-agent.sh`: additional bot/controller; requires no server files or Java.
- `practice.sh`, `learn.sh`, `rl.sh`: separate regression, learning, and policy experiments.
- `check.sh`: offline verification.
- `server/start.sh`: server-only bootstrap using the bundled jars.

`scripts/runtime.sh` selects Python consistently: explicit `MINECRAFT_PYTHON`, active Conda environment other than `base` (`CONDA_PREFIX`), the existing `$HOME/miniforge3/envs/minecraft-agent` installation, then Python on PATH. Java selection honors `MINECRAFT_JAVA`, `JAVA_HOME`, macOS Java 21 discovery, then Java on PATH. Invalid explicit overrides fail instead of silently selecting another interpreter.

`scripts/launch_checks.py` parses configuration as data, validates setup, emits only selected shell-quoted non-secret settings, checks TCP readiness, and inspects installed Ollama tags. It never starts a model, downloads weights, or requests inference. The Docker image includes these same helpers.

## Source versus runtime

Track source, manifests/lockfiles, tests, plugin source, documentation, and intentional server settings. Ignore installed dependencies, generated libraries, caches, worlds, logs, plugin build output, and Docker data backups.

The bundled Purpur and compatibility-plugin jars remain tracked so fresh-checkout setup works. A download manifest with integrity checks can replace them in a separate migration. Administration files such as operator, whitelist, ban, and permission lists remain intentional server settings.

Private runtime paths include `.env`, `docker.env`, `docker.*.env`, `data/`, `scratch/`, `.venv/`, and `bot/node_modules/`. The host uses `data/memory` and `data/learning`; source-launched additional agents default to `data/agents/<name>/memory` and `learning`. Docker mounts its agent data at `/app/data` in a named volume. Do not rename a world or its memory identity during source cleanup.

## Stop tracking generated server data

Ignore rules do not remove already tracked files. This maintenance command removes only their Git index entries and preserves every local file:

```bash
bash scripts/untrack-runtime.sh --dry-run
bash scripts/untrack-runtime.sh --apply
git diff --cached --stat
```

The scoped targets are server caches/libraries/logs/versions, the three existing world directories, `server/usercache.json`, `server/version_history.json`, plugin telemetry under `server/plugins/bStats`, and temporary profiler files under `server/plugins/spark/tmp`. The script checks that tracked files exist and refuses staged changes in its target paths. It does not commit, alter unrelated staged changes, or rewrite history.

The seven large runtime directories were previously untracked while retaining their local files. Four generated cache/telemetry files remain tracked in this checkout. This Codex session cannot write `.git/index`, so applying the prepared index cleanup requires the user's terminal. Ignore rules are already in place.

## Fresh checkout

Install dependencies and configure the private `.env`. Review Minecraft's EULA and set `eula=true` yourself in `server/eula.txt`. Start `./server/start.sh` once to populate Purpur's runtime libraries, then stop it using the console `stop` command. The root launcher and offline Java builders can then compile against those cached libraries.

The server-only script checks the bundled jars and EULA; it does not replace the server, fall back to vanilla, or rewrite acceptance. No existing world is moved or reset.

## Retained development data

Practice and RL runs include reports, traces, checkpoints, disposable worlds, and cached runtimes. Failed runs are debugging evidence. Disk-space reclamation is a separate operation: identify exact runs to archive and preserve their reports before removing anything. Previously committed worlds/logs also remain in Git history after untracking.
