# Minecraft agent implementation roadmap

The target is an LLM-led Minecraft assistant with a small local execution layer. Jarvis/Ollama remains the development backend. Nemotron through Nebius Token Factory handles conversation, task interpretation, prerequisite reasoning, and strategic replanning in the demo. Mineflayer supplies structured observations and game controls. A small RL policy is optional and must improve a measured execution bottleneck; the project does not require training a universal player from video.

## Current foundation

The current controller already has model-selected tools, fresh-state verification, operation cancellation, exact checkpoints, Graphiti world memory, and disposable-world practice. It still combines planning and execution in one model loop. Experience retrieval returns written lessons rather than reusable executable procedures. The original practice rewards remain diagnostics. A separate optional `rl.sh` experiment now updates a small navigation actor–critic; it is not enabled in the normal agent.

The October 8 implementation pass preserves uncommitted training-mode changes and all local worlds/logs. It introduces repository organization and the read-only observation contract. It does not change the default gameplay planner or claim a trained policy.

## Milestone 1 Repository and observation foundation

- Keep short launch instructions in the root README and retain the full operations guide under `docs/`.
- Ignore generated runtime artifacts and prepare a scoped index-cleanup script that preserves local files. The user must run the apply step because this workspace cannot write the Git index.
- Provide one offline verification command using the `minecraft-agent` Conda environment.
- Add a versioned Mineflayer observation request with local terrain, explicit unknown cells, movement, inventory details, and relative landmarks.
- Verify Node serialization, Python validation, and bridge request correlation without inference or a live server.

Acceptance: existing offline regressions pass, the schema works across the Node/Python boundary, and runtime files stay on disk. This is an observation interface, not an RL environment or a learned controller.

The initial October 8 validation passed 119 Python tests with one opt-in live-memory test skipped, all Node tool/observation checks, shell/JavaScript syntax checks, and Java plugin contracts. `./start.sh --check` passed without launching services. The user subsequently applied runtime untracking: all 190 staged removals were verified to remain on disk. Later disposable RL runs exercised the observation contract with real Minecraft; none enabled the policy in the normal agent.

## Milestone 2 Separate planning from execution

Introduce explicit planner and executor interfaces without replacing working tools in one rewrite. The planner translates user intent into a task graph with dependencies, quantities, destinations, and completion criteria. The executor runs bounded actions, persists completed subgoals, and returns actual progress/failure evidence. Invoke the model when the plan needs changing rather than after each repeated operation.

Keep `status`, `stop`, inventory, and memory independent of inference. Retain session guards and serialize physical work. Never retry an uncertain transfer or craft without checking actual state. Unknown task verbs remain attemptable through generic tools; skill confidence is metadata, not a new objective allowlist.

Acceptance: a multi-resource delivery resumes after an injected interruption without recollecting completed resources, and a craft-and-deliver goal cannot pass on crafting alone.

## Milestone 3 Grounded task dependencies and independent grading

Use version-matched registry data to calculate recipe quantities, intermediate items, required stations, and harvest requirements. The model chooses strategies and material alternatives; arithmetic and API contracts remain deterministic. Verify the original intended outcome with a task contract rather than treating model-declared goals as independent intent interpretation.

Classify failures as planning, observation, navigation, inventory synchronization, execution, or verification. Keep authoritative server state for grading, not hidden global-world information in the policy's observation. Add crop-property and furnace interfaces when those mechanics enter scope.

Acceptance: varied collect/craft/deliver tasks are graded against requested items and destinations, and failures are reproducible from saved traces.

## Milestone 4 Reusable procedures and autonomous practice

Generalize successful traces into parameterized procedures with prerequisites, termination checks, and recovery behavior. Do not blindly replay historical coordinates. Validate procedures in multiple layouts before marking them reusable; allow exploratory composition when no procedure fits.

Extend practice with held-out seeds, terrain variation, missing equipment, full/inaccessible containers, and changed destinations. Track success, verified partial progress, inference calls, action duration, and repeated no-progress behavior. Keep world memory separate from procedural learning.

Acceptance: unfamiliar quantities and layouts can reuse a procedure, and evaluation environments are separate from the examples used to build it.

## Milestone 5 Small learned execution policy

First measure whether target selection or stuck recovery remains a bottleneck after the executor is reliable. Choose one task and define its observation encoder, bounded action choices, independent rewards, termination/truncation, and compute budget. Start with successful compatible demonstrations when available; compare a small learned policy against the existing helper before adopting it. PPO with an actor and value critic is a candidate, not a fixed dependency decision.

Record aligned pre-action observations, chosen actions, elapsed time, outcomes, rewards, and policy version. PPO also needs fresh trajectories from its current policy, not arbitrary old logs. Account for variable action durations if training on Mineflayer skills instead of fixed-rate movement actions.

Acceptance: the policy improves held-out results over the existing baseline within the agreed local compute budget. No vision pipeline, Nemotron weight training, or large GPU training job is implied.

October 8 experiment: a CPU-only 3,526-parameter navigation actor–critic, structured encoder, gated walking options, duration-aware rewards/GAE, compatible checkpoints, and optional successful-demonstration warm-start are implemented. Real disposable Minecraft runs collected actions and performed PPO updates. The warm-started simulator policy reached 8/12 evaluation goals, below the local heuristic's 12/12. One Minecraft training goal completed, but separate Minecraft evaluation failed. This milestone's adoption criterion is therefore **not met**. See [the RL guide](rl.md) for commands, retained evidence, and limitations. Planner/executor separation remains unfinished.

## Milestone 6 Hosted validation and hackathon demo

Keep local inference as the default and make hosted execution explicit. Normalize provider capabilities and add request/token limits, bounded retries, usage accounting, and no silent hosted fallback. Leave Graphiti inference local independently of planner selection. Use Tavily only for missing external knowledge and cache its results.

Run a small shared benchmark on local and selected hosted Nemotron models; investigate planner failures separately from broken controls. The submitted path must make a real Nebius runtime call and use an NVIDIA open-source model, or meet the eligible AI Cloud alternative in the [official rules](https://nebiusglobalaihackathon.devpost.com/rules). The published submission deadline is October 30, 2026 at 10 AM PDT.

Acceptance: a reproducible multi-stage task completes with authoritative evidence, the demo shows actual hosted inference, and setup instructions, license, public source, and demonstration video meet the rules. Do not postpone the first hosted compatibility test until the final recording.

## Coordination

Keep repository cleanup and the first interfaces in this chat. A later separate chat can own a narrow task such as hosted Nemotron benchmarking or the first RL experiment once its interfaces and budget are fixed. Avoid simultaneous edits to the launcher, observation schema, or planner contract. No extra model/service needs to start for the offline foundation work.
