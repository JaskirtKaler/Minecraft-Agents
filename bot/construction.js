/**
 * Deterministic, evidence-based block-batch placement.
 *
 * This module deliberately does not call runOperation itself.  Its `bot`
 * argument must already be the revocable bot facade supplied by the caller's
 * runOperation, so a cancelled batch cannot issue a later equip/place call.
 */
const { Vec3 } = require('vec3');
const { goals } = require('mineflayer-pathfinder');
const { dataFor, plantingRule } = require('./knowledge');
const { pathCost, walkTo } = require('./navigation');

const AIR = new Set(['air', 'cave_air', 'void_air']);
const MAX_TARGETS = 64;
const MAX_DISTANCE = 64;
const PLACE_REACH = 4.5; // Match the existing single-block placement check.
const constructionTasks = new WeakMap();
const constructionHandles = new WeakMap();

// Falling, fluid, and harmful blocks need specialised mechanics rather than
// this deliberately boring structural-block executor.  This remains generic:
// an item is otherwise accepted only when the installed registry exposes it as
// both an item and a block with the same canonical name.
const RESTRICTED_BLOCK = /^(?:air|cave_air|void_air|water|lava|bubble_column|fire|soul_fire|magma_block|cactus|tnt|powder_snow|scaffolding|(?:soul_)?campfire|sand|red_sand|gravel|suspicious_sand|suspicious_gravel|.*_concrete_powder|(?:chipped_|damaged_)?anvil|pointed_dripstone)$/;

function fail (code, message, data = {}) {
  const error = new Error(message);
  error.code = code;
  error.data = data;
  throw error;
}

function plainPosition (position) {
  return { x: position.x, y: position.y, z: position.z };
}

function key (position) {
  return `${position.x},${position.y},${position.z}`;
}

function isAir (block) {
  return !!block && AIR.has(block.name);
}

function isRestricted (name) {
  return RESTRICTED_BLOCK.test(name || '');
}

function isHazardOrFalling (name) {
  return !AIR.has(name) && isRestricted(name);
}

function isSolidSupport (block) {
  return !!block && block.boundingBox === 'block' && !isRestricted(block.name);
}

function blockFingerprint (block) {
  const properties = block.getProperties?.() || {};
  const ordered = Object.fromEntries(Object.keys(properties).sort().map(name => [name, properties[name]]));
  return JSON.stringify({ name: block.name,
    type: Number.isInteger(block.type) ? block.type : null,
    stateId: Number.isInteger(block.stateId) ? block.stateId : null,
    properties: ordered });
}

/**
 * Controller-only task lifecycle, never a model tool. The returned opaque
 * handle is registered here rather than accepting JSON ownership claims.
 * Call on the raw bot; operations receive only the handle plus their existing
 * revocable bot facade. A new task invalidates every old placement receipt.
 */
function beginConstructionTask (bot, taskId, expectedCells, { worldSessionId = null } = {}) {
  if (!bot || typeof bot !== 'object' || typeof taskId !== 'string' || !taskId ||
      !Array.isArray(expectedCells) || !expectedCells.length || expectedCells.length > 4096) {
    fail('INVALID_CONSTRUCTION_CONTRACT', 'A controller task id and 1–4096 accepted spatial cells are required.');
  }
  const desired = new Map();
  for (const [index, cell] of expectedCells.entries()) {
    const position = parsePosition(cell?.position, index);
    if (typeof cell?.block !== 'string' || !cell.block || desired.has(key(position))) {
      fail('INVALID_CONSTRUCTION_CONTRACT', 'Accepted cells need unique integer positions and canonical block names.');
    }
    desired.set(key(position), cell.block);
  }
  const previous = constructionTasks.get(bot);
  if (previous) invalidateConstructionTask(previous);
  const handle = Object.freeze({ task_id: taskId });
  const task = { bot, handle, taskId, worldSessionId, desired, placements: new Map(), revisions: new Map(), active: true,
    entity: bot.entity, dimension: bot.game?.dimension, version: bot.version };
  // A same-name replacement must not inherit ownership. Even an identical
  // state update is conservatively treated as an intervening world change.
  task.onUpdate = (before, after) => {
    const position = after?.position || before?.position;
    if (position && desired.has(key(position))) {
      task.revisions.set(key(position), (task.revisions.get(key(position)) || 0) + 1);
    }
  };
  task.onEnd = () => invalidateConstructionTask(task);
  bot.on?.('blockUpdate', task.onUpdate);
  bot.on?.('end', task.onEnd);
  constructionTasks.set(bot, task);
  constructionHandles.set(handle, task);
  return handle;
}

function invalidateConstructionTask (task) {
  task.active = false;
  task.placements.clear();
  task.bot.removeListener?.('blockUpdate', task.onUpdate);
  task.bot.removeListener?.('end', task.onEnd);
  if (constructionTasks.get(task.bot) === task) constructionTasks.delete(task.bot);
}

function endConstructionTask (bot, handle) {
  const task = constructionHandles.get(handle);
  if (!task || task.bot !== bot || !task.active) return false;
  invalidateConstructionTask(task);
  return true;
}

function currentConstructionTask (bot, handle) {
  const task = handle && constructionHandles.get(handle);
  if (!task || !task.active || constructionTasks.get(task.bot) !== task ||
      task.entity !== bot.entity || task.bot.inventory !== bot.inventory ||
      task.dimension !== bot.game?.dimension || task.version !== bot.version) {
    fail('CONSTRUCTION_TASK_UNAVAILABLE', 'Repair needs the original active controller contract in this world session.');
  }
  return task;
}

function hasConstructionTarget (handle, position) {
  const task = handle && constructionHandles.get(handle);
  if (!task?.active || constructionTasks.get(task.bot) !== task) {
    fail('CONSTRUCTION_TASK_UNAVAILABLE', 'The original controller construction contract is no longer active.');
  }
  return task.desired.has(key(parsePosition(position, 0)));
}

