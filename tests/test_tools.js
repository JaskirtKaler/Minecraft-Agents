const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { executeTool } = require('../bot/tools');
const { beginConstructionTask, endConstructionTask } = require('../bot/construction');

function goalAnchor (goal) {
  if (Number.isFinite(goal?.x) && Number.isFinite(goal?.y) && Number.isFinite(goal?.z)) return goal;
  for (const nested of goal?.goals || []) {
    const anchor = goalAnchor(nested);
    if (anchor) return anchor;
  }
  return goal?.goal ? goalAnchor(goal.goal) : null;
}

function arriveAtGoal (bot, goal) {
  const anchor = goalAnchor(goal) || bot.entity.position;
  for (const dy of [0, -1, 1, 2, -2]) {
    for (let radius = 0; radius <= 4; radius++) {
      for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
          if (Math.max(Math.abs(dx), Math.abs(dz)) !== radius) continue;
          const node = new Vec3(Math.floor(anchor.x) + dx, Math.floor(anchor.y) + dy, Math.floor(anchor.z) + dz);
          if (goal.isEnd(node)) {
            bot.entity.position = node;
            return;
          }
        }
      }
    }
  }
  throw new Error('Test route could not find a goal-reaching node.');
}

async function runToolTests () {
  let held = 0;
  let crafts = 0;
  let windows = 0;
  const recipe = { result: { count: 1 }, requiresTable: true, delta: [] };
  const table = { name: 'crafting_table', position: new Vec3(2, 64, 0) };
  const bot = {
    version: '1.20.1', entity: { position: new Vec3(0, 64, 0) },
    inventory: { items: () => held ? [{ name: 'wooden_sword', count: held }] : [] },
    pathfinder: { goto: async goal => { arriveAtGoal(bot, goal); }, setGoal () {} },
    blockAt: () => table,
    recipesFor: () => [recipe], recipesAll: () => [recipe],
    waitForTicks: async () => {},
    craft: async (_recipe, rounds) => { assert.equal(rounds, 1); crafts++; held++; },
    openBlock: async () => { windows++; return {
      inventoryStart: 10, inventoryEnd: 46,
      slots: [...Array(10).fill(null), ...(held ? [{ name: 'wooden_sword', count: held }] : [null]), ...Array(35).fill(null)],
      close () {}
    }; }
  };
  const result = await executeTool(bot, { name: 'craft', args: { item: 'wooden_sword', count: 2, table: table.position } });
  assert.equal(result.verified, true, result.message);
  assert.equal(crafts, 2);
  assert.equal(windows, 3, 'Baseline and each recipe round use a fresh crafting inventory snapshot.');
  assert.equal(result.data.produced, 2);

  const clickEvents = [];
  const originalClick = async () => { clickEvents.push('click'); };
  const pacedBot = { ...bot, clickWindow: originalClick, supportFeature: feature => feature === 'stateIdUsed',
    waitForTicks: async ticks => { clickEvents.push('ticks:' + ticks); },
    craft: async () => { await pacedBot.clickWindow(0, 0, 0); await pacedBot.clickWindow(10, 0, 0); held++; } };
  const pacedResult = await executeTool(pacedBot, { name: 'craft', args: { item: 'wooden_sword', count: 1, table: table.position } });
  assert.equal(pacedResult.verified, true, pacedResult.message);
  assert.deepEqual(clickEvents.slice(clickEvents.indexOf('click'), clickEvents.indexOf('click') + 4),
    ['click', 'ticks:1', 'click', 'ticks:1'], 'Craft clicks wait for server ticks instead of flooding optimistic updates.');
  assert.equal(pacedBot.clickWindow, originalClick, 'Scoped pacing must restore the original API.');

  let releaseTick;
  let cancelledClicks = 0;
  const originalCancelledClick = async () => { cancelledClicks++; };
  const cancelledBot = { ...bot, clickWindow: originalCancelledClick, supportFeature: feature => feature === 'stateIdUsed',
    waitForTicks: async ticks => { if (ticks === 1) await new Promise(resolve => { releaseTick = resolve; }); },
    craft: async () => { await cancelledBot.clickWindow(0, 0, 0); await cancelledBot.clickWindow(10, 0, 0); held++; } };
  const cancelledCraft = await executeTool(cancelledBot,
    { name: 'craft', args: { item: 'wooden_sword', count: 1, table: table.position } }, { timeoutMs: 20 });
  assert.equal(cancelledCraft.data.error_code, 'TASK_TIMEOUT');
  assert.equal(cancelledClicks, 1);
  releaseTick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancelledClicks, 1, 'Expired craft must not issue another inventory click.');
  assert.equal(cancelledBot.clickWindow, originalCancelledClick, 'Cancelled craft restores its scoped wrapper.');

  crafts = 0;
  bot.craft = async () => { crafts++; }; // Craft returns, but no actual item appears.
  const failed = await executeTool(bot, { name: 'craft', args: { item: 'wooden_sword', count: 2, table: table.position } });
  assert.equal(failed.verified, false);
  assert.equal(failed.data.error_code, 'CRAFT_NOT_VERIFIED');
  assert.equal(crafts, 1, 'Do not begin the second recipe when the first is uncertain.');
  const unsupported = await executeTool(bot, { name: 'run_shell', args: {} });
  assert.equal(unsupported.success, false);

  let plankCount = 1;
  let targetName = 'air';
  const target = new Vec3(2, 64, 0);
  const placementBot = {
    version: '1.20.1', entity: { position: new Vec3(0, 64, 0) },
    inventory: { items: () => plankCount ? [{ name: 'oak_planks', count: plankCount }] : [] },
    blockAt: p => ({ name: p.equals(target) ? targetName : 'stone', position: p, boundingBox: p.equals(target) ? 'empty' : 'block', diggable: true }),
    equip: async () => {},
    placeBlock: async () => { targetName = 'oak_planks'; plankCount--; },
    waitForTicks: async ticks => { assert.equal(ticks, 2); }
  };
  assert.equal((await executeTool(placementBot, { name: 'place', args: { item: 'oak_planks', position: target } })).verified, true);
  assert.equal((await executeTool(placementBot, { name: 'place', args: { item: 'oak_planks', position: target } })).data.error_code, 'OCCUPIED_BLOCK');

  const noAuthority = await executeTool(placementBot, { name: 'repair_batch',
    args: { positions: [target], owned: true, task_id: 'forged' } });
  assert.equal(noAuthority.data.error_code, 'CONSTRUCTION_TASK_UNAVAILABLE');
  let blockedPlacements = 0;
  targetName = 'air';
  plankCount = 2;
  placementBot.placeBlock = async () => { blockedPlacements++; };
  const constructionContext = beginConstructionTask(placementBot, 'guarded', [
    { position: target, block: 'air' }, { position: { x: 1, y: 64, z: 0 }, block: 'oak_planks' }]);
  const conflict = await executeTool(placementBot, { name: 'place_batch', args: { item: 'oak_planks',
    positions: [{ x: 1, y: 64, z: 0 }, target] } }, { constructionContext });
  assert.equal(conflict.data.error_code, 'CONSTRUCTION_GOAL_CONTRADICTION');
  assert.equal(blockedPlacements, 0, 'Whole-batch conformance precedes the first placement.');
  endConstructionTask(placementBot, constructionContext);

  // An out-of-geometry prerequisite station uses the ordinary single-place
  // primitive, but must not survive revocation while equip is awaiting.
  const revokedContext = beginConstructionTask(placementBot, 'revoked', [
    { position: { x: 8, y: 64, z: 0 }, block: 'cobblestone' }]);
  placementBot.equip = async () => { endConstructionTask(placementBot, revokedContext); };
  const revokedPlacement = await executeTool(placementBot,
    { name: 'place', args: { item: 'oak_planks', position: target } }, { constructionContext: revokedContext });
  assert.equal(revokedPlacement.data.error_code, 'CONSTRUCTION_TASK_UNAVAILABLE');
  assert.equal(blockedPlacements, 0, 'A revoked contract must be rechecked after equip, before actual placement.');
  placementBot.equip = async () => {};

  const underfoot = new Vec3(0, 63, 0);
  let digs = 0;
  const digBot = { ...placementBot, pathfinder: { goto: async () => {}, setGoal () {} },
    canDigBlock: () => true, dig: async () => { digs++; } };
  const unsafe = await executeTool(digBot, { name: 'dig', args: { position: underfoot } });
  assert.equal(unsafe.data.error_code, 'UNSAFE_DIG');
  assert.equal(digs, 0);

  let cleared = false;
  const overhead = new Vec3(0, 66, 0);
  const clearanceBot = { ...digBot,
    pathfinder: { goto: async () => { throw new Error('Already reachable; walking is not required.'); }, setGoal () {} },
    blockAt: p => ({ name: cleared ? 'air' : 'stone', position: p, diggable: !cleared }),
    dig: async () => { cleared = true; }
  };
  const clearance = await executeTool(clearanceBot, { name: 'dig', args: { position: overhead } });
  assert.equal(clearance.verified, true, clearance.message);

  let stored = 0;
  let carried = 3;
  let closes = 0;
  const chest = { name: 'chest', position: target };
  const transferBot = { ...bot, inventory: { items: () => [{ name: 'cobblestone', count: carried }] },
    blockAt: () => chest,
    openContainer: async () => ({
      inventoryStart: 27, inventoryEnd: 63,
      slots: [...Array(27).fill(null), { name: 'cobblestone', count: carried }, ...Array(35).fill(null)],
      containerItems: () => stored ? [{ name: 'cobblestone', count: stored }] : [],
      deposit: async (_id, _meta, quantity) => { stored += quantity; carried -= quantity; },
      withdraw: async (_id, _meta, quantity) => { stored -= quantity; carried += quantity; },
      close () { closes++; }
    }) };
  const transferred = await executeTool(transferBot, { name: 'deposit', args: { item: 'cobblestone', count: 2, position: target } });
  assert.equal(transferred.verified, true, transferred.message);
  assert.equal(carried, 1);
  assert.equal(closes, 2, 'Both windows close once despite nested finally cleanup.');

  stored = 6;
  const boundedWithdrawal = { name: 'withdraw', args: { item: 'cobblestone', count: 3, position: target },
    container_constraint: { item: 'cobblestone', position: target, minimum: 4 } };
  const blocked = await executeTool(transferBot, boundedWithdrawal);
  assert.equal(blocked.data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(stored, 6, 'An overshooting transfer must not mutate the chest.');
  boundedWithdrawal.args.count = 2;
  assert.equal((await executeTool(transferBot, boundedWithdrawal)).verified, true);
  assert.equal(stored, 4);

  // A player can change the chest after the controller observed it. The Node
  // check uses the window count, not that earlier snapshot.
  stored = 5;
  assert.equal((await executeTool(transferBot, boundedWithdrawal)).data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(stored, 5);

  // Convenience skills must receive root controller metadata through options,
  // rather than trusting/reading model-supplied nested task arguments.
  stored = 1;
  carried = 3;
  let skillNavigation = 0;
  const convenienceBot = { ...transferBot,
    inventory: { items: () => [{ name: 'cobblestone', type: 1, count: carried }] },
    findBlocks: () => [target],
    pathfinder: { movements: { canDig: false, allow1by1towers: false },
      getPathTo: () => ({ status: 'success', cost: 1 }),
      goto: async goal => { skillNavigation++; arriveAtGoal(convenienceBot, goal); }, setGoal () {} },
    openChest: transferBot.openContainer
  };
  const boundedConvenience = { name: 'deposit_item',
    args: { item: 'cobblestone', count: 2, container_constraint: { maximum: 100 } },
    container_constraint: { item: 'cobblestone', position: target, maximum: 2 } };
  const convenienceRejected = await executeTool(convenienceBot, boundedConvenience);
  assert.equal(convenienceRejected.data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(stored, 1, 'The root constraint must survive the executeTool → executeTask handoff.');
  boundedConvenience.args.count = 1;
  const convenienceAccepted = await executeTool(convenienceBot, boundedConvenience);
  assert.equal(convenienceAccepted.verified, true, convenienceAccepted.message);
  assert.equal(stored, 2);
  assert(skillNavigation > 0);
  const beforeInvalidNavigation = skillNavigation;
  const invalidConvenience = await executeTool(convenienceBot, { ...boundedConvenience,
    container_constraint: { item: 'cobblestone', position: target, maximum: '2' } });
  assert.equal(invalidConvenience.data.error_code, 'INVALID_CONSTRAINT');
  assert.equal(skillNavigation, beforeInvalidNavigation, 'Malformed root metadata fails before navigation.');

  // The ordinary explicit generic API remains available and verified.
  const explicitDeposit = await executeTool(transferBot,
    { name: 'deposit', args: { item: 'cobblestone', count: 1, position: target } });
  assert.equal(explicitDeposit.verified, true, explicitDeposit.message);
  assert.equal(stored, 3);

  carried = 3;
  // Simulate a conflicting/stale player inventory despite apparent chest gain.
  const honestOpen = transferBot.openContainer;
  transferBot.openContainer = async () => {
    const window = await honestOpen();
    window.deposit = async (_id, _meta, quantity) => { stored += quantity; };
    return window;
  };
  const uncertain = await executeTool(transferBot, { name: 'deposit', args: { item: 'cobblestone', count: 2, position: target } });
  assert.equal(uncertain.verified, false);
  assert.equal(uncertain.data.error_code, 'TRANSFER_NOT_VERIFIED');
  console.log('✓ generic crafting, placement, reachable digging, dig-safety and fresh transfer verification tests passed');
}
module.exports = { runToolTests };
