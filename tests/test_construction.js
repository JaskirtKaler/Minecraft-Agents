const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const { EventEmitter } = require('events');
const { placeBatch, digBatch, freshStance, beginConstructionTask, endConstructionTask,
  constructionStatus, hasConstructionTarget, guardConstructionAction, repairBatch } = require('../bot/construction');
const { runOperation } = require('../bot/operations');

const AIR = new Set(['air', 'cave_air', 'void_air']);
const key = position => `${position.x},${position.y},${position.z}`;

function fixture ({ inventoryCount = 2, inventoryItems, targetNames = {}, onEquip, placeEffect, digEffect } = {}) {
  const world = new Map(Object.entries(targetNames));
  const inventory = (inventoryItems || []).map((entry, index) => ({
    name: entry.name,
    type: entry.type ?? data.itemsByName[entry.name]?.id,
    count: entry.count,
    slot: entry.slot ?? (10 + index)
  }));
  if (!inventoryItems && inventoryCount > 0) {
    // Deliberately split the supply: the executor must total all matching
    // stacks rather than inspecting only its first inventory item.
    const first = Math.min(1, inventoryCount);
    inventory.push({ name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: first, slot: 10 });
    if (inventoryCount > first) inventory.push({ name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: inventoryCount - first, slot: 11 });
  }
  let held = null;
  const calls = { blockAt: 0, pathCosts: 0, goto: [], equips: [], places: [], digs: [], ticks: 0 };
  const block = (value, position) => {
    const name = typeof value === 'object' ? value.name : value;
    return {
    name, type: data.blocksByName[name]?.id,
    stateId: value?.stateId ?? data.blocksByName[name]?.defaultState,
    getProperties: () => ({ ...(value?.properties || {}) }),
    position: new Vec3(position.x, position.y, position.z),
    boundingBox: AIR.has(name) ? 'empty' : 'block',
    canHarvest: type => !data.blocksByName[name]?.harvestTools || !!data.blocksByName[name].harvestTools[type]
    };
  };
  const events = new EventEmitter();
  const bot = {
    version: '1.20.1',
    game: { dimension: 'overworld' },
    on: (...args) => events.on(...args),
    removeListener: (...args) => events.removeListener(...args),
    emit: (...args) => events.emit(...args),
    entity: { position: new Vec3(0, 64, 0) },
    inventory: { items: () => inventory.filter(stack => stack.count > 0) },
    pathfinder: {
      movements: { canDig: false, allow1by1towers: false },
      getPathTo: (_moves, goal) => {
        calls.pathCosts++;
        return { status: 'success', cost: Math.abs(goal.x - bot.entity.position.x) + Math.abs(goal.z - bot.entity.position.z) };
      },
      async goto (goal) {
        calls.goto.push(goal);
        bot.entity.position = new Vec3(goal.x, goal.y, goal.z);
      },
      setGoal () {}
    },
    blockAt (position) {
      calls.blockAt++;
      if (world.get(key(position)) === null) return null;
      const name = world.has(key(position)) ? world.get(key(position)) : position.y < 64 ? 'stone' : 'air';
      return block(name, position);
    },
    async equip (stack, destination) {
      assert.equal(destination, 'hand');
      held = stack;
      calls.equips.push(stack.name);
      if (onEquip) await onEquip({ bot, world, inventory, calls, stack });
    },
    async placeBlock (reference, face) {
      const target = reference.position.plus(face);
      calls.places.push({ reference: reference.position, face, target });
      if (placeEffect) return placeEffect({ bot, world, inventory, calls, held, reference, face, target });
      const before = bot.blockAt(target);
      world.set(key(target), held.name);
      held.count--;
      bot.emit('blockUpdate', before, bot.blockAt(target));
    },
    canDigBlock (target) {
      return !!target && target.position.distanceTo(bot.entity.position) <= 4.5;
    },
    async dig (target) {
      calls.digs.push(target.position);
      if (digEffect) return digEffect({ bot, world, inventory, calls, target });
      world.set(key(target.position), 'air');
      bot.emit('blockUpdate', target, bot.blockAt(target.position));
    },
    async waitForTicks (ticks) {
      assert.equal(ticks, 2);
      calls.ticks++;
    }
  };
  return { bot, world, inventory, calls };
}

async function testPlacesEveryInitiallyAirCellWithAllMatchingStacks () {
  const { bot, world, inventory, calls } = fixture({ inventoryCount: 2 });
  const result = await placeBatch(bot, { item: 'cobblestone', positions: [
    { x: 2, y: 64, z: 0 }, { x: 3, y: 64, z: 0 }
  ] });
  assert.equal(result.complete, true);
  assert.equal(result.verified, true);
  assert.equal(result.planned_placements, 2);
  assert.equal(result.placements.length, 2);
  assert.equal(result.inventory_before, 2);
  assert.equal(result.inventory_after, 0);
  assert.equal(calls.places.length, 2);
  assert.equal(calls.goto.length, 0, 'Already reachable cells should not pathfind.');
  assert.equal(world.get('2,64,0'), 'cobblestone');
  assert.equal(world.get('3,64,0'), 'cobblestone');
  assert.equal(inventory.reduce((total, stack) => total + stack.count, 0), 0);
}