/** Defense in depth for the controller's accepted geometry, not a planner. */
function guardConstructionAction (bot, action, handle) {
  const name = action?.name;
  if (!handle) {
    if (name === 'repair_batch') currentConstructionTask(bot, handle);
    return;
  }
  const task = currentConstructionTask(bot, handle);
  const args = action?.args || {};
  if (name === 'place_batch' || name === 'place') {
    const positions = name === 'place_batch' ? parseBatch(bot, args).positions : [parsePosition(args.position, 0)];
    // A single prerequisite/station outside the geometry keeps the ordinary
    // tool semantics; it is never recorded as owned construction placement.
    if (name === 'place' && !task.desired.has(key(positions[0]))) return;
    const resultingBlock = name === 'place' ? plantingRule(bot, args.item)?.result_block || args.item : args.item;
    for (const target of positions) {
      const desired = task.desired.get(key(target));
      if (desired == null) fail('CONSTRUCTION_SCOPE', 'Construction batches may only place in accepted spatial cells.', {
        position: plainPosition(target) });
      if (desired !== resultingBlock) fail('CONSTRUCTION_GOAL_CONTRADICTION', 'Placement contradicts this accepted final cell.', {
        position: plainPosition(target), item: args.item, resulting_block: resultingBlock, desired_block: desired });
    }
  }
  if (name === 'dig' || name === 'dig_batch') {
    const positions = name === 'dig_batch' ? parseDigBatch(bot, args).positions : [parsePosition(args.position, 0)];
    for (const target of positions) {
      const desired = task.desired.get(key(target));
      if (desired == null) {
        if (name === 'dig_batch') fail('CONSTRUCTION_SCOPE', 'Construction clearing batches must stay in accepted spatial cells.', {
          position: plainPosition(target) });
        continue;
      }
      const block = loadedBlock(bot, target);
      if (!isAir(block) && block.name === desired) fail('CONSTRUCTION_ALREADY_CORRECT', 'Do not demolish a correct accepted final cell.', {
        position: plainPosition(target), desired_block: desired });
      if (!isAir(block) && !isClearableNaturalBlock(block.name)) fail('REPAIR_REQUIRED',
        'Ordinary digging cannot remove a non-natural build block; inspect current-task repair eligibility.', {
          position: plainPosition(target), block: block.name, desired_block: desired });
    }
  }
}

function recordOwnedPlacement (bot, handle, target, beforeBlock, afterBlock, inventoryBefore, inventoryAfter, item) {
  if (!handle) return;
  const task = currentConstructionTask(bot, handle);
  if (!task.desired.has(key(target)) || !isAir(beforeBlock) || afterBlock.name !== item || inventoryAfter !== inventoryBefore - 1) return;
  task.placements.set(key(target), { fingerprint: blockFingerprint(afterBlock),
    revision: task.revisions.get(key(target)) || 0, item, position: plainPosition(target) });
}

function countItem (bot, item) {
  return bot.inventory.items().filter(stack => stack && stack.name === item)
    .reduce((total, stack) => total + stack.count, 0);
}

function footPosition (bot) {
  const position = bot.entity?.position;
  if (!position || !['x', 'y', 'z'].every(axis => Number.isFinite(position[axis]))) {
    fail('POSITION_UNAVAILABLE', 'Cannot verify the bot position for placement.');
  }
  return new Vec3(Math.floor(position.x), Math.floor(position.y), Math.floor(position.z));
}

function parsePosition (value, index) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      !['x', 'y', 'z'].every(axis => Number.isInteger(value[axis]))) {
    fail('INVALID_POSITION', `positions[${index}] needs integer x, y, z.`);
  }
  return new Vec3(value.x, value.y, value.z);
}

function parseBatch (bot, args = {}) {
  const item = args.item;
  if (typeof item !== 'string' || !item) {
    fail('INVALID_ARGUMENT', 'place_batch needs a non-empty item name.');
  }
  if (!Array.isArray(args.positions) || !args.positions.length || args.positions.length > MAX_TARGETS) {
    fail('INVALID_ARGUMENT', `place_batch needs 1–${MAX_TARGETS} target positions.`);
  }
  const data = dataFor(bot);
  const itemDefinition = data.itemsByName[item];
  const blockDefinition = data.blocksByName[item];
  if (!itemDefinition || !blockDefinition) {
    fail('NOT_PLACEABLE_BLOCK', `${item} is not an installed item/block pair for this server version.`);
  }
  if (isRestricted(item)) {
    fail('RESTRICTED_BLOCK', `${item} requires specialised placement and is not allowed in place_batch.`);
  }

  const seen = new Set();
  const positions = args.positions.map((value, index) => {
    const position = parsePosition(value, index);
    if (seen.has(key(position))) fail('DUPLICATE_POSITION', `positions[${index}] duplicates another target.`);
    seen.add(key(position));
    return position;
  });
  const origin = footPosition(bot);
  // Validate the whole geographic scope before a single world lookup.  A
  // caller never receives a misleading partial preflight for a far batch.
  for (const position of positions) {
    if (position.distanceTo(origin) > MAX_DISTANCE) {
      fail('OUT_OF_RANGE', `Target ${key(position)} is outside the ${MAX_DISTANCE}-block placement scope.`, {
        position: plainPosition(position), origin: plainPosition(origin), max_distance: MAX_DISTANCE
      });
    }
  }
  return { item, positions, origin, blockDefinition };
}

function loadedBlock (bot, position, role = 'target') {
  const block = bot.blockAt(position);
  if (!block) {
    fail(role === 'target' ? 'UNLOADED_BLOCK' : 'UNLOADED_SUPPORT',
      `${role === 'target' ? 'Target' : 'Placement support'} ${key(position)} is not loaded.`,
      { position: plainPosition(position), role });
  }
  return block;
}

function targetObservation (block, desiredName) {
  if (block.name === desiredName) return { kind: 'already_desired', observed: block.name };
  if (!isAir(block)) return { kind: 'occupied_unrelated', observed: block.name };
  return { kind: 'place', observed: block.name };
}

const FACES = [
  new Vec3(0, 1, 0),
  new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
  new Vec3(0, 0, 1), new Vec3(0, 0, -1),
  new Vec3(0, -1, 0)
];

function freshSupport (bot, target) {
  let sawUnloaded = false;
  for (const face of FACES) {
    const referencePosition = target.minus(face);
    const reference = bot.blockAt(referencePosition);
    if (!reference) {
      sawUnloaded = true;
      continue;
    }
    if (isSolidSupport(reference)) return { reference, face };
  }
  if (sawUnloaded) {
    fail('UNLOADED_SUPPORT', 'No loaded solid placement support is available; re-observe this target before building.', {
      position: plainPosition(target)
    });
  }
  fail('NO_PLACEMENT_SUPPORT', 'The air target has no safe loaded solid support block.', {
    position: plainPosition(target)
  });
}

function collidesWithTarget (foot, target) {
  return foot.x === target.x && foot.z === target.z && (foot.y === target.y || foot.y + 1 === target.y);
}

function freshStance (bot, target, foot) {
  if (collidesWithTarget(foot, target) || target.distanceTo(foot) > PLACE_REACH) return null;
  const floor = bot.blockAt(foot.offset(0, -1, 0));
  const body = bot.blockAt(foot);
  const head = bot.blockAt(foot.offset(0, 1, 0));
  if (!floor || !body || !head || !isSolidSupport(floor) || !isAir(body) || !isAir(head)) return null;
  return { foot, floor };
}

