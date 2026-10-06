/** Safe, grounded terrain-resource collection and evidence-based chest transfers. */
const { Vec3 } = require('vec3');
const { goals } = require('mineflayer-pathfinder');
const { harvestTool, hasSilkTouch, dataFor } = require('./knowledge');
const { assertDigSafety } = require('./operations');
const { pathCost, walkTo, moveToGoal } = require('./navigation');
const { recoverPickup } = require('./pickup');

function fail (code, message, data = {}) {
  const error = new Error(message);
  error.code = code;
  error.data = data;
  throw error;
}
const countItems = (items, name) => items.filter(item => item && item.name === name).reduce((n, item) => n + item.count, 0);
const countItem = (bot, name) => countItems(bot.inventory.items(), name);
const air = block => block && ['air', 'cave_air', 'void_air'].includes(block.name);
const hazardous = block => !block || /^(?:water|lava|fire|soul_fire|cactus|magma_block|campfire|sand|gravel)$/.test(block.name);
const key = p => [p.x, p.y, p.z].join(',');

function safeStances (bot, block) {
  const positions = [];
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    for (const dy of [0, -1]) {
      const foot = new Vec3(block.position.x + dx, block.position.y + dy, block.position.z + dz);
      const floor = bot.blockAt(foot.offset(0, -1, 0));
      if (air(bot.blockAt(foot)) && air(bot.blockAt(foot.offset(0, 1, 0))) &&
          floor && floor.boundingBox === 'block' && !hazardous(floor)) positions.push(foot);
    }
  }
  return positions;
}

function safeBlock (bot, block) {
  try { assertDigSafety(bot, block); } catch (_) { return false; }
  const above = bot.blockAt(block.position.offset(0, 1, 0));
  if (hazardous(above)) return false;
  // Do not release liquids or falling blocks into the mining position.
  return [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dz]) => {
    const neighbour = bot.blockAt(block.position.offset(dx, 0, dz));
    return neighbour && !['water', 'lava'].includes(neighbour.name);
  });
}

async function selectTarget (bot, args, excluded) {
  const data = dataFor(bot);
  // Domain grounding: coal ore never becomes a cobblestone candidate.
  const sources = (args.item === 'dirt' ? ['dirt', 'grass_block'] : ['stone', 'cobblestone']).filter(name =>
    data.blockLoot[name]?.drops.some(drop => drop.item === args.item && !drop.silkTouch)
  );
  const positions = bot.findBlocks({
    matching: block => block && sources.includes(block.name),
    // Filter exposed faces BEFORE the count limit. Otherwise nearby buried
    // stone can fill all 64 slots and hide the mountain eight blocks away.
    useExtraInfo: block => block && !excluded.has(key(block.position)) &&
      safeBlock(bot, block) && safeStances(bot, block).length > 0,
    maxDistance: args.maxDistance, count: 64
  }) || [];
  const candidates = [];
  for (const position of positions) {
    if (excluded.has(key(position))) continue;
    const block = bot.blockAt(position);
    if (!block || !sources.includes(block.name) || !safeBlock(bot, block)) continue;
    const tool = args.item === 'dirt'
      ? bot.inventory.items().find(item => item.name.endsWith('_shovel') && !hasSilkTouch(item)) || null
      : harvestTool(bot, block);
    if (!tool && args.item !== 'dirt') continue;
    const stances = safeStances(bot, block);
    if (stances.length) candidates.push({ block, tool, stances });
  }
  let best = null;
  const scanStarted = Date.now();
  for (const candidate of candidates) {
    if (Date.now() - scanStarted > 2000) break;
    for (const stance of candidate.stances) {
      if (Date.now() - scanStarted > 2000) break;
      const goal = new goals.GoalBlock(stance.x, stance.y, stance.z);
      const cost = await pathCost(bot, goal);
      if (cost != null && (!best || cost < best.cost)) best = { ...candidate, stance, goal, cost };
    }
  }
  if (!best) fail('NO_SAFE_RESOURCE', `No exposed ${sources.join('/')} has a safe walking route. I will not dig down or tunnel; help me reach an exposed face.`);
  return best;
}