async function testPlacementGroundStanceSurvivesInvalidCloserCandidateCap () {
  const { bot, calls } = fixture({ inventoryCount: 1, targetNames: {
    '0,50,0': 'air', '0,51,0': 'air'
  } });
  bot.entity.position = new Vec3(0, 50, 0);
  const result = await placeBatch(bot, { item: 'cobblestone', positions: [{ x: 0, y: 64, z: 0 }] });
  assert.equal(result.verified, true);
  assert.ok(calls.goto.length > 0);
  assert.equal(bot.entity.position.y, 64, 'Closer impossible buried cells must not hide a valid ground stance.');
  assert.equal(calls.places.length, 1);
}

async function testAlreadyDesiredAndOccupiedCellsNeverBecomeNewWork () {
  const { bot, calls } = fixture({ inventoryCount: 0, targetNames: {
    '1,64,0': 'cobblestone', '2,64,0': 'dirt'
  } });
  const result = await placeBatch(bot, { item: 'cobblestone', positions: [
    { x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }
  ] });
  assert.equal(result.planned_placements, 0);
  assert.deepEqual(result.skipped.map(entry => entry.reason), ['already_desired', 'occupied_unrelated']);
  assert.equal(result.complete, false, 'A different pre-existing block is preserved but remains unresolved.');
  assert.equal(result.verified, false);
  assert.equal(calls.equips.length, 0);
  assert.equal(calls.places.length, 0);
}

async function testFullScopeAndSupplyArePreflightedBeforeMutating () {
  const far = fixture({ inventoryCount: 2 });
  await assert.rejects(placeBatch(far.bot, { item: 'cobblestone', positions: [
    { x: 1, y: 64, z: 0 }, { x: 65, y: 64, z: 0 }
  ] }), error => error.code === 'OUT_OF_RANGE');
  assert.equal(far.calls.blockAt, 0, 'A far later target must prevent every world lookup.');
  assert.equal(far.calls.places.length, 0);

  const short = fixture({ inventoryCount: 1 });
  await assert.rejects(placeBatch(short.bot, { item: 'cobblestone', positions: [
    { x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }
  ] }), error => {
    assert.equal(error.code, 'INSUFFICIENT_ITEMS');
    assert.equal(error.data.partial.planned_placements, 2);
    assert.equal(error.data.partial.placements.length, 0);
    return true;
  });
  assert.equal(short.calls.equips.length, 0);
  assert.equal(short.calls.places.length, 0, 'Insufficient supplies must not start a partial build.');
}

async function testRoofCanBePlacedFromAnInsideFloorStance () {
  const { bot, calls } = fixture({ inventoryCount: 1, targetNames: { '1,67,0': 'stone' } });
  const result = await placeBatch(bot, { item: 'cobblestone', positions: [{ x: 0, y: 67, z: 0 }] });
  assert.equal(result.verified, true);
  assert.equal(calls.goto.length, 0, 'A floor stance three blocks below a roof is within the existing 4.5-block reach.');
  assert.equal(calls.places.length, 1);
  assert.deepEqual(calls.places[0].reference, new Vec3(1, 67, 0));
  assert.deepEqual(calls.places[0].target, new Vec3(0, 67, 0));
  assert.deepEqual(result.placements[0].stance, { x: 0, y: 64, z: 0 });
}

async function testLastFloorCellCanUseTopOfAdjacentPlacedFloorWithoutBodyCollision () {
  const airFloors = {};
  for (const [x, z] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    airFloors[`${x},63,${z}`] = 'air';
  }
  // The only stable nearby stance is on top of this already-placed adjacent
  // floor cell.  This models filling the last cell of a surrounded floor.
  airFloors['1,64,0'] = 'cobblestone';
  const { bot, calls } = fixture({ inventoryCount: 1, targetNames: airFloors });
  bot.entity.position = new Vec3(5, 64, 0);
  const target = new Vec3(0, 64, 0);
  assert.equal(freshStance(bot, target, new Vec3(0, 64, 0)), null, 'The bot cannot occupy the placement cell.');
  assert.equal(freshStance(bot, target, new Vec3(0, 63, 0)), null, 'The target cannot occupy the bot head cell.');

  const result = await placeBatch(bot, { item: 'cobblestone', positions: [target] });
  assert.equal(result.verified, true);
  assert.equal(calls.goto.length, 1);
  assert.deepEqual(bot.entity.position, new Vec3(1, 65, 0));
  assert.deepEqual(result.placements[0].stance, { x: 1, y: 65, z: 0 });
  assert.deepEqual(calls.places[0].target, target);
}