function stanceCandidates (bot, target) {
  const current = footPosition(bot);
  const candidates = [current];
  // The same x/z column three blocks below a roof is deliberately included:
  // the bot can stand inside a room and place its ceiling without a tower.
  const horizontal = [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1],
    [1, 1], [1, -1], [-1, 1], [-1, -1]];
  for (const yOffset of [-3, -2, -1, 0, 1]) {
    for (const [xOffset, zOffset] of horizontal) {
      const foot = new Vec3(target.x + xOffset, target.y + yOffset, target.z + zOffset);
      if (!candidates.some(existing => key(existing) === key(foot))) candidates.push(foot);
    }
  }
  // Try the nearest plausible floor first.  This bounds path work while still
  // leaving the model in charge of target order and construction geometry.
  // Impossible cells must not consume the route-work cap, especially when
  // approaching an above-ground build from a lower quarry/cave.
  return candidates.filter(foot => freshStance(bot, target, foot))
    .sort((a, b) => a.distanceTo(current) - b.distanceTo(current)).slice(0, 20);
}

async function reachStance (bot, target) {
  const current = freshStance(bot, target, footPosition(bot));
  if (current) return { ...current, navigation: 'already_in_reach' };

  let lastRouteError = null;
  for (const foot of stanceCandidates(bot, target)) {
    const stance = freshStance(bot, target, foot);
    if (!stance) continue;
    const goal = new goals.GoalBlock(foot.x, foot.y, foot.z);
    let cost;
    try {
      cost = await pathCost(bot, goal);
    } catch (error) {
      // Unsafe navigation configuration is a hard stop, not a reason to
      // quietly try a different route with potentially destructive defaults.
      throw error;
    }
    if (cost == null) continue;
    try {
      await walkTo(bot, goal);
    } catch (error) {
      if (['CANCELLED', 'TASK_TIMEOUT'].includes(error.code)) throw error;
      lastRouteError = error;
      continue;
    }
    const arrived = freshStance(bot, target, footPosition(bot));
    if (arrived) return { ...arrived, navigation: 'walked', route_cost: cost };
  }
  fail('NO_SAFE_PLACEMENT_STANCE', 'No loaded, non-hazardous walking stance can safely reach this placement target.', {
    position: plainPosition(target), route_error: lastRouteError?.message
  });
}

function snapshot (result, bot) {
  return {
    ...result,
    inventory_after: countItem(bot, result.item),
    placements: [...result.placements],
    skipped: [...result.skipped],
    failed: result.failed ? { ...result.failed } : null
  };
}

function recordSkip (result, target, reason, observed) {
  result.skipped.push({ position: plainPosition(target), reason, observed });
}

function recordFailure (result, target, error) {
  result.failed = {
    position: plainPosition(target), code: error.code || 'PLACE_FAILED', message: error.message,
    data: error.data || {}
  };
}

function throwWithPartial (error, result, bot) {
  error.data = { ...(error.data || {}), partial: snapshot(result, bot) };
  throw error;
}

/**
 * Place the supplied target cells in caller-provided order.
 *
 * The returned evidence is successful only when every initially-air target
 * was confirmed as the requested block or was independently observed as
 * already desired.  Occupied unrelated cells are deliberately left alone and
 * reported as skipped/unresolved; this function never digs or overwrites.
 */
async function placeBatch (bot, args = {}, constructionHandle = null) {
  if (constructionHandle) currentConstructionTask(bot, constructionHandle);
  const { item, positions } = parseBatch(bot, args);
  const result = {
    item,
    requested_count: positions.length,
    planned_placements: 0,
    inventory_before: countItem(bot, item),
    inventory_after: null,
    placements: [],
    skipped: [],
    failed: null,
    complete: false,
    verified: false
  };

  let planned;
  try {
    // Preflight every target before an equip/place call.  Already-correct and
    // occupied targets are recorded now and never turn into new work later.
    planned = [];
    for (const target of positions) {
      const observation = targetObservation(loadedBlock(bot, target), item);
      if (observation.kind === 'place') planned.push(target);
      else recordSkip(result, target, observation.kind, observation.observed);
    }
    result.planned_placements = planned.length;
    if (result.inventory_before < planned.length) {
      fail('INSUFFICIENT_ITEMS', `Need ${planned.length} ${item} blocks for the initially-air targets but carry ${result.inventory_before}.`, {
        item, needed: planned.length, available: result.inventory_before
      });
    }
  } catch (error) {
    throwWithPartial(error, result, bot);
  }

  for (const target of planned) {
    try {
      if (constructionHandle) currentConstructionTask(bot, constructionHandle);
      // Re-observe after earlier cells: supports can be a wall/roof block the
      // model deliberately ordered earlier, but external changes never cause
      // digging or blind placement.
      let targetBlock = loadedBlock(bot, target);
      let observation = targetObservation(targetBlock, item);
      if (observation.kind !== 'place') {
        recordSkip(result, target, observation.kind + '_after_preflight', observation.observed);
        continue;
      }
      let support = freshSupport(bot, target);
      const stance = await reachStance(bot, target);
      if (constructionHandle) currentConstructionTask(bot, constructionHandle);

      // Walking/equipping can race with the world.  Select a new loaded
      // support immediately before the one placeBlock call.
      targetBlock = loadedBlock(bot, target);
      observation = targetObservation(targetBlock, item);
      if (observation.kind !== 'place') {
        recordSkip(result, target, observation.kind + '_after_approach', observation.observed);
        continue;
      }
      support = freshSupport(bot, target);
      const before = countItem(bot, item);
      if (before < 1) {
        fail('INVENTORY_CHANGED', `No ${item} remains for the next verified placement.`, { item, before });
      }
      const stack = bot.inventory.items().find(candidate => candidate && candidate.name === item && candidate.count > 0);
      if (!stack) fail('INVENTORY_CHANGED', `No usable ${item} stack remains for the next placement.`, { item, before });
      await bot.equip(stack, 'hand');
      if (constructionHandle) currentConstructionTask(bot, constructionHandle);

      targetBlock = loadedBlock(bot, target);
      observation = targetObservation(targetBlock, item);
      if (observation.kind !== 'place') {
        recordSkip(result, target, observation.kind + '_before_place', observation.observed);
        continue;
      }
      support = freshSupport(bot, target);
      const currentStance = freshStance(bot, target, footPosition(bot));
      if (!currentStance) {
        fail('STANCE_CHANGED', 'The bot is no longer at a loaded safe stance within placement reach.', {
          position: plainPosition(target), stance: plainPosition(stance.foot)
        });
      }
      await bot.placeBlock(support.reference, support.face);
      if (constructionHandle) currentConstructionTask(bot, constructionHandle);
      if (bot.waitForTicks) await bot.waitForTicks(2);
      if (constructionHandle) currentConstructionTask(bot, constructionHandle);
      const afterBlock = loadedBlock(bot, target);
      const after = countItem(bot, item);
      if (afterBlock.name !== item || after !== before - 1) {
        fail('PLACE_NOT_VERIFIED', 'The target block and exact one-item inventory decrease were not both confirmed.', {
          position: plainPosition(target), expected_block: item, observed_block: afterBlock.name,
          inventory_before: before, inventory_after: after
        });
      }
      recordOwnedPlacement(bot, constructionHandle, target, targetBlock, afterBlock, before, after, item);
      result.placements.push({
        position: plainPosition(target), before: targetBlock.name, after: afterBlock.name,
        inventory_before: before, inventory_after: after, verified: true,
        support: { position: plainPosition(support.reference.position), name: support.reference.name },
        stance: plainPosition(currentStance.foot), navigation: stance.navigation,
        ...(stance.route_cost != null ? { route_cost: stance.route_cost } : {})
      });
    } catch (error) {
      recordFailure(result, target, error);
      throwWithPartial(error, result, bot);
    }
  }

  const unresolved = result.skipped.some(entry => entry.reason.startsWith('occupied_unrelated'));
  result.inventory_after = countItem(bot, item);
  result.complete = !unresolved && !result.failed;
  result.verified = result.complete;
  return result;
}

