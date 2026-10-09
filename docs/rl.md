# Local navigation policy training and evaluation

The optional RL experiment trains a small local navigation policy from structured Mineflayer observations. Jarvis and future Nemotron remain responsible for interpreting requests and planning tasks. The policy is not connected to normal chat yet and is never automatically deployed. Mining, crafting, delivery, farming, climbing, and vision are outside this first experiment.

Unlike `learn.sh`, which stores written lessons, `rl.sh` updates neural-network weights. Its 3,526-parameter actor–critic runs on CPU with NumPy in the `minecraft-agent` Conda environment. No LLM or paid API is called. Optional demonstration training changes the small controller's weights, not Jarvis or Nemotron.

## Observations and walking choices

Each decision uses a Mineflayer snapshot with a 5 by 5 by 5 terrain grid. A derived 5 by 5 ground map distinguishes unknown, blocked, and walkable cells. The encoder supplies 103 features: categorical ground cells, the relative destination, recent local visit counts, and health. Registry IDs, absolute world coordinates, server grading state, and the full arena map are not policy features. Like the existing observation interface, this is privileged client world data, not human line-of-sight vision.

The actor chooses east, west, south, north, or wait. These are bounded walking options, not raw keyboard inputs. Mineflayer's pathfinder handles movement to an adjacent cell; the policy chooses which cell. Known dry full-block support and clear foot/head space are required. Unknown cells, liquids, hazards, partial supports, and airborne movement are masked. No option mines, crafts, places, jumps up steps, or transfers items. These mechanic checks do not restrict the normal LLM's task vocabulary.

Minecraft action requests include the observed world/session and foot position. A stale request is rejected. Physical work remains serialized and cancellable. The Node controls require both `ENABLE_RL_NAVIGATION=true` and an `MC_WORLD_ID` beginning with `practice:rl:`; the normal launcher does not enable them.

## Training and grading