async function testWalksOnlyToFreshSafeStanceAndStopsOnFirstFailure () {
  const walked = fixture({ inventoryCount: 1 });
  const routed = await placeBatch(walked.bot, { item: 'cobblestone', positions: [{ x: 5, y: 64, z: 0 }] });
  assert.equal(routed.verified, true);
  assert.equal(walked.calls.goto.length, 1);
  assert.deepEqual(walked.bot.entity.position, new Vec3(4, 64, 0));
  assert.equal(walked.calls.places.length, 1);

  const changed = fixture({ inventoryCount: 2, onEquip: ({ world, calls }) => {
    if (calls.equips.length === 2) world.set('3,63,0', 'air');
  } });
  await assert.rejects(placeBatch(changed.bot, { item: 'cobblestone', positions: [
    { x: 1, y: 64, z: 0 }, { x: 3, y: 64, z: 0 }
  ] }), error => {
    assert.equal(error.code, 'NO_PLACEMENT_SUPPORT');
    assert.equal(error.data.partial.placements.length, 1);
    assert.deepEqual(error.data.partial.failed.position, { x: 3, y: 64, z: 0 });
    return true;
  });
  assert.equal(changed.calls.places.length, 1, 'A changed second support must stop the batch instead of blindly continuing.');
}

async function testRequiresBothBlockAndExactInventoryEvidenceAndPreservesCancellation () {
  const unverifiable = fixture({ inventoryCount: 1, placeEffect: ({ world, target }) => {
    world.set(key(target), 'cobblestone'); // Simulate a stale inventory update.
  } });
  await assert.rejects(placeBatch(unverifiable.bot, { item: 'cobblestone', positions: [{ x: 1, y: 64, z: 0 }] }), error => {
    assert.equal(error.code, 'PLACE_NOT_VERIFIED');
    assert.equal(error.data.partial.placements.length, 0);
    assert.equal(error.data.partial.failed.code, 'PLACE_NOT_VERIFIED');
    return true;
  });

  const cancelled = fixture({ inventoryCount: 1, onEquip: () => {
    const error = new Error('Task stopped by player');
    error.code = 'CANCELLED';
    throw error;
  } });
  await assert.rejects(placeBatch(cancelled.bot, { item: 'cobblestone', positions: [{ x: 1, y: 64, z: 0 }] }), error => {
    assert.equal(error.code, 'CANCELLED', 'Cancellation must not be converted into a retryable placement failure.');
    assert.equal(error.data.partial.failed.code, 'CANCELLED');
    return true;
  });
  assert.equal(cancelled.calls.places.length, 0);
}

async function testGuardedBatchCannotPlaceAfterItsOperationExpires () {
  let releaseEquip;
  const expired = fixture({ inventoryCount: 1, onEquip: () => new Promise(resolve => { releaseEquip = resolve; }) });
  const job = runOperation(expired.bot,
    guarded => placeBatch(guarded, { item: 'cobblestone', positions: [{ x: 1, y: 64, z: 0 }] }),
    { timeoutMs: 20 });
  for (let attempt = 0; !releaseEquip && attempt < 10; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 1));
  }
  assert.equal(typeof releaseEquip, 'function');
  await assert.rejects(job, error => error.code === 'TASK_TIMEOUT');
  releaseEquip();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(expired.calls.places.length, 0, 'The revoked facade must prevent the post-equip placeBlock call.');
}

async function testRejectsDuplicateAndRestrictedItemsBeforeActions () {
  const duplicate = fixture({ inventoryCount: 2 });
  await assert.rejects(placeBatch(duplicate.bot, { item: 'cobblestone', positions: [
    { x: 1, y: 64, z: 0 }, { x: 1, y: 64, z: 0 }
  ] }), error => error.code === 'DUPLICATE_POSITION');
  assert.equal(duplicate.calls.blockAt, 0);

  const restricted = fixture({ inventoryCount: 2 });
  await assert.rejects(placeBatch(restricted.bot, { item: 'gravel', positions: [{ x: 1, y: 64, z: 0 }] }),
    error => error.code === 'RESTRICTED_BLOCK');
  assert.equal(restricted.calls.blockAt, 0);
  assert.equal(restricted.calls.places.length, 0);
}

async function testDigBatchClearsOnlySelectedNaturalBlocksAndReportsDrops () {
  const { bot, calls } = fixture({ inventoryItems: [
    { name: 'wooden_pickaxe', count: 1 }, { name: 'wooden_axe', count: 1 }
  ], targetNames: { '1,64,0': 'stone', '2,64,0': 'oak_log' } });
  const result = await digBatch(bot, { positions: [{ x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }] });
  assert.equal(result.complete, true);
  assert.equal(result.verified, true);
  assert.deepEqual(calls.digs, [new Vec3(1, 64, 0), new Vec3(2, 64, 0)]);
  assert.deepEqual(result.cleared.map(entry => entry.tool), ['wooden_pickaxe', 'wooden_axe']);
  assert.deepEqual(result.cleared.map(entry => entry.inventory_delta), [{}, {}],
    'Pickup is not required for clearing; each cell still reports observed inventory evidence.');
  assert.deepEqual(calls.equips, ['wooden_pickaxe', 'wooden_axe']);

  const air = fixture({ inventoryCount: 0 });
  const idempotent = await digBatch(air.bot, { positions: [{ x: 1, y: 64, z: 0 }] });
  assert.equal(idempotent.verified, true);
  assert.deepEqual(idempotent.skipped.map(entry => entry.reason), ['already_air']);
  assert.equal(air.calls.digs.length, 0);
}