const CLEARABLE_BLOCKS = new Set(['grass', 'tall_grass', 'grass_block', 'dirt', 'stone', 'andesite', 'diorite', 'granite']);
const LIQUID = new Set(['water', 'lava']);
const DIG_NEIGHBOUR_OFFSETS = [
  [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]
];

function isClearableNaturalBlock (name) {
  // Keep the selected footprint narrow: naturally generated leaves/logs plus
  // the explicit ground/stone family only.  Stripped logs are commonly player
  // construction material and are intentionally not treated as clearing.
  return CLEARABLE_BLOCKS.has(name) ||
    /^(?!stripped_)[a-z_]+_leaves$/.test(name || '') ||
    /^(?!stripped_)[a-z_]+_log$/.test(name || '');
}

function parseDigBatch (bot, args = {}) {
  if (!Array.isArray(args.positions) || !args.positions.length || args.positions.length > MAX_TARGETS) {
    fail('INVALID_ARGUMENT', `dig_batch needs 1–${MAX_TARGETS} target positions.`);
  }
  if (args.tool != null && (typeof args.tool !== 'string' || !args.tool)) {
    fail('INVALID_TOOL', 'tool must be an inventory item name when supplied.');
  }
  const seen = new Set();
  const positions = args.positions.map((value, index) => {
    const position = parsePosition(value, index);
    if (seen.has(key(position))) fail('DUPLICATE_POSITION', `positions[${index}] duplicates another target.`);
    seen.add(key(position));
    return position;
  });
  const origin = footPosition(bot);
  // As with placement, reject the full geographic scope before reading a
  // target.  This prevents a far final coordinate from producing a partial
  // clearing run.
  for (const position of positions) {
    if (position.distanceTo(origin) > MAX_DISTANCE) {
      fail('OUT_OF_RANGE', `Target ${key(position)} is outside the ${MAX_DISTANCE}-block clearing scope.`, {
        position: plainPosition(position), origin: plainPosition(origin), max_distance: MAX_DISTANCE
      });
    }
  }
  return { positions, tool: args.tool || null };
}

function inventoryTotals (bot) {
  const totals = {};
  for (const stack of bot.inventory.items()) {
    if (stack?.name && Number.isFinite(stack.count)) totals[stack.name] = (totals[stack.name] || 0) + stack.count;
  }
  return totals;
}

function inventoryDelta (before, after) {
  const delta = {};
  for (const name of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const change = (after[name] || 0) - (before[name] || 0);
    if (change) delta[name] = change;
  }
  return delta;
}

function isStoneFamily (name) {
  return ['stone', 'andesite', 'diorite', 'granite'].includes(name);
}

function isLog (name) {
  return /^(?!stripped_)[a-z_]+_log$/.test(name || '');
}

function itemCanHarvest (block, stack, data) {
  if (!stack) return false;
  const definition = data.blocksByName[block.name];
  if (!definition) return false;
  if (definition.harvestTools && Object.keys(definition.harvestTools).length && !definition.harvestTools[stack.type]) return false;
  return !block.canHarvest || block.canHarvest(stack.type);
}

function fastestTool (block, stacks) {
  return [...stacks].sort((left, right) => {
    const leftTime = typeof block.digTime === 'function' ? block.digTime(left.type) : 0;
    const rightTime = typeof block.digTime === 'function' ? block.digTime(right.type) : 0;
    return leftTime - rightTime;
  })[0] || null;
}

function selectDigTool (bot, block, requestedTool = null) {
  const data = dataFor(bot);
  const stacks = bot.inventory.items().filter(stack => stack && stack.count > 0);
  const requested = requestedTool && stacks.find(stack => stack.name === requestedTool);
  if (requestedTool && !requested) fail('MISSING_TOOL', `Not carrying requested tool ${requestedTool}.`, { tool: requestedTool });
  if (requestedTool && !/(?:_(?:pickaxe|axe|shovel)$|^shears$)/.test(requested.name)) {
    fail('INVALID_TOOL', `${requestedTool} is not a supported hand tool for dig_batch.`, { tool: requestedTool });
  }
  if (isStoneFamily(block.name)) {
    const candidates = requested ? [requested] : stacks.filter(stack => stack.name.endsWith('_pickaxe'));
    const chosen = fastestTool(block, candidates.filter(stack => itemCanHarvest(block, stack, data)));
    if (!chosen) {
      fail(requestedTool ? 'UNSUITABLE_TOOL' : 'TOOL_REQUIRED',
        `A carried usable pickaxe is required to safely clear ${block.name}.`, { block: block.name, tool: requestedTool });
    }
    return chosen;
  }
  if (requested) {
    const expectedFamily = isLog(block.name) ? '_axe' :
      ['dirt', 'grass_block'].includes(block.name) ? '_shovel' :
        block.name.endsWith('_leaves') ? 'shears' : null;
    if (expectedFamily && (expectedFamily === 'shears'
      ? requested.name !== 'shears'
      : !requested.name.endsWith(expectedFamily))) {
      fail('UNSUITABLE_TOOL', `${requestedTool} is not the appropriate tool for ${block.name}; omit tool to auto-select.`, {
        block: block.name, tool: requestedTool
      });
    }
    if (expectedFamily && !itemCanHarvest(block, requested, data)) {
      fail('UNSUITABLE_TOOL', `${requestedTool} cannot safely harvest ${block.name}.`, { block: block.name, tool: requestedTool });
    }
    return requested;
  }
  if (isLog(block.name)) {
    return fastestTool(block, stacks.filter(stack => stack.name.endsWith('_axe') && itemCanHarvest(block, stack, data)));
  }
  if (['dirt', 'grass_block'].includes(block.name)) {
    return fastestTool(block, stacks.filter(stack => stack.name.endsWith('_shovel') && itemCanHarvest(block, stack, data)));
  }
  if (block.name.endsWith('_leaves')) {
    return fastestTool(block, stacks.filter(stack => stack.name === 'shears' && itemCanHarvest(block, stack, data)));
  }
  return null; // Grass/tall grass are intentionally cleared by hand if needed.
}

