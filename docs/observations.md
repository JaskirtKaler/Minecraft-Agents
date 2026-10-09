# Structured Mineflayer observations

The read-only `get_observation` bridge request supplies game state to a future lightweight executor or learned policy without video capture. The existing planner continues to use its compact `get_state` snapshots. Adding this request does not train a policy or change the agent's gameplay decisions.

## Request and response

Python callers use `await bridge.get_observation(horizontal_radius=3, vertical_radius=2)`. Both radii are bounded: horizontal 0–5, vertical 0–3. Invalid requests fail explicitly rather than waiting for a missing response. This request never walks, opens containers, changes blocks, calls a model, or resets a world.

The response uses `schemaVersion=1`, `source=mineflayer`, and the actual `gameVersion`. It contains the existing timestamped `state` with world, dimension, and session identity; `motion` with exact position, facing in radians, velocity, and grounded status; `inventoryDetails` with item IDs and available durability fields; bounded relative `landmarks`; and `terrain`. The compact state's position remains rounded for chat; relative geometry uses the exact motion position.

Before spawning, `state.ready=false`, motion/terrain are null, and inventory/landmarks are empty. Consumers must not act on that snapshot. Python validates the response before updating the existing checkpoint state.

## Live interaction targets

Both compact planner state and the observation's nested state now include bounded `farmingTargets`. Each entry separates `soil`, `planting_position`, `occupant` with block properties, `planting_cell` (empty, occupied, or unknown), `occupied_by_bot`, and inventory-item/resulting-block pairs. Positions come from currently loaded farmland and its actual cell above, not historical coordinates or a fixed ground height. This is an additive compact-state field; the terrain schema and existing RL encoder remain unchanged.

The read-only `inspect` tool complements these targets: position queries report actual below/above blocks, and identifier queries report whether a name denotes an item, a block, or both. Crop rules are explicit mechanics checked against the version registry, not rules learned from play. These facts help the language model ground its goal and action parameters; they do not choose a farming sequence or prove reachability.

## Local terrain

The default local grid is 7 by 5 by 7 cells, centered at the bot's floored block position. Negative positions use floor, not truncation. `shape` is ordered x, y, z. Channel arrays use x outermost, y next, and z innermost. A zero-based index is `(x * shape[1] + y) * shape[2] + z`; offsets start at the negative radius along each axis.

Channels have exactly one entry per cell:

- `loaded` identifies cells supplied by the bot's current loaded world data.
- `blockIds` and `stateIds` contain version-specific categorical registry IDs, or null when unavailable. State IDs preserve distinctions such as crop age.
- `collision` is -1 for unknown geometry, 0 for no collision, 1 for one full cube, and 2 for partial or multiple shapes. A generic block bounding box alone is not assumed to be a full cube.
- `fluid` is -1 for unknown, 0 for none, 1 for water or waterlogged blocks, and 2 for lava.

Unloaded cells always have null IDs and unknown collision/fluid. Never encode them as air. `fullyLoaded` describes this local grid only; it is not an absence proof for other terrain. These coarse channels are not a complete collision solver or permission to dig.

Mineflayer can know loaded blocks hidden behind walls or underground. `lineOfSightFiltered=false` makes that observation privilege explicit. The landmark list comes from a bounded key-block scan and is capped at 24; `landmarkListComplete=false` means omitted resources may still exist. Landmarks are not proof of reachability or chest contents.

## Policy input and future training

Raw JSON is a perception contract, not a neural-network input tensor. A later encoder must use categorical embeddings or a documented vocabulary for IDs, normalized numeric features, padded inventory/landmark masks, and a separate goal condition. Do not treat numeric block IDs as meaningful ordinal distances. Policy checkpoints must record the game version, observation schema, encoder vocabulary, action contract, and training configuration.

A later training wrapper must align pre-action observations with selected actions, authoritative outcomes, reward, elapsed time, termination, and truncation. It must reject stale world/session identity and unsettled operations before resetting. Game truth used for reward grading belongs to the training harness; it must not silently become global-world perception for the actor.

This first contract is unit-tested across Node and Python. A live snapshot acceptance check remains necessary before using it for gameplay or training.