async function testDigBatchPreflightsFixturesLoadsAndToolsBeforeDigging () {
  const fixtureBlock = fixture({ inventoryItems: [{ name: 'wooden_pickaxe', count: 1 }], targetNames: {
    '1,64,0': 'grass', '2,64,0': 'cobblestone'
  } });
  await assert.rejects(digBatch(fixtureBlock.bot, { positions: [{ x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }] }), error => {
    assert.equal(error.code, 'UNSAFE_CLEAR_TARGET');
    return true;
  });
  assert.equal(fixtureBlock.calls.digs.length, 0, 'A later player fixture must block the entire batch preflight.');

  const unloaded = fixture({ targetNames: { '1,64,0': null } });
  await assert.rejects(digBatch(unloaded.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => error.code === 'UNLOADED_BLOCK');
  assert.equal(unloaded.calls.digs.length, 0);

  const noPick = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'stone' } });
  await assert.rejects(digBatch(noPick.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => error.code === 'TOOL_REQUIRED');
  assert.equal(noPick.calls.digs.length, 0, 'Stone must not begin clearing without a real carried pickaxe.');

  const wrongTool = fixture({ inventoryItems: [{ name: 'wooden_axe', count: 1 }], targetNames: { '1,64,0': 'stone' } });
  await assert.rejects(digBatch(wrongTool.bot, { tool: 'wooden_axe', positions: [{ x: 1, y: 64, z: 0 }] }),
    error => error.code === 'UNSUITABLE_TOOL');
  assert.equal(wrongTool.calls.digs.length, 0, 'A requested tool cannot bypass stone harvest requirements.');

  const bottomFirst = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'dirt', '1,65,0': 'dirt' } });
  assert.equal((await digBatch(bottomFirst.bot, { positions: [{ x: 1, y: 64, z: 0 }, { x: 1, y: 65, z: 0 }] })).verified, true);
  assert.equal(bottomFirst.calls.digs.length, 2, 'Stable natural dirt does not fall and need not be cleared top-down.');
  const branch = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'oak_leaves', '1,65,0': 'oak_log' } });
  assert.equal((await digBatch(branch.bot, { positions: [{ x: 1, y: 64, z: 0 }] })).verified, true);
  assert.equal(branch.bot.blockAt(new Vec3(1, 65, 0)).name, 'oak_log', 'Trimming a leaf does not implicitly remove its stable overburden.');
  for (const name of ['chest', 'crafting_table', 'furnace', 'cobblestone']) {
    const support = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'dirt', '1,65,0': name } });
    await assert.rejects(digBatch(support.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => error.code === 'UNSUPPORTED_OVERBURDEN');
    assert.equal(support.calls.digs.length, 0, 'Do not undermine fixtures or player-built blocks.');
  }
}

async function testDigBatchMovesOffFloorTargetsAndRejectsHazards () {
  const floor = fixture({ inventoryCount: 0, targetNames: { '0,63,0': 'grass_block' } });
  const result = await digBatch(floor.bot, { positions: [{ x: 0, y: 63, z: 0 }] });
  assert.equal(result.verified, true);
  assert.equal(floor.calls.goto.length, 1, 'The bot must leave a floor target instead of digging beneath itself.');
  assert.notEqual(Math.floor(floor.bot.entity.position.x), 0);
  assert.deepEqual(floor.calls.digs, [new Vec3(0, 63, 0)]);

  const liquid = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'grass', '1,64,1': 'water' } });
  await assert.rejects(digBatch(liquid.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => error.code === 'LIQUID_ADJACENT');
  assert.equal(liquid.calls.digs.length, 0);

  const falling = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'grass', '1,65,0': 'sand' } });
  await assert.rejects(digBatch(falling.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => error.code === 'HAZARDOUS_ADJACENCY');
  assert.equal(falling.calls.digs.length, 0);
}

async function testOverheadClearingFindsGroundStanceWithoutSeparateWalkAction () {
  for (const height of [67, 68]) {
    const { bot, calls } = fixture({ inventoryCount: 0, targetNames: { [`10,${height},0`]: 'oak_leaves' } });
    const result = await digBatch(bot, { positions: [{ x: 10, y: height, z: 0 }] });
    assert.equal(result.verified, true);
    assert.equal(calls.digs.length, 1);
    assert.ok(calls.goto.length > 0, 'The executor must walk to reachable ground for a remote overhead target.');
    assert.equal(bot.entity.position.y, 64, 'Do not tower or stand on leaves to reach an overhead target.');
  }
}