function targetUnderFoot (foot, target) {
  return foot.x === target.x && foot.z === target.z && target.y < foot.y;
}

function freshDigStance (bot, target, foot) {
  if (targetUnderFoot(foot, target) || target.distanceTo(foot) > PLACE_REACH) return null;
  const floor = bot.blockAt(foot.offset(0, -1, 0));
  const body = bot.blockAt(foot);
  const head = bot.blockAt(foot.offset(0, 1, 0));
  // Leaves/logs can decay or be part of the selected clearing itself.  They
  // are acceptable placement references, but not durable footing for a dig.
  if (!floor || !body || !head || !isSolidSupport(floor) || isLog(floor.name) ||
      floor.name.endsWith('_leaves') || !isAir(body) || !isAir(head)) return null;
  return { foot, floor };
}

function digStanceCandidates (bot, target) {
  const current = footPosition(bot);
  const candidates = [current];
  const horizontal = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1], [0, 0]];
  // A floor block is normally one below feet, while an overhead log can be
  // reached from the same column.  Exact horizontal stances avoid routing on
  // top of a selected floor target just to dig through it.
  // Ground-level feet can be three or four blocks below an overhead branch.
  // A nearby bot must not be required to invent a separate walk_to action
  // just because this target is higher than the two-block candidate window.
  for (const yOffset of [-4, -3, -2, -1, 0, 1]) {
    for (const [xOffset, zOffset] of horizontal) {
      const foot = new Vec3(target.x + xOffset, target.y + yOffset, target.z + zOffset);
      if (!candidates.some(existing => key(existing) === key(foot))) candidates.push(foot);
    }
  }
  // Filter observed impossible footing before the route-work cap. Otherwise
  // closer air/leaf-filled cells can consume all slots and hide real ground.
  return candidates.filter(foot => freshDigStance(bot, target, foot))
    .sort((left, right) => left.distanceTo(current) - right.distanceTo(current)).slice(0, 20);
}

async function reachDigStance (bot, target) {
  const current = freshDigStance(bot, target, footPosition(bot));
  const currentTarget = bot.blockAt(target);
  // Let the caller re-read an asynchronously vanished/unloaded target and
  // report it accurately as idempotent/unknown instead of spending routes
  // looking for a stance to dig an already-air cell.
  if (current && (!currentTarget || isAir(currentTarget))) return { ...current, navigation: 'already_in_reach' };
  if (current && currentTarget && !isAir(currentTarget) && bot.canDigBlock(currentTarget)) {
    return { ...current, navigation: 'already_in_reach' };
  }

  let lastRouteError = null;
  for (const foot of digStanceCandidates(bot, target)) {
    const stance = freshDigStance(bot, target, foot);
    if (!stance) continue;
    const goal = new goals.GoalBlock(foot.x, foot.y, foot.z);
    const cost = await pathCost(bot, goal);
    if (cost == null) continue;
    try {
      await walkTo(bot, goal);
    } catch (error) {
      if (['CANCELLED', 'TASK_TIMEOUT'].includes(error.code)) throw error;
      lastRouteError = error;
      continue;
    }
    const arrived = freshDigStance(bot, target, footPosition(bot));
    const arrivedTarget = bot.blockAt(target);
    if (arrived && (!arrivedTarget || isAir(arrivedTarget))) return { ...arrived, navigation: 'walked', route_cost: cost };
    if (arrived && arrivedTarget && !isAir(arrivedTarget) && bot.canDigBlock(arrivedTarget)) {
      return { ...arrived, navigation: 'walked', route_cost: cost };
    }
  }
  fail('NO_SAFE_DIG_STANCE', 'No loaded, non-hazardous walking stance can safely reach this clearing target.', {
    position: plainPosition(target), route_error: lastRouteError?.message
  });
}

function assertSafeDigNeighbors (bot, target) {
  for (const [x, y, z] of DIG_NEIGHBOUR_OFFSETS) {
    const position = target.offset(x, y, z);
    const block = bot.blockAt(position);
    if (!block) {
      fail('UNLOADED_NEIGHBOR', 'A neighbouring block is unloaded; refusing to clear into an unknown opening.', {
        position: plainPosition(position), target: plainPosition(target)
      });
    }
    const properties = block.getProperties?.() || {};
    if (LIQUID.has(block.name) || properties.waterlogged === true || properties.waterlogged === 'true') {
      fail('LIQUID_ADJACENT', 'A liquid borders this clearing target; refusing to open it.', {
        position: plainPosition(position), target: plainPosition(target), block: block.name
      });
    }
    if (isHazardOrFalling(block.name)) {
      fail('HAZARDOUS_ADJACENCY', 'A hazardous or falling block borders this clearing target; refusing to dig.', {
        position: plainPosition(position), target: plainPosition(target), block: block.name
      });
    }
  }
}

function assertPreflightOverburden (bot, planned) {
  for (const { target } of planned) assertClearOverburden(bot, target);
}

function assertClearOverburden (bot, target) {
  const abovePosition = target.offset(0, 1, 0);
  const above = bot.blockAt(abovePosition);
  if (!above) {
    fail('UNLOADED_NEIGHBOR', 'The block directly above a clearing target is unloaded.', {
      position: plainPosition(abovePosition), target: plainPosition(target)
    });
  }
  // Natural stone/soil/wood/leaves do not fall in Minecraft. Allow ordinary
  // tree trimming and face clearing beneath them; an all-air-above rule would
  // require reaching the canopy top before trimming even a low branch.
  // Neighbour preflight separately refuses fluids and falling/hazard blocks.
  // Do not remove support beneath fixtures or other non-natural construction.
  if (!isAir(above) && !isClearableNaturalBlock(above.name)) {
    fail('UNSUPPORTED_OVERBURDEN', 'The target supports a fixture or non-natural block; refusing to undermine it.', {
      position: plainPosition(abovePosition), target: plainPosition(target), block: above.name
    });
  }
}

