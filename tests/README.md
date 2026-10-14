# Test commands

Run from the repository root with Python and bot dependencies installed:

```bash
./check.sh
```

This command runs offline contracts without opening Minecraft, making model/API calls, or downloading dependencies. It disables the opt-in live-memory test and uses shared [runtime discovery](../docs/repository.md#launchers-and-runtime-discovery). Java plugin checks need a Java 21 JDK and the server libraries populated by the [first server startup](../docs/repository.md#fresh-checkout).

## Run a smaller suite

```bash
python -m unittest discover -s tests -p test_launcher.py -v
python -m unittest discover -s tests -p test_memory_store.py -v
npm test --prefix bot
node tests/test_construction.js
```

Use the active project Python or the `MINECRAFT_PYTHON` interpreter explicitly for direct Python commands. `npm test` runs all Node contract entry points through `run-node.cjs`; `test_skills.js` includes state, resources, tools, navigation, observations, and RL navigation, so they are not run twice.

## Coverage map

| Area | Tests |
| --- | --- |
| Controller, model clients, chat routing, recovery | `test_chat_*`, `test_dialogue.py`, `test_planner_client.py`, `test_tool_agent.py`, `test_task_recovery.py` |
| Goals, quantities, construction scope and verification | `test_*contract.py`, `test_quantity_contract.py`, `test_construction*`, `test_transfer_constraints.py` |
| Memory, world isolation, persistence | `test_*memory*.py`, `test_memory_store.py` |
| Observation/region contracts and mechanics | `test_observations*`, `test_region*`, `test_ground*`, `test_resources.js`, `test_tools.js` |
| Process ownership, runtime discovery, safe index cleanup | `test_launcher.py`, `test_launch_checks.py`, `test_runtime_scripts.py`, `test_repository_cleanup.py`, `test_docker_healthcheck.py` |
| Practice, simulator, optional policy | `test_practice.py`, `test_learning_dispatch.py`, `test_construction_practice.py`, `test_rl*.py`, `test_rl_navigation.js` |

Process tests use disposable repositories and executable stand-ins. Java contracts live alongside each plugin under `server-plugins/`. Offline mocks check contracts and failure handling; they do not establish model-led success or real Minecraft physics.

## Live evaluation

See [practice and evaluation](../docs/practice.md) for disposable-server regressions, actual model-led practice, and the opt-in Graphiti/Ollama test. The [RL guide](../docs/rl.md) covers frozen-policy evaluation. Those commands have explicit prerequisites and retain private results under `data/`.

Docker image builds and Windows networking require Docker Desktop; `check.sh` validates the controller/launcher contracts but does not launch a container. Keep one-off experiments in ignored `scratch/`; promote useful repeatable checks into the tracked suite.