async function testDigBatchStopsAtFirstUnverifiedOrCancelledCell () {
  let attempts = 0;
  const changed = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'grass', '2,64,0': 'grass' },
    digEffect: ({ world, target }) => {
      attempts++;
      if (attempts === 1) world.set(key(target.position), 'air');
    } });
  await assert.rejects(digBatch(changed.bot, { positions: [{ x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }] }), error => {
    assert.equal(error.code, 'DIG_NOT_VERIFIED');
    assert.equal(error.data.partial.cleared.length, 1);
    assert.deepEqual(error.data.partial.failed.position, { x: 2, y: 64, z: 0 });
    return true;
  });
  assert.equal(changed.calls.digs.length, 2);

  const targetChanged = fixture({ inventoryItems: [{ name: 'wooden_pickaxe', count: 1 }], targetNames: { '1,64,0': 'stone' },
    onEquip: ({ world }) => world.set('1,64,0', 'dirt') });
  await assert.rejects(digBatch(targetChanged.bot, { positions: [{ x: 1, y: 64, z: 0 }] }), error => {
    assert.equal(error.code, 'TARGET_CHANGED');
    assert.equal(error.data.partial.cleared.length, 0);
    return true;
  });
  assert.equal(targetChanged.calls.digs.length, 0, 'A changed-but-still-natural target must not be silently dug.');

  let releaseDig;
  const cancelled = fixture({ inventoryCount: 0, targetNames: { '1,64,0': 'grass', '2,64,0': 'grass' },
    digEffect: ({ world, target }) => new Promise(resolve => { releaseDig = () => {
      world.set(key(target.position), 'air');
      resolve();
    }; }) });
  const job = runOperation(cancelled.bot,
    guarded => digBatch(guarded, { positions: [{ x: 1, y: 64, z: 0 }, { x: 2, y: 64, z: 0 }] }),
    { timeoutMs: 20 });
  for (let attempt = 0; !releaseDig && attempt < 10; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 1));
  }
  assert.equal(typeof releaseDig, 'function');
  await assert.rejects(job, error => error.code === 'TASK_TIMEOUT');
  releaseDig();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(cancelled.calls.digs.length, 1, 'A revoked dig batch must never issue its next selected dig.');
}

const repairPosition = { x: 1, y: 64, z: 0 };
const repairInventory = () => [
  { name: 'cobblestone', count: 64 }, { name: 'stone_pickaxe', count: 1 }
];

async function ownedMistake (options = {}, positions = [repairPosition], desired = 'air') {
  const context = fixture({ inventoryItems: repairInventory(), ...options });
  const cells = positions.map(position => ({ position, block: desired }));
  const handle = beginConstructionTask(context.bot, 'repair-fixture', cells, { worldSessionId: 'local-fixture-session' });
  // Trusted deterministic mistake fixture, NOT the production action path:
  // call the lower-level executor directly to emulate the former controller
  // error. The receipt still requires actual placement and inventory proof.
  // Production executeTool always calls guardConstructionAction beforehand.
  await placeBatch(context.bot, { item: 'cobblestone', positions }, handle);
  return { ...context, handle };
}

async function testRepairOnlyRemovesActualCurrentTaskWrongPlacements () {
  const positions = [repairPosition, { x: 2, y: 64, z: 0 }];
  const example = await ownedMistake({}, positions);
  const status = constructionStatus(example.bot, example.handle);
  assert.equal(status.world_session_id, 'local-fixture-session');
  assert.equal(status.owned_placement_count, 2);
  assert.ok(status.owned_placements.every(cell => cell.repair_eligible));
  const result = await repairBatch(example.bot, { positions }, example.handle);
  assert.equal(result.mode, 'own_placement_repair');
  assert.equal(result.verified, true);
  assert.equal(result.cleared.length, 2);
  assert.ok(result.cleared.every(receipt => receipt.before === 'cobblestone' && receipt.after === 'air' &&
    receipt.desired_block === 'air' && receipt.provenance === 'verified_current_task_placement'));
  assert.equal(constructionStatus(example.bot, example.handle).owned_placement_count, 0);
  assert.equal(example.calls.digs.length, 2);
  assert.ok(example.calls.equips.includes('stone_pickaxe'));
}

async function testRepairPreflightsEveryOwnedCellAndPreservesExistingOrCorrectBlocks () {
  const mixed = await ownedMistake({ targetNames: { '2,64,0': 'cobblestone' } });
  await assert.rejects(repairBatch(mixed.bot, { positions: [repairPosition, { x: 2, y: 64, z: 0 }] }, mixed.handle),
    error => error.code === 'CONSTRUCTION_SCOPE' && error.data.partial.cleared.length === 0);
  assert.equal(mixed.calls.digs.length, 0);

  const existing = fixture({ inventoryItems: repairInventory(), targetNames: { '1,64,0': 'cobblestone' } });
  const existingHandle = beginConstructionTask(existing.bot, 'existing', [{ position: repairPosition, block: 'air' }]);
  await assert.rejects(repairBatch(existing.bot, { positions: [repairPosition] }, existingHandle), error => error.code === 'REPAIR_NOT_OWNED');
  assert.equal(existing.calls.digs.length, 0);

  const laterUnowned = fixture({ inventoryItems: repairInventory(), targetNames: { '2,64,0': 'cobblestone' } });
  const laterHandle = beginConstructionTask(laterUnowned.bot, 'partial-preflight', [
    { position: repairPosition, block: 'air' }, { position: { x: 2, y: 64, z: 0 }, block: 'air' }
  ]);
  await placeBatch(laterUnowned.bot, { item: 'cobblestone', positions: [repairPosition] }, laterHandle);
  await assert.rejects(repairBatch(laterUnowned.bot, { positions: [repairPosition, { x: 2, y: 64, z: 0 }] }, laterHandle),
    error => error.code === 'REPAIR_NOT_OWNED' && error.data.partial.cleared.length === 0);
  assert.equal(laterUnowned.calls.digs.length, 0);

  const correct = await ownedMistake({}, [repairPosition], 'cobblestone');
  assert.equal(constructionStatus(correct.bot, correct.handle).owned_placements[0].repair_eligible, false);
  await assert.rejects(repairBatch(correct.bot, { positions: [repairPosition] }, correct.handle), error => error.code === 'REPAIR_NOT_NEEDED');
  assert.equal(correct.calls.digs.length, 0);
  assert.throws(() => guardConstructionAction(correct.bot, { name: 'dig', args: { position: repairPosition } }, correct.handle),
    error => error.code === 'CONSTRUCTION_ALREADY_CORRECT');
}