function expectedDigBlock (bot, target, expected, stage) {
  const block = loadedBlock(bot, target);
  if (isAir(block)) return null;
  if (block.name !== expected) {
    fail('TARGET_CHANGED', 'The selected clearing target changed after preflight; refusing to dig a different block.', {
      position: plainPosition(target), expected, observed: block.name, stage
    });
  }
  return block;
}

function digSnapshot (result, bot) {
  return {
    ...result,
    inventory_after: inventoryTotals(bot),
    cleared: [...result.cleared],
    skipped: [...result.skipped],
    failed: result.failed ? { ...result.failed } : null
  };
}

function throwDigPartial (error, result, bot) {
  error.data = { ...(error.data || {}), partial: digSnapshot(result, bot) };
  throw error;
}

/**
 * Clear exactly the model-selected natural blocks in coordinate order.
 *
 * This is intentionally not an excavation planner: it will never choose a
 * target, dig a staircase, dig a fixture/cobblestone block, or create a tower.
 * Call it only with the guarded bot facade from an existing runOperation.
 */
async function digBatch (bot, args = {}) {
  const { positions, tool } = parseDigBatch(bot, args);
  const result = {
    requested_count: positions.length,
    planned_clearances: 0,
    tool: tool || 'auto',
    inventory_before: inventoryTotals(bot),
    inventory_after: null,
    cleared: [],
    skipped: [],
    failed: null,
    complete: false,
    verified: false
  };

  let planned;
  try {
    planned = [];
    // Every requested target is observed before the first action.  Air is a
    // successful idempotent result; any player fixture/non-natural block is a
    // hard stop rather than permission to modify it.
    for (const target of positions) {
      const block = loadedBlock(bot, target);
      if (isAir(block)) {
        result.skipped.push({ position: plainPosition(target), reason: 'already_air', observed: block.name });
        continue;
      }
      if (!isClearableNaturalBlock(block.name)) {
        fail('UNSAFE_CLEAR_TARGET', `Refusing to clear non-natural block ${block.name}.`, {
          position: plainPosition(target), block: block.name
        });
      }
      // Catch any already-observed liquid/falling edge before an earlier cell
      // can create avoidable partial site clearing.  It is repeated later
      // because the world may still change while the bot walks.
      assertSafeDigNeighbors(bot, target);
      // Validate every selected tool choice now, before any earlier target is
      // broken.  A later stone cannot create an unexpected partial clearing.
      selectDigTool(bot, block, tool);
      planned.push({ target, expected: block.name });
    }
    assertPreflightOverburden(bot, planned);
    result.planned_clearances = planned.length;
  } catch (error) {
    throwDigPartial(error, result, bot);
  }

  for (const { target, expected } of planned) {
    try {
      let block = expectedDigBlock(bot, target, expected, 'before_approach');
      if (!block) {
        result.skipped.push({ position: plainPosition(target), reason: 'already_air_after_preflight', observed: 'air' });
        continue;
      }
      assertSafeDigNeighbors(bot, target);
      const stance = await reachDigStance(bot, target);

      // A route can place the bot over the floor it is about to clear.  Check
      // the actual post-route stance, not the pathfinder's claimed success.
      if (targetUnderFoot(footPosition(bot), target)) {
        fail('UNSAFE_DIG', 'Refusing to clear underneath the bot.', { position: plainPosition(target) });
      }
      block = expectedDigBlock(bot, target, expected, 'after_approach');
      if (!block) {
        result.skipped.push({ position: plainPosition(target), reason: 'already_air_after_approach', observed: 'air' });
        continue;
      }
      assertSafeDigNeighbors(bot, target);
      assertClearOverburden(bot, target);
      if (!bot.canDigBlock(block)) {
        fail('CANNOT_DIG', 'The selected target is no longer within safe digging reach.', { position: plainPosition(target), block: block.name });
      }
      const selectedTool = selectDigTool(bot, block, tool);
      if (selectedTool) await bot.equip(selectedTool, 'hand');
      const inventoryBefore = inventoryTotals(bot);

      // The target and surrounding hazards are read once more directly before
      // the sole dig call.  No retry can accidentally clear a changed block.
      block = expectedDigBlock(bot, target, expected, 'before_dig');
      if (!block) {
        result.skipped.push({ position: plainPosition(target), reason: 'already_air_before_dig', observed: 'air' });
        continue;
      }
      if (targetUnderFoot(footPosition(bot), target) || !freshDigStance(bot, target, footPosition(bot))) {
        fail('STANCE_CHANGED', 'The bot is no longer at a safe stance for this dig target.', { position: plainPosition(target) });
      }
      assertSafeDigNeighbors(bot, target);
      assertClearOverburden(bot, target);
      if (!bot.canDigBlock(block)) fail('CANNOT_DIG', 'The target is no longer within safe digging reach.', { position: plainPosition(target) });
      await bot.dig(block);
      if (bot.waitForTicks) await bot.waitForTicks(2);
      const afterBlock = loadedBlock(bot, target);
      const inventoryAfter = inventoryTotals(bot);
      if (!isAir(afterBlock)) {
        fail('DIG_NOT_VERIFIED', 'The selected target did not become air after digging.', {
          position: plainPosition(target), before: block.name, after: afterBlock.name,
          inventory_before: inventoryBefore, inventory_after: inventoryAfter
        });
      }
      result.cleared.push({
        position: plainPosition(target), before: block.name, after: afterBlock.name, verified: true,
        tool: selectedTool?.name || 'hand', stance: plainPosition(stance.foot), navigation: stance.navigation,
        ...(stance.route_cost != null ? { route_cost: stance.route_cost } : {}),
        inventory_before: inventoryBefore, inventory_after: inventoryAfter,
        inventory_delta: inventoryDelta(inventoryBefore, inventoryAfter)
      });
    } catch (error) {
      result.failed = {
        position: plainPosition(target), code: error.code || 'DIG_FAILED', message: error.message, data: error.data || {}
      };
      throwDigPartial(error, result, bot);
    }
  }
  result.inventory_after = inventoryTotals(bot);
  result.complete = !result.failed;
  result.verified = result.complete;
  return result;
}