PPO uses freshly collected actions, probabilities, values, and rewards from the current policy version. Consumed or mismatched rollouts are rejected. The implementation includes clipped policy updates, a value critic, entropy, generalized advantage estimates, Adam, gradient-norm clipping, and a KL early stop. The optimization follows [PPO's clipped objective](https://arxiv.org/pdf/1707.06347); implementation gradients are checked numerically in the offline tests.

Rewards use observed position and health. In Minecraft, these come from the console-only Bukkit oracle, not the model's completion claim. A grounded arrival in the requested goal cell receives a completion reward; damage or excavation ends the episode unsuccessfully. Duration-aware potential shaping rewards progress while penalizing elapsed time. Discount and advantage calculations account for variable walking durations in wall-clock units. Step caps, stagnation, and failed settled options truncate episodes; truncation bootstraps the final observation without carrying advantages across a reset. Unsettled actions or session changes abort the run without another reset.

With `--demonstrations`, a local heuristic produces teacher episodes first. Only independently successful episodes become behavior-cloning labels. Demonstrations and PPO training use different seeds from evaluation. Behavior cloning is supervised learning, not off-policy PPO; subsequent PPO updates use new rollouts from the warmed policy.

The report compares the initial policy, optional demonstration warm-start, final policy, and a local heuristic on the same evaluation seeds. Minecraft additionally checks the existing full-route pathfinder. That helper uses a wider loaded map, so its result is labelled as a control rather than an equal-observation comparison. A learned controller must show useful improvement before replacing it.

## Run locally

No additional model service is needed. NumPy is already installed in this project's Conda environment. Another environment needs the controller dependencies plus `requirements-rl.txt`.

Check the policy contract without starting services or training:

```bash
./rl.sh --check
```

Run cheap simulated practice with demonstrations and actual weight updates:

```bash
./rl.sh --backend simulator --demonstrations 96 --updates 20 --episodes 8 --max-steps 32 --eval-episodes 12 --seed 777 --time-budget 60
```

The simulator generates small connected obstacle layouts. It approximates flat walking only; its scores are not Minecraft performance. It is labelled `grid-simulator`, and its synthetic frames cannot authorize Minecraft bridge actions.

### Evaluate a frozen checkpoint on new layouts

An evaluation-only run tests the saved policy without demonstrations, PPO updates, or new checkpoints. Neither Minecraft nor Jarvis needs to be running for the simulator. This measures learned navigation; it does not teach the policy by itself.

```bash
./rl.sh --evaluate-only --backend simulator \
  --checkpoint data/rl/20261009-041807-3b147896/policy.npz \
  --eval-episodes 100 --evaluation-start-seed 2100000 \
  --max-steps 24 --compare-max-steps 64 --time-budget 120
```

Use your actual checkpoint path. The example chooses a new seed range after the October 8 evaluation below. Each seed specifies an automatically generated layout. The command rejects overlap with recorded training attempts, teacher attempts (including failures), or evaluation seeds in saved local `data/rl/*/report.json` reports. Deleted or external histories cannot be audited. For another fresh evaluation, choose a different `--evaluation-start-seed`; the second step cap intentionally reuses the first cap's layouts within the same run.

The frozen policy uses deterministic masked action selection. It and the local heuristic receive matching start/goal fixtures and the same local observation information. The report verifies checkpoint bytes, policy parameters/version, and optimizer step count remain unchanged. Evaluation-only mode refuses training flags, accepts up to 256 simulator layouts or 16 disposable-Minecraft cases, and never deploys the policy.

Each evaluation saves `report.json`, `summary.md`, and `failures.jsonl`. Failed trajectories include the exact policy features, legal-action masks, action probabilities, and visited cells. Simulator maps and shortest routes are stored for grading/debugging only; they are not policy input. Cycle and revisit labels describe movement, not the LLM's reasoning or a proven cause. Comparing 24 and 64 steps helps distinguish added-time gains from failures that persist with more time.

The agent plays these short episodes automatically. Later, `--backend minecraft` checks the controller in guarded real server arenas with the same no-update mode (start with only 1–2 cases and allow startup time). Manually playing or chatting in the normal world does not update this policy's weights; explicit training is a separate operation.

### Check real Minecraft walking and training

Check real observations, reset handling, walking, grading, and one PPO update:

```bash
./rl.sh --backend minecraft --updates 1 --episodes 1 --max-steps 20 --eval-episodes 1 --time-budget 120
```

Minecraft practice starts its own loopback server and bot in a guarded disposable world. It needs the existing Java 21, Node, pinned server jar, and generated runtime cache. It never copies the normal world or installs the practice oracle in `server/plugins`. Only one disposable Minecraft experiment should run at a time. If the Mac is under pressure, stop the normal game/server yourself before practice; the experiment does not stop your existing services.

Use `--checkpoint data/rl/YOUR_RUN/policy.npz` to initialize another experiment from compatible weights. This is a weight warm-start, not exact training resumption: Adam state starts fresh. The default is a new random policy. A work deadline includes server startup; owned processes receive bounded shutdown afterward. Ctrl+C and SIGTERM stop the experiment and its owned services. Interrupted traces are retained, not silently retried.

Every run keeps its reports under a new UTC-named `data/rl/` directory. Training runs also keep initial/final checkpoints and aligned `transitions.jsonl`; demonstration runs keep labels and a warm-start checkpoint. Evaluation-only runs do not create or replace checkpoints. Minecraft runs retain the disposable server, world, `server.log`, and `bot.log`. These copies consume disk space and are intentionally ignored by Git; no automatic pruning is performed.

## Development results on October 8

These are small development checks, not a final benchmark or evidence of general Minecraft competence.

`./check.sh` passed 137 Python tests with one opt-in live-memory test skipped, the Node control/perception regressions, shell/JavaScript syntax checks, and both local Java plugin builds. PPO and cloning gradients match finite-difference checks. The Minecraft runs below used separate real servers, not the mocked offline fixtures.

| Experiment | Observation |
| --- | --- |
| Simulator, seed 777, 96 teacher episodes and 20 PPO updates | Initial policy 0/12 goals; demonstration checkpoint 6/12; final policy 8/12; local heuristic 12/12. |
| Minecraft, random initial weights | Real walking and PPO updates completed safely, but the policy did not pass its evaluation case. The existing full-route pathfinder reached the control destination in about 2.3 seconds. |
| Minecraft, initialized from the simulator checkpoint | One training goal completed; the separate evaluation case remained unsuccessful. No damage or excavation was needed. |

The demonstration checkpoint's 6/12 result was measured separately after that run. New reports also record a warm-start evaluation before PPO, so gains from demonstrations and reinforcement updates can be distinguished. Simulator progress does not establish transfer to Minecraft; the retained Minecraft evaluation failure is why this policy remains experimental.

### Frozen 100-layout evaluation

The user's seed-17 checkpoint from `data/rl/20261009-041807-3b147896/policy.npz` was evaluated on October 8 without changing its version-9 weights. Seeds 2,000,000–2,000,099 did not overlap the four saved local run reports, and generated 100 distinct start/goal/map layouts. The two step caps used the same layouts, not separate samples.

| Step cap | Frozen policy | Local heuristic |
| --- | --- | --- |
| 24 | 26/100 goals | 95/100 goals |
| 64 | 49/100 goals | 100/100 goals |

The larger budget added 23 successes and lost none, but 51 layouts still failed. Of those, 37 ended with a repeated movement cycle, 12 with a high moving-revisit fraction, and 2 simply reached the cap. These are trajectory labels, not proof of a specific training defect. All 100 layouts had a shortest path within 18 steps according to the grading-only map, so route length alone does not explain the failures.

The run made zero LLM/API calls, collected no demonstrations or training episodes, and left the source checkpoint unchanged. Retained results: `data/rl/20261009-043601-e601c155/summary.md`, `report.json`, and `failures.jsonl`. These are simulator results, not Minecraft performance. Once inspected for development, these layouts should not be reused as a fresh holdout after modifying the policy; the next audit needs new seeds.

## Next implementation boundary

Keep the existing pathfinder for reliable ordinary navigation. Complete the planner/executor goal interface and resumable task progress before making this policy callable from chat. Extend training only around a measured execution problem, such as resource-target selection or recovery, with compatible demonstrations, independent grading, and unseen evaluation layouts. The current policy is not a jack-of-all-trades player and does not solve recipe reasoning or stacked-task planning.