async function testRepairDoesNotAcceptJsonOwnershipOrDifferentTaskWorldHandles () {
  const example = await ownedMistake();
  await assert.rejects(repairBatch(example.bot, { positions: [repairPosition], owned: true }, example.handle), error => error.code === 'INVALID_ARGUMENT');
  await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }, { task_id: 'repair-fixture', owned: true }),
    error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }), error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  const other = fixture({ inventoryItems: repairInventory() });
  await assert.rejects(repairBatch(other.bot, { positions: [repairPosition] }, example.handle), error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  example.bot.game.dimension = 'the_nether';
  await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }, example.handle), error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  assert.equal(example.calls.digs.length, 0);

  const replaced = await ownedMistake();
  beginConstructionTask(replaced.bot, 'new-task', [{ position: repairPosition, block: 'air' }]);
  await assert.rejects(repairBatch(replaced.bot, { positions: [repairPosition] }, replaced.handle), error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  assert.equal(hasConstructionTarget(beginConstructionTask(other.bot, 'lookup', [{ position: repairPosition, block: 'air' }]), repairPosition), true);
  assert.equal(replaced.calls.digs.length, 0);
}

async function testRepairInvalidatesPropertyStateAndSameNameExternalReplacements () {
  for (const change of ['property', 'state', 'revision', 'unload_update']) {
    const example = await ownedMistake();
    const before = example.bot.blockAt(new Vec3(1, 64, 0));
    if (change === 'property') example.world.set('1,64,0', { name: 'cobblestone', properties: { fixture_axis: 'x' } });
    if (change === 'state') example.world.set('1,64,0', { name: 'cobblestone', stateId: before.stateId + 1 });
    if (change === 'revision') example.bot.emit('blockUpdate', before, example.bot.blockAt(new Vec3(1, 64, 0)));
    if (change === 'unload_update') example.bot.emit('blockUpdate', before, null);
    await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }, example.handle), error => error.code === 'REPAIR_TARGET_CHANGED');
    assert.equal(example.calls.digs.length, 0);
  }
  const unloaded = await ownedMistake();
  unloaded.world.set('1,64,0', null);
  await assert.rejects(repairBatch(unloaded.bot, { positions: [repairPosition] }, unloaded.handle), error => error.code === 'UNLOADED_BLOCK');
  assert.equal(unloaded.calls.digs.length, 0);
  unloaded.world.set('1,64,0', 'cobblestone');
  await assert.rejects(repairBatch(unloaded.bot, { positions: [repairPosition] }, unloaded.handle), error => error.code === 'REPAIR_NOT_OWNED');
}

async function testRepairRequiresToolAndPreservesHazardsFixturesAndPlayerFooting () {
  const noTool = await ownedMistake();
  noTool.inventory.find(stack => stack.name === 'stone_pickaxe').count = 0;
  await assert.rejects(repairBatch(noTool.bot, { positions: [repairPosition] }, noTool.handle), error => error.code === 'TOOL_REQUIRED');
  assert.equal(noTool.calls.digs.length, 0);
  for (const [neighbor, name, code] of [
    ['2,64,0', 'water', 'LIQUID_ADJACENT'], ['1,65,0', 'sand', 'HAZARDOUS_ADJACENCY'],
    ['2,64,0', null, 'UNLOADED_NEIGHBOR'], ['1,65,0', 'chest', 'UNSUPPORTED_OVERBURDEN']
  ]) {
    const example = await ownedMistake();
    example.world.set(neighbor, name);
    await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }, example.handle), error => error.code === code);
    assert.equal(example.calls.digs.length, 0);
  }
  const player = await ownedMistake();
  player.bot.entities = { another: { type: 'player', position: new Vec3(1, 65, 0) } };
  await assert.rejects(repairBatch(player.bot, { positions: [repairPosition] }, player.handle), error => error.code === 'UNSAFE_DIG');
  assert.equal(player.calls.digs.length, 0);
  const ownedFixture = fixture({ inventoryItems: [{ name: 'chest', count: 1 }, { name: 'stone_pickaxe', count: 1 }] });
  const fixtureHandle = beginConstructionTask(ownedFixture.bot, 'owned-fixture', [{ position: repairPosition, block: 'air' }]);
  await placeBatch(ownedFixture.bot, { item: 'chest', positions: [repairPosition] }, fixtureHandle);
  await assert.rejects(repairBatch(ownedFixture.bot, { positions: [repairPosition] }, fixtureHandle), error => error.code === 'UNSAFE_REPAIR_TARGET');
  assert.equal(ownedFixture.calls.digs.length, 0);
  const roof = await ownedMistake();
  roof.world.set('1,65,0', 'cobblestone');
  assert.equal((await repairBatch(roof.bot, { positions: [repairPosition] }, roof.handle)).verified, true,
    'A stable non-falling roof must not make a stray interior block irreparable.');
}