// Fixtures and stateful mechanisms need their own mechanic, even when this
// task placed them. Ordinary stable structural blocks can be repaired without
// treating all existing cobblestone in the world as safe to demolish.
const REPAIR_FIXTURE = /(?:^|_)(?:chest|barrel|furnace|smoker|dispenser|dropper|hopper|shulker_box|crafting_table|cartography_table|smithing_table|fletching_table|enchanting_table|loom|stonecutter|grindstone|brewing_stand|beacon|conduit|jukebox|note_block|bed|door|trapdoor|fence_gate|button|pressure_plate|lever|piston|observer|daylight_detector|redstone_block|repeater|comparator|lectern|bell|respawn_anchor|command_block|structure_block|jigsaw|spawner|vault|decorated_pot|crafter)(?:$|_)/;

function isRepairableStructure (bot, block) {
  const definition = dataFor(bot).blocksByName[block.name];
  const properties = block.getProperties?.() || {};
  return !!definition && definition.boundingBox === 'block' && !definition.transparent &&
    definition.diggable !== false && !isRestricted(block.name) && !REPAIR_FIXTURE.test(block.name) &&
    properties.waterlogged !== true && properties.waterlogged !== 'true';
}

function repairTarget (bot, task, target, stage) {
  currentConstructionTask(bot, task.handle);
  const desired = task.desired.get(key(target));
  if (desired == null) fail('CONSTRUCTION_SCOPE', 'Repair can only act in the original accepted spatial cells.', {
    position: plainPosition(target), stage });
  const block = bot.blockAt(target);
  if (!block) {
    task.placements.delete(key(target));
    fail('UNLOADED_BLOCK', 'The owned repair target is unloaded; its previous placement receipt is no longer current evidence.', {
      position: plainPosition(target), stage });
  }
  if (block.name === desired) fail('REPAIR_NOT_NEEDED', 'This cell already matches the accepted final goal; leave it untouched.', {
    position: plainPosition(target), desired_block: desired, stage });
  const receipt = task.placements.get(key(target));
  if (!receipt) fail('REPAIR_NOT_OWNED', 'This cell has no verified placement receipt in the current task and world session.', {
    position: plainPosition(target), observed_block: block.name, stage });
  if (receipt.fingerprint !== blockFingerprint(block) || receipt.revision !== (task.revisions.get(key(target)) || 0)) {
    task.placements.delete(key(target));
    fail('REPAIR_TARGET_CHANGED', 'The exact placed block or its world revision changed; ownership has been revoked.', {
      position: plainPosition(target), observed_block: block.name, stage });
  }
  if (!isRepairableStructure(bot, block)) fail('UNSAFE_REPAIR_TARGET', 'Repair only removes owned ordinary structural blocks, not fixtures, liquids or special mechanisms.', {
    position: plainPosition(target), observed_block: block.name, stage });
  return { block, desired, receipt };
}

function assertSafeRepairOverburden (bot, target) {
  const above = loadedBlock(bot, target.offset(0, 1, 0), 'support');
  // Stable ordinary structural roofs/walls do not fall. They may remain while
  // a wrongly filled interior cell is repaired; never undermine a fixture or
  // unknown/falling/fluid/special block. Neighbor checks repeat fluid checks.
  if (!isAir(above) && !isRepairableStructure(bot, above)) fail('UNSUPPORTED_OVERBURDEN',
    'The repair target supports a fixture or special block; refusing to undermine it.', {
      position: plainPosition(above.position), target: plainPosition(target), block: above.name });
}

function assertNotPlayerFooting (bot, target) {
  for (const entity of Object.values(bot.entities || {})) {
    if (entity === bot.entity || !entity?.position || (entity.type !== 'player' && entity.name !== 'player')) continue;
    const feet = new Vec3(Math.floor(entity.position.x), Math.floor(entity.position.y), Math.floor(entity.position.z));
    if (targetUnderFoot(feet, target)) fail('UNSAFE_DIG', 'Refusing to remove a block underneath another player.', {
      position: plainPosition(target), player_position: plainPosition(feet) });
  }
}

function selectRepairTool (bot, block, requestedTool) {
  const definition = dataFor(bot).blocksByName[block.name];
  const stacks = bot.inventory.items().filter(stack => stack?.count > 0);
  if (requestedTool && !stacks.some(stack => stack.name === requestedTool)) fail('MISSING_TOOL',
    `Not carrying requested repair tool ${requestedTool}.`, { tool: requestedTool });
  const requested = requestedTool && stacks.find(stack => stack.name === requestedTool);
  if (requested && !/(?:_(?:pickaxe|axe|shovel|hoe)$|^shears$)/.test(requested.name)) fail('INVALID_TOOL',
    `${requestedTool} is not a supported repair hand tool.`, { tool: requestedTool });
  const requiresTool = definition.harvestTools && Object.keys(definition.harvestTools).length > 0;
  const family = typeof definition.material === 'string' && definition.material.startsWith('mineable/')
    ? '_' + definition.material.slice('mineable/'.length) : null;
  const candidates = requested ? [requested] : stacks.filter(stack => family && stack.name.endsWith(family));
  const chosen = fastestTool(block, candidates.filter(stack => itemCanHarvest(block, stack, dataFor(bot))));
  if (requested && !chosen) fail('UNSUITABLE_TOOL', `${requestedTool} cannot harvest this repair block.`, {
    tool: requestedTool, block: block.name });
  if (requiresTool && !chosen) fail('TOOL_REQUIRED', `A carried usable harvest tool is required to repair ${block.name}.`, {
    block: block.name });
  return chosen;
}