async function waitCount (bot, item, predicate) {
  for (let i = 0; i < 40; i++) {
    const count = countItem(bot, item);
    if (predicate(count)) return count;
    if (bot.waitForTicks) await bot.waitForTicks(1);
    else await new Promise(resolve => setTimeout(resolve, 50));
  }
  return countItem(bot, item);
}

async function mineResource (bot, args, progress = () => {}, options = {}) {
  // Staircase excavation may have already supplied part/all of this collection.
  const before = options.baseline ?? countItem(bot, args.item);
  const target = args.collectionMode === 'ensure_inventory' ? args.count : before + args.count;
  const excluded = new Set();
  const minedPositions = [];
  if (args.item === 'cobblestone' && countItem(bot, args.item) < target && !bot.inventory.items().some(item => item.name.endsWith('_pickaxe') && !hasSilkTouch(item))) {
    fail('TOOL_REQUIRED', 'Give me a usable pickaxe without Silk Touch before collecting cobblestone.');
  }
  while (countItem(bot, args.item) < target) {
    const selected = await selectTarget(bot, args, excluded);
    const { block, tool, goal } = selected;
    excluded.add(key(block.position));
    console.log('[Resource Target]', JSON.stringify({
      block: block.name, position: block.position, stance: selected.stance, routeCost: selected.cost, tool: tool?.name || 'hand'
    }));
    progress('walking to exposed ' + args.item + ' source');
    try {
      await walkTo(bot, goal);
    } catch (error) {
      if (['TASK_TIMEOUT', 'CANCELLED'].includes(error.code)) throw error;
      // A route can change after planning. Re-observe and try another safe face,
      // rather than switching to tunneling or retrying the same failed target.
      console.warn('[Resource Route Rejected]', key(block.position), error.message);
      continue;
    }
    const current = bot.blockAt(block.position);
    if (!current || current.name !== block.name || !safeBlock(bot, current)) continue;
    if (!bot.canDigBlock(current)) continue;
    if (tool) await bot.equip(tool, 'hand');
    else if (bot.heldItem && bot.unequip) await bot.unequip('hand');
    progress('mining and collecting ' + args.item);
    const previous = countItem(bot, args.item);
    await bot.dig(current);
    let after = await waitCount(bot, args.item, n => n > previous);
    if (after <= previous) {
      progress('recovering a dropped ' + args.item + ' on a safe route');
      after = await recoverPickup(bot, args.item, previous, current.position);
    }
    if (after <= previous) fail('PICKUP_NOT_VERIFIED', `The block broke, but ${args.item} did not enter inventory. Stopping rather than mining more blindly.`);
    minedPositions.push({ x: block.position.x, y: block.position.y, z: block.position.z });
  }
  return {
    message: args.collectionMode === 'ensure_inventory'
      ? `Holding ${countItem(bot, args.item)} ${args.item}; collected ${countItem(bot, args.item) - before} more.`
      : 'Collected ' + args.count + ' ' + args.item + '.',
    data: { item: args.item, requested_count: args.count, before, after: countItem(bot, args.item),
      observed_mined: countItem(bot, args.item) - before, target_count: target,
      collection_mode: args.collectionMode || 'additional', mined_positions: minedPositions }
  };
}

