const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { executeTool } = require('../bot/tools');

async function runToolTests () {
  let held = 0;
  let crafts = 0;
  let windows = 0;
  const recipe = { result: { count: 1 }, requiresTable: true, delta: [] };
  const table = { name: 'crafting_table', position: new Vec3(2, 64, 0) };
  const bot = {
    version: '1.20.1', entity: { position: new Vec3(0, 64, 0) },
    inventory: { items: () => held ? [{ name: 'wooden_sword', count: held }] : [] },
    pathfinder: { goto: async () => {}, setGoal () {} },
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
      close () { closes++; }
    }) };
  const transferred = await executeTool(transferBot, { name: 'deposit', args: { item: 'cobblestone', count: 2, position: target } });
  assert.equal(transferred.verified, true, transferred.message);
  assert.equal(carried, 1);
  assert.equal(closes, 2, 'Both windows close once despite nested finally cleanup.');

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
