/** Walking stays non-destructive; bounded uphill excavation is an explicit skill. */
const { Vec3 } = require('vec3');
const { goals } = require('mineflayer-pathfinder');
const { dataFor, hasSilkTouch } = require('./knowledge');
const { assertDigSafety } = require('./operations');

const TERRAIN = new Set([
  'stone', 'cobblestone', 'andesite', 'diorite', 'granite', 'dirt', 'grass_block',
  'coarse_dirt', 'rooted_dirt', 'deepslate', 'cobbled_deepslate', 'tuff', 'calcite',
  'mycelium', 'podzol', 'netherrack', 'end_stone'
]);
const FIXTURES = new Set(['chest', 'trapped_chest', 'ender_chest', 'crafting_table', 'furnace', 'blast_furnace', 'smoker']);
const LIMITS = Object.freeze({ rise: 8, steps: 12, blocks: 24, radius: 8, nodes: 768 });
const key = p => `${p.x},${p.y},${p.z}`;
const air = b => b && ['air', 'cave_air', 'void_air'].includes(b.name);
const hazard = b => !b || /^(?:water|lava|fire|soul_fire|cactus|magma_block|(?:soul_)?campfire|(?:red_)?sand|gravel|.*_concrete_powder|(?:chipped_|damaged_)?anvil|pointed_dripstone)$/.test(b.name);
const offsets = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];

function fail (code, message, data = {}) {
  const error = new Error(message);
  error.code = code;
  error.data = data;
  throw error;
}

function footPosition (bot) {
  const p = bot.entity?.position;
  if (!p) fail('WORLD_UNAVAILABLE', 'Cannot verify the current position.');
  return new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z));
}

async function walkTo (bot, goal, { timeoutMs = 45000, stalledMs = 15000, intervalMs = 500 } = {}) {
  const started = Date.now();
  let advancedAt = started;
  const score = () => {
    try {
      if (typeof goal.heuristic === 'function') return goal.heuristic(bot.entity.position);
      const p = bot.entity.position;
      return Math.hypot(p.x - goal.x, p.y - goal.y, p.z - goal.z);
    } catch (_) { return Infinity; }
  };
  let best = score();
  let timer;
  const watchdog = new Promise((_, reject) => {
    timer = setInterval(() => {
      const current = score();
      if (Number.isFinite(current) && current < best - 0.1) {
        best = current;
        advancedAt = Date.now();
      }
      const timedOut = Date.now() - started >= timeoutMs;
      if (!timedOut && Date.now() - advancedAt < stalledMs) return;
      const error = new Error(timedOut ? 'Walking deadline exceeded.' : 'No improvement toward the walking goal; stopping instead of circling.');
      error.code = timedOut ? 'NAVIGATION_TIMEOUT' : 'NO_NAVIGATION_PROGRESS';
      // Reject first so a synchronous pathfinder-stop rejection does not hide
      // the actionable watchdog reason. The old path's rejection is handled by
      // Promise.race; it cannot issue a subsequent skill action.
      reject(error);
      try { bot.pathfinder.setGoal(null); } catch (_) {}
      clearInterval(timer);
    }, intervalMs);
  });
  try {
    return await Promise.race([bot.pathfinder.goto(goal), watchdog]);
  } finally {
    clearInterval(timer);
  }
}

async function pathCost (bot, goal) {
  const moves = bot.pathfinder?.movements;
  if (!moves || moves.canDig !== false || moves.allow1by1towers !== false) {
    fail('UNSAFE_NAVIGATION', 'Ordinary navigation must disable digging and tower building.');
  }
  if (typeof bot.pathfinder.getPathFromTo === 'function') {
    const generator = bot.pathfinder.getPathFromTo(moves, bot.entity.position, goal, { timeout: 400, tickTimeout: 10 });
    for (const { result } of generator) {
      if (result.status === 'success') return result.cost;
      if (result.status !== 'partial') return null;
      await new Promise(resolve => setImmediate(resolve));
    }
    return null;
  }
  if (typeof bot.pathfinder.getPathTo !== 'function') fail('PATHFINDER_UNAVAILABLE', 'Cannot check safe reachability.');
  const result = bot.pathfinder.getPathTo(moves, goal, 400);
  return result.status === 'success' ? result.cost : null;
}