async function testRepairKeepsPartialProofAndNeverContinuesAfterTargetChange () {
  const positions = [repairPosition, { x: 2, y: 64, z: 0 }];
  const changed = await ownedMistake({ digEffect: ({ bot, world, target }) => {
    world.set(key(target.position), 'air');
    world.set('2,64,0', 'dirt');
    bot.emit('blockUpdate', null, bot.blockAt(new Vec3(2, 64, 0)));
  } }, positions);
  await assert.rejects(repairBatch(changed.bot, { positions }, changed.handle), error =>
    error.code === 'REPAIR_TARGET_CHANGED' && error.data.partial.cleared.length === 1 && error.data.partial.cleared[0].verified);
  assert.equal(changed.calls.digs.length, 1);
  assert.equal(changed.world.get('2,64,0'), 'dirt');

  const notGone = await ownedMistake({ digEffect: () => {} });
  await assert.rejects(repairBatch(notGone.bot, { positions: [repairPosition] }, notGone.handle), error =>
    error.code === 'REPAIR_NOT_VERIFIED' && error.data.partial.cleared.length === 0);
  assert.equal(notGone.calls.digs.length, 1);
}

async function testRepairTaskEndAndOperationTimeoutRevokeLaterMutation () {
  let handle;
  let ended = false;
  const expiry = await ownedMistake({ onEquip: ({ bot, stack }) => {
    if (stack.name === 'stone_pickaxe') {
      ended = endConstructionTask(bot, handle);
    }
  } });
  handle = expiry.handle;
  await assert.rejects(repairBatch(expiry.bot, { positions: [repairPosition] }, handle), error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  assert.equal(ended, true);
  assert.equal(expiry.calls.digs.length, 0);

  let release;
  let issued;
  const firstDig = new Promise(resolve => { issued = resolve; });
  const heldDig = new Promise(resolve => { release = resolve; });
  const positions = [repairPosition, { x: 2, y: 64, z: 0 }];
  const cancelled = await ownedMistake({ digEffect: async ({ world, target }) => {
    issued();
    await heldDig;
    world.set(key(target.position), 'air');
  } }, positions);
  const operation = runOperation(cancelled.bot, guarded => repairBatch(guarded, { positions }, cancelled.handle), { timeoutMs: 25 });
  await firstDig;
  await assert.rejects(operation, error => error.code === 'TASK_TIMEOUT');
  release();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(cancelled.calls.digs.length, 1);
  assert.equal(cancelled.world.get('2,64,0'), 'cobblestone');

  let placementHandle;
  const revokedPlacement = fixture({ inventoryItems: repairInventory(), onEquip: ({ bot }) => {
    endConstructionTask(bot, placementHandle);
  } });
  placementHandle = beginConstructionTask(revokedPlacement.bot, 'placement-revocation', [{ position: repairPosition, block: 'cobblestone' }]);
  await assert.rejects(placeBatch(revokedPlacement.bot, { item: 'cobblestone', positions: [repairPosition] }, placementHandle),
    error => error.code === 'CONSTRUCTION_TASK_UNAVAILABLE');
  assert.equal(revokedPlacement.calls.places.length, 0, 'Ending a task during equip must prevent the following placement.');
}

async function testRepairDoesNotClaimUnverifiedPlacements () {
  const example = fixture({ inventoryItems: repairInventory(), placeEffect: ({ world, target }) => {
    world.set(key(target), 'cobblestone'); // No matching inventory consumption.
  } });
  const handle = beginConstructionTask(example.bot, 'unverified-placement', [{ position: repairPosition, block: 'air' }]);
  await assert.rejects(placeBatch(example.bot, { item: 'cobblestone', positions: [repairPosition] }, handle),
    error => error.code === 'PLACE_NOT_VERIFIED');
  assert.equal(constructionStatus(example.bot, handle).owned_placement_count, 0);
  await assert.rejects(repairBatch(example.bot, { positions: [repairPosition] }, handle), error => error.code === 'REPAIR_NOT_OWNED');
  assert.equal(example.calls.digs.length, 0);
}

async function testConstructionGuardRejectsWholeContradictoryBatchesAndMapsPlantItems () {
  const example = fixture({ inventoryItems: repairInventory() });
  const handle = beginConstructionTask(example.bot, 'guard', [
    { position: repairPosition, block: 'cobblestone' },
    { position: { x: 2, y: 64, z: 0 }, block: 'air' },
    { position: { x: 3, y: 64, z: 0 }, block: 'wheat' }
  ]);
  assert.throws(() => guardConstructionAction(example.bot, { name: 'place_batch', args: { item: 'cobblestone', positions: [repairPosition, { x: 2, y: 64, z: 0 }] } }, handle),
    error => error.code === 'CONSTRUCTION_GOAL_CONTRADICTION');
  assert.equal(example.calls.places.length, 0);
  guardConstructionAction(example.bot, { name: 'place', args: { item: 'wheat_seeds', position: { x: 3, y: 64, z: 0 } } }, handle);
  guardConstructionAction(example.bot, { name: 'place', args: { item: 'crafting_table', position: { x: 10, y: 64, z: 0 } } }, handle);
  assert.throws(() => guardConstructionAction(example.bot, { name: 'place_batch', args: { item: 'cobblestone', positions: [{ x: 10, y: 64, z: 0 }] } }, handle),
    error => error.code === 'CONSTRUCTION_SCOPE');
  example.world.set('2,64,0', 'cobblestone');
  assert.throws(() => guardConstructionAction(example.bot, { name: 'dig_batch', args: { positions: [{ x: 2, y: 64, z: 0 }] } }, handle),
    error => error.code === 'REPAIR_REQUIRED');
  assert.equal(example.calls.digs.length, 0);
}

async function testConstructionStatusIsBoundedAndPrioritizesLateMistakes () {
  const positions = Array.from({ length: 65 }, (_, index) => ({ x: 1 + index % 12, y: 64, z: Math.floor(index / 12) }));
  const example = fixture({ inventoryItems: repairInventory() });
  const handle = beginConstructionTask(example.bot, 'bounded-status', positions.map((position, index) => ({
    position, block: index === 64 ? 'air' : 'cobblestone'
  })));
  await placeBatch(example.bot, { item: 'cobblestone', positions: positions.slice(0, 64) }, handle);
  example.inventory.find(stack => stack.name === 'cobblestone').count = 1;
  await placeBatch(example.bot, { item: 'cobblestone', positions: positions.slice(64) }, handle);
  const status = constructionStatus(example.bot, handle);
  assert.equal(status.complete, false);
  assert.equal(status.omitted_count, 1);
  assert.equal(status.owned_placements.length, 64);
  assert.equal(status.repairable_count, 1);
  assert.equal(status.owned_placements[0].repair_eligible, true);
  assert.deepEqual(status.repairable_owned_mismatches[0].position, positions[64]);
}

async function runConstructionTests () {
  await testPlacesEveryInitiallyAirCellWithAllMatchingStacks();
  await testPlacementGroundStanceSurvivesInvalidCloserCandidateCap();
  await testAlreadyDesiredAndOccupiedCellsNeverBecomeNewWork();
  await testFullScopeAndSupplyArePreflightedBeforeMutating();
  await testRoofCanBePlacedFromAnInsideFloorStance();
  await testLastFloorCellCanUseTopOfAdjacentPlacedFloorWithoutBodyCollision();
  await testWalksOnlyToFreshSafeStanceAndStopsOnFirstFailure();
  await testRequiresBothBlockAndExactInventoryEvidenceAndPreservesCancellation();
  await testGuardedBatchCannotPlaceAfterItsOperationExpires();
  await testRejectsDuplicateAndRestrictedItemsBeforeActions();
  await testDigBatchClearsOnlySelectedNaturalBlocksAndReportsDrops();
  await testDigBatchPreflightsFixturesLoadsAndToolsBeforeDigging();
  await testDigBatchMovesOffFloorTargetsAndRejectsHazards();
  await testOverheadClearingFindsGroundStanceWithoutSeparateWalkAction();
  await testDigBatchStopsAtFirstUnverifiedOrCancelledCell();
  await testRepairOnlyRemovesActualCurrentTaskWrongPlacements();
  await testRepairPreflightsEveryOwnedCellAndPreservesExistingOrCorrectBlocks();
  await testRepairDoesNotAcceptJsonOwnershipOrDifferentTaskWorldHandles();
  await testRepairInvalidatesPropertyStateAndSameNameExternalReplacements();
  await testRepairRequiresToolAndPreservesHazardsFixturesAndPlayerFooting();
  await testRepairKeepsPartialProofAndNeverContinuesAfterTargetChange();
  await testRepairTaskEndAndOperationTimeoutRevokeLaterMutation();
  await testConstructionGuardRejectsWholeContradictoryBatchesAndMapsPlantItems();
  await testConstructionStatusIsBoundedAndPrioritizesLateMistakes();
  await testRepairDoesNotClaimUnverifiedPlacements();
  console.log('✓ guarded construction placement, clearing and current-task repair tests passed');
}

if (require.main === module) runConstructionTests().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { runConstructionTests };