async function resolveChest (bot, args, progress = () => {}) {
  const positions = bot.findBlocks({
    matching: block => block && block.name === 'chest', maxDistance: args.maxDistance, count: 16
  }) || [];
  let inspected = 0;
  const routeFailures = [];
  for (const position of positions) {
    const block = bot.blockAt(position);
    if (!block || block.name !== 'chest') continue;
    const goal = new goals.GoalGetToBlock(position.x, position.y, position.z);
    let navigation;
    try {
      navigation = await moveToGoal(bot, goal, progress);
    } catch (error) {
      if (!['NO_WALKING_ROUTE', 'ESCAPE_NO_ROUTE', 'ESCAPE_UNSAFE'].includes(error.code)) throw error;
      routeFailures.push({ position, reason: error.message });
      console.warn('[Chest Route Rejected]', JSON.stringify(routeFailures[routeFailures.length - 1]));
      continue;
    }
    // A staircase can change terrain before the inventory can be inspected;
    // room is still checked before deliberately collecting more resources.
    const fresh = bot.blockAt(position);
    if (!fresh || fresh.name !== 'chest') continue;
    const chest = await bot.openChest(fresh);
    try {
      inspected++;
      const slots = chest.slots?.slice(0, chest.inventoryStart);
      if (!slots) fail('CHEST_UNAVAILABLE', 'Cannot inspect chest capacity safely.');
      let empty = slots.filter(item => !item).length;
      const totals = new Map();
      for (const goal of args.items || [args]) totals.set(goal.item, (totals.get(goal.item) || 0) + goal.count);
      for (const [name, count] of totals) {
        const size = dataFor(bot).itemsByName[name]?.stackSize || 64;
        const mergeRoom = slots.filter(item => item?.name === name && !item.nbt)
          .reduce((space, item) => space + Math.max(0, size - item.count), 0);
        empty -= Math.ceil(Math.max(0, count - mergeRoom) / size);
      }
      if (empty >= 0) return { position, goal, navigation };
    } finally {
      try { chest.close(); } catch (_) {}
    }
  }
  if (!positions.length) fail('CHEST_NOT_FOUND', 'I cannot find a chest in loaded range.');
  if (inspected) fail('CHEST_FULL', 'The accessible chests do not have room for these items. Please make space.', { route_failures: routeFailures });
  fail('CHEST_UNREACHABLE', 'I can see a chest, but neither a walking route nor a controlled uphill staircase reached it.', { route_failures: routeFailures });
}

async function depositItem (bot, args, chestTarget, progress = () => {}) {
  const destination = chestTarget || await resolveChest(bot, args, progress);
  progress('walking to the chest');
  const navigation = await moveToGoal(bot, destination.goal, progress);
  const block = bot.blockAt(destination.position);
  if (!block || block.name !== 'chest') fail('CHEST_CHANGED', 'The selected chest is no longer present.');
  const stack = bot.inventory.items().find(item => item.name === args.item);
  const inventoryBefore = countItem(bot, args.item);
  if (!stack || inventoryBefore < args.count) fail('INSUFFICIENT_ITEMS', 'Not enough ' + args.item + ' to deposit.');
  const chest = await bot.openChest(block);
  let chestBefore;
  let sourceBefore;
  try {
    chestBefore = countItems(chest.containerItems(), args.item);
    // Mineflayer's bot.inventory may be stale while a container is open. The
    // player region of this freshly opened window is the matching observation.
    sourceBefore = chest.inventoryEnd != null
      ? countItems(chest.slots.slice(chest.inventoryStart, chest.inventoryEnd), args.item) : inventoryBefore;
    if (sourceBefore < args.count) fail('INSUFFICIENT_ITEMS', 'Fresh chest-window inventory has too few ' + args.item + '.');
    progress('depositing exact count');
    await chest.deposit(stack.type, null, args.count);
  } finally {
    try { chest.close(); } catch (_) {}
  }
  // A new window_items snapshot is independent of optimistic local click
  // updates. Do not blindly repeat a transfer whose outcome is uncertain.
  if (bot.waitForTicks) await bot.waitForTicks(2);
  progress('reopening chest to verify transfer');
  const verification = await bot.openChest(block);
  try {
    const chestAfter = countItems(verification.containerItems(), args.item);
    const inventoryAfter = verification.inventoryEnd != null
      ? countItems(verification.slots.slice(verification.inventoryStart, verification.inventoryEnd), args.item)
      : countItem(bot, args.item);
    const evidence = {
      item: args.item, requested_count: args.count, inventory_before: sourceBefore, inventory_after: inventoryAfter,
      chest_before: chestBefore, chest_after: chestAfter,
      verification_source: 'reopened_container',
      chest_position: { x: destination.position.x, y: destination.position.y, z: destination.position.z },
      navigation: destination.navigation?.kind === 'controlled_staircase' ? destination.navigation : navigation
    };
    if (chestAfter - chestBefore !== args.count || sourceBefore - inventoryAfter !== args.count) {
      fail('DEPOSIT_NOT_VERIFIED', 'The chest/inventory counts did not confirm the requested transfer.', evidence);
    }
    return { message: 'Deposited exactly ' + args.count + ' ' + args.item + ' in the chest.', data: evidence };
  } finally {
    try { verification.close(); } catch (_) {}
  }
}

module.exports = { mineResource, depositItem, resolveChest, safeStances };