/** Read-only, bounded ownership facts; not authority for a later mutation. */
function constructionStatus (bot, handle, maxCells = 64) {
  const task = currentConstructionTask(bot, handle);
  if (!Number.isInteger(maxCells) || maxCells < 1 || maxCells > 64) fail('INVALID_ARGUMENT', 'Ownership status limit must be 1–64.');
  const entries = [...task.placements.entries()];
  const observed = entries.map(([positionKey, receipt]) => {
    const target = new Vec3(receipt.position.x, receipt.position.y, receipt.position.z);
    const block = bot.blockAt(target);
    let reason = null;
    if (!block) {
      reason = 'UNLOADED_BLOCK';
      task.placements.delete(positionKey);
    }
    else if (block.name === task.desired.get(positionKey)) reason = 'REPAIR_NOT_NEEDED';
    else if (receipt.fingerprint !== blockFingerprint(block) || receipt.revision !== (task.revisions.get(positionKey) || 0)) {
      reason = 'REPAIR_TARGET_CHANGED';
      task.placements.delete(positionKey);
    }
    else if (!isRepairableStructure(bot, block)) reason = 'UNSAFE_REPAIR_TARGET';
    return { position: { ...receipt.position }, placed_block: receipt.item,
      desired_block: task.desired.get(positionKey), observed_block: block?.name || 'unknown',
      repair_eligible: reason == null, reason };
  });
  // A large already-correct floor must not hide the few late interior errors.
  // Exact totals refer to this complete survey; the bounded cells prioritize
  // eligible mismatches, then other mismatches, then already-correct cells.
  observed.sort((a, b) => (a.repair_eligible ? 0 : a.reason === 'REPAIR_NOT_NEEDED' ? 2 : 1) -
    (b.repair_eligible ? 0 : b.reason === 'REPAIR_NOT_NEEDED' ? 2 : 1));
  const owned = observed.slice(0, maxCells);
  const repairable = observed.filter(entry => entry.repair_eligible);
  return { active: true, task_id: task.taskId, world_session_id: task.worldSessionId,
    accepted_cell_count: task.desired.size, owned_placement_count: task.placements.size,
    surveyed_receipt_count: entries.length, repair_eligible_count: repairable.length,
    repairable_count: repairable.length,
    repairable_owned_mismatches: repairable.slice(0, maxCells).map(entry => ({
      position: entry.position, observed: entry.observed_block, desired: entry.desired_block,
      source: 'verified_current_task_placement' })),
    owned_placements: owned, complete: entries.length <= maxCells, omitted_count: Math.max(0, entries.length - maxCells),
    semantics: 'Eligibility is current provenance only; repair rechecks loaded state, tool, reach, neighbors, fixtures and footing.' };
}

/**
 * The model chooses correction coordinates; the executor only removes exact,
 * still-owned current-task placements that contradict the frozen goal. No
 * generic cobblestone demolition capability or JSON ownership override exists.
 */
async function repairBatch (bot, args = {}, constructionHandle = null) {
  const task = currentConstructionTask(bot, constructionHandle);
  if (Object.keys(args).some(name => !['positions', 'tool'].includes(name))) fail('INVALID_ARGUMENT',
    'repair_batch accepts only positions and an optional tool; ownership and scope come from the controller.');
  const { positions, tool } = parseDigBatch(bot, args);
  const result = { mode: 'own_placement_repair', task_id: task.taskId,
    requested_count: positions.length, planned_clearances: 0, tool: tool || 'auto',
    inventory_before: inventoryTotals(bot), inventory_after: null,
    cleared: [], skipped: [], failed: null, complete: false, verified: false };
  try {
    // Scope, identity, current desired state, hazards and tools for ALL cells
    // are checked before the first dig. A later invalid cell cannot authorize
    // avoidable partial demolition of an earlier cell.
    for (const target of positions) {
      const { block } = repairTarget(bot, task, target, 'preflight');
      assertSafeDigNeighbors(bot, target);
      assertSafeRepairOverburden(bot, target);
      assertNotPlayerFooting(bot, target);
      selectRepairTool(bot, block, tool);
    }
    result.planned_clearances = positions.length;
  } catch (error) { throwDigPartial(error, result, bot); }

  for (const target of positions) {
    try {
      let { block, desired } = repairTarget(bot, task, target, 'before_approach');
      assertSafeDigNeighbors(bot, target);
      assertSafeRepairOverburden(bot, target);
      assertNotPlayerFooting(bot, target);
      const stance = await reachDigStance(bot, target);
      currentConstructionTask(bot, constructionHandle);
      ({ block, desired } = repairTarget(bot, task, target, 'after_approach'));
      if (targetUnderFoot(footPosition(bot), target) || !freshDigStance(bot, target, footPosition(bot))) fail('STANCE_CHANGED',
        'The bot is no longer at a safe stance for owned-placement repair.', { position: plainPosition(target) });
      const selectedTool = selectRepairTool(bot, block, tool);
      if (selectedTool) await bot.equip(selectedTool, 'hand');
      currentConstructionTask(bot, constructionHandle);
      ({ block, desired } = repairTarget(bot, task, target, 'before_dig'));
      assertSafeDigNeighbors(bot, target);
      assertSafeRepairOverburden(bot, target);
      assertNotPlayerFooting(bot, target);
      if (targetUnderFoot(footPosition(bot), target) || !freshDigStance(bot, target, footPosition(bot))) fail('STANCE_CHANGED',
        'The bot is no longer at a safe repair stance.', { position: plainPosition(target) });
      if (!bot.canDigBlock(block)) fail('CANNOT_DIG', 'The owned repair block is no longer within safe digging reach.', {
        position: plainPosition(target) });
      const inventoryBefore = inventoryTotals(bot);
      await bot.dig(block);
      currentConstructionTask(bot, constructionHandle);
      if (bot.waitForTicks) await bot.waitForTicks(2);
      currentConstructionTask(bot, constructionHandle);
      const after = loadedBlock(bot, target);
      const inventoryAfter = inventoryTotals(bot);
      if (!isAir(after)) fail('REPAIR_NOT_VERIFIED', 'The exact owned block did not become air; stopped before another repair.', {
        position: plainPosition(target), before: block.name, after: after.name });
      task.placements.delete(key(target));
      result.cleared.push({ position: plainPosition(target), before: block.name, after: after.name,
        desired_block: desired, task_id: task.taskId, provenance: 'verified_current_task_placement', verified: true,
        tool: selectedTool?.name || 'hand', stance: plainPosition(stance.foot), navigation: stance.navigation,
        ...(stance.route_cost != null ? { route_cost: stance.route_cost } : {}),
        inventory_before: inventoryBefore, inventory_after: inventoryAfter,
        inventory_delta: inventoryDelta(inventoryBefore, inventoryAfter) });
    } catch (error) {
      result.failed = { position: plainPosition(target), code: error.code || 'REPAIR_FAILED', message: error.message, data: error.data || {} };
      throwDigPartial(error, result, bot);
    }
  }
  result.inventory_after = inventoryTotals(bot);
  result.complete = true;
  result.verified = true;
  return result;
}

module.exports = {
  placeBatch,
  // Expose narrow pure helpers for focused tests and future callers.  They do
  // not create an action capability or bypass the guarded bot supplied above.
  countItem,
  parseBatch,
  freshSupport,
  freshStance,
  digBatch,
  parseDigBatch,
  freshDigStance,
  isClearableNaturalBlock,
  beginConstructionTask,
  endConstructionTask,
  constructionStatus,
  hasConstructionTarget,
  guardConstructionAction,
  repairBatch
};