function excavationTool (bot, block) {
  const definition = dataFor(bot).blocksByName[block.name];
  if (!definition || !TERRAIN.has(block.name) || definition.diggable === false || definition.hardness < 0) return null;
  const tools = bot.inventory.items().filter(item =>
    /_(?:pickaxe|shovel|axe)$/.test(item.name) && !hasSilkTouch(item) &&
    (!definition.harvestTools || definition.harvestTools[item.type]) &&
    (!block.canHarvest || block.canHarvest(item.type))
  );
  tools.sort((a, b) => {
    const time = item => block.digTime ? block.digTime(item.type) : 0;
    return time(a) - time(b);
  });
  // Bare hands may clear soft terrain, never harvest-tool-restricted stone.
  return tools[0] || (!definition.harvestTools ? { hand: true } : null);
}

function safeClearance (bot, position, removed = new Set()) {
  const block = bot.blockAt(position);
  if (!block || hazard(block)) return false;
  if (!removed.has(key(position)) && !air(block) && !excavationTool(bot, block)) return false;
  // Loaded neighbours are required. Do not open liquid pockets or remove the
  // support below falling blocks. Unknown chunk edges are not permission to dig.
  for (const [dx, dy, dz] of offsets) {
    const adjacent = bot.blockAt(position.offset(dx, dy, dz));
    if (!adjacent || ['water', 'lava'].includes(adjacent.name) || (dy === 1 && hazard(adjacent))) return false;
    if (dy === 1 && FIXTURES.has(adjacent.name) && !air(block) && !removed.has(key(position))) return false;
  }
  return true;
}

function stepPlan (bot, from, to, removed) {
  const support = bot.blockAt(to.offset(0, -1, 0));
  if (!support || removed.has(key(support.position)) || support.boundingBox !== 'block' || hazard(support)) return null;
  // A jumping step also needs clearance above the CURRENT standing position.
  const clearance = to.y > from.y
    ? [from.offset(0, 2, 0), to.offset(0, 1, 0), to]
    : [to.offset(0, 1, 0), to];
  const nextRemoved = new Set(removed);
  const digs = [];
  for (const position of clearance) {
    if (!safeClearance(bot, position, nextRemoved)) return null;
    if (!air(bot.blockAt(position)) && !nextRemoved.has(key(position))) {
      nextRemoved.add(key(position));
      digs.push(position);
    }
  }
  return { from, to, digs, removed: nextRemoved };
}

async function planStaircase (bot, goal) {
  const start = footPosition(bot);
  const currentSupport = bot.blockAt(start.offset(0, -1, 0));
  if (!currentSupport || currentSupport.boundingBox !== 'block' || hazard(currentSupport) ||
      !air(bot.blockAt(start)) || !air(bot.blockAt(start.offset(0, 1, 0)))) {
    fail('ESCAPE_UNSAFE', 'I need stable footing and clear headroom before making a staircase.');
  }
  const queue = [{ position: start, removed: new Set(), steps: [], cost: 0 }];
  const minimumY = Number.isFinite(goal.y) ? goal.y : start.y + 1;
  const visited = new Map();
  let examined = 0;
  while (queue.length && examined++ < LIMITS.nodes) {
    queue.sort((a, b) => (a.cost + 4 * goal.heuristic(a.position)) - (b.cost + 4 * goal.heuristic(b.position)) ||
      goal.heuristic(a.position) - goal.heuristic(b.position));
    const node = queue.shift();
    // GoalGetToBlock also permits interaction from underneath a chest. Escape
    // should climb to its level instead, not excavate a tunnel under its base.
    if (node.position.y > start.y && node.position.y >= minimumY && goal.isEnd(node.position)) return node.steps;
    if (node.steps.length >= LIMITS.steps) continue;
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      for (const dy of [1, 0]) {
        const to = node.position.offset(dx, dy, dz);
        if (to.y - start.y > LIMITS.rise || Math.abs(to.x - start.x) + Math.abs(to.z - start.z) > LIMITS.radius) continue;
        const step = stepPlan(bot, node.position, to, node.removed);
        if (!step || step.removed.size > LIMITS.blocks) continue;
        const cost = node.cost + 1 + step.digs.length;
        // Include planned excavation: two routes to the same foot position can
        // have different support/headroom after their earlier blocks are removed.
        const signature = key(to) + '|' + [...step.removed].sort().join(';');
        if ((visited.get(signature) ?? Infinity) <= cost) continue;
        visited.set(signature, cost);
        queue.push({ position: to, removed: step.removed, steps: [...node.steps, step], cost });
      }
    }
    if (examined % 24 === 0) await new Promise(resolve => setImmediate(resolve));
  }
  fail('ESCAPE_NO_ROUTE', 'No controlled uphill staircase fits the loaded terrain, tools, or safety limits (8 blocks up, 12 steps, 24 blocks). I have not started digging.');
}

async function executeStaircase (bot, goal, progress = () => {}) {
  const before = footPosition(bot);
  const plan = await planStaircase(bot, goal); // Find a bounded route BEFORE removing anything.
  const dug = [];
  let steps = 0;
  for (const step of plan) {
    if (!footPosition(bot).equals(step.from)) fail('ESCAPE_CHANGED', 'My position changed; stopping this staircase plan.');
    const freshStep = stepPlan(bot, step.from, step.to, new Set());
    if (!freshStep) fail('ESCAPE_CHANGED', 'The staircase terrain changed or became unsafe. Stopping before further digging.', { dug });
    if (dug.length + freshStep.digs.length > LIMITS.blocks) fail('ESCAPE_LIMIT', 'Terrain changes would exceed the staircase excavation limit.', { dug });
    progress(`making staircase step ${steps + 1}/${plan.length}`);
    for (const position of freshStep.digs) {
      if (!footPosition(bot).equals(step.from)) fail('ESCAPE_CHANGED', 'My position changed during excavation; stopping this staircase plan.', { dug });
      const block = bot.blockAt(position);
      if (air(block)) continue;
      const tool = excavationTool(bot, block);
      if (!safeClearance(bot, position) || !tool) fail('ESCAPE_CHANGED', 'This clearance block is no longer safe to excavate.', { dug });
      assertDigSafety(bot, block);
      if (!bot.canDigBlock(block)) fail('ESCAPE_OUT_OF_REACH', 'Cannot reach the next staircase clearance block safely.', { dug });
      if (!tool.hand) await bot.equip(tool, 'hand');
      else if (bot.unequip) await bot.unequip('hand');
      console.log('[Staircase Dig]', JSON.stringify({ block: block.name, position }));
      await bot.dig(block);
      for (let tick = 0; tick < 10 && !air(bot.blockAt(position)); tick++) await bot.waitForTicks(1);
      if (!air(bot.blockAt(position))) fail('ESCAPE_BREAK_UNVERIFIED', 'The staircase block did not become air; I will not walk through it.', { dug });
      dug.push({ block: block.name, x: position.x, y: position.y, z: position.z });
    }
    const ready = stepPlan(bot, step.from, step.to, new Set());
    if (!ready || ready.digs.length) fail('ESCAPE_CHANGED', 'The next step no longer has verified clear headroom and support.', { dug });
    // Pathfinder still cannot dig. It only walks/jumps through already-verified clearance.
    await walkTo(bot, new goals.GoalBlock(step.to.x, step.to.y, step.to.z));
    for (let tick = 0; tick < 10 && (!footPosition(bot).equals(step.to) || bot.entity.onGround === false); tick++) await bot.waitForTicks(1);
    if (!footPosition(bot).equals(step.to) || bot.entity.onGround === false) fail('ESCAPE_MOVE_UNVERIFIED', 'Could not confirm standing on the new staircase step.', { dug });
    steps++;
    if (steps % 2 === 0) await new Promise(resolve => setImmediate(resolve));
  }
  if (!goal.isEnd(footPosition(bot))) fail('ESCAPE_MOVE_UNVERIFIED', 'The staircase did not reach the requested approach position.', { dug });
  const result = { kind: 'controlled_staircase', before, after: footPosition(bot), steps, dug };
  console.log('[Staircase Complete]', JSON.stringify(result));
  return result;
}

async function moveToGoal (bot, goal, progress = () => {}) {
  if (await pathCost(bot, goal) != null) {
    await walkTo(bot, goal);
    return { kind: 'walking' };
  }
  if (!Number.isFinite(goal.y) || goal.y <= footPosition(bot).y) {
    fail('NO_WALKING_ROUTE', 'No walking route is available; controlled excavation is only permitted for an uphill escape.');
  }
  progress('planning a controlled staircase out');
  return executeStaircase(bot, goal, progress);
}

module.exports = { pathCost, walkTo, moveToGoal, executeStaircase, planStaircase, LIMITS };
