const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const { executeTask } = require('../bot/skills');
const { describeSubject } = require('../bot/knowledge');
const { executeCodeSnippet } = require('../bot/sandbox');
const { cancelOperation } = require('../bot/operations');

function fixture ({ reachable = true, silk = false, tool = true, chest = true, full = false, buriedCount = 0, badDeposit = false, navigationDelay = 0 } = {}) {
  const targets = new Map();
  const position = (x, y, z) => new Vec3(x, y, z);
  const block = (name, pos) => ({
    name, position: pos, boundingBox: name === 'air' ? 'empty' : 'block',
    canHarvest: type => !!data.blocksByName[name]?.harvestTools?.[type]
  });
  const add = (name, pos) => targets.set(pos.toString(), block(name, pos));
  add('stone', position(0, 63, 0)); // Closest stone is BELOW the bot.
  add('coal_ore', position(1, 64, 0)); // Wrong drop, even though closer.
  add('stone', position(8, 64, 0));
  add('stone', position(9, 64, 0));
  for (let i = 0; i < buriedCount; i++) add('stone', position(i % 5, 60 + Math.floor(i / 25), Math.floor(i / 5) % 5));
  if (chest) add('chest', position(4, 64, 4));
  const inventory = [{ name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: 2 }];
  if (tool) inventory.push({
    name: 'wooden_pickaxe', type: data.itemsByName.wooden_pickaxe.id, count: 1,
    enchants: silk ? [{ name: 'silk_touch', lvl: 1 }] : []
  });
  let chestCount = 7;
  const calls = { digs: [], goals: [], equipped: [], deposits: [], chats: [], closes: 0, stops: 0 };
  const bot = {
    version: '1.20.1', entity: { position: position(0, 64, 0) }, entities: {},
    inventory: { items: () => inventory.filter(item => item.count > 0) },
    pathfinder: {
      movements: { canDig: false, allow1by1towers: false },
      getPathTo: (_moves, goal) => ({ status: reachable ? 'success' : 'noPath', cost: Math.abs(goal.x) + Math.abs(goal.z) }),
      async goto (goal) {
        calls.goals.push(goal);
        if (navigationDelay) await new Promise(resolve => setTimeout(resolve, navigationDelay));
        if (goal.constructor.name === 'GoalBlock') bot.entity.position = position(goal.x, goal.y, goal.z);
      },
      setGoal: () => { calls.stops++; }
    },
    blockAt: pos => targets.get(pos.toString()) || block(pos.y < 64 ? 'stone' : 'air', pos),
    findBlocks: ({ matching, count, useExtraInfo }) => [...targets.values()].filter(matching)
      .filter(block => typeof useExtraInfo !== 'function' || useExtraInfo(block))
      .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))
      .slice(0, count).map(block => block.position),
    canDigBlock: () => true,
    async equip (item) { calls.equipped.push(item.name); },
    async dig (target) {
      calls.digs.push(target.position);
      targets.delete(target.position.toString());
      inventory[0].count++;
    },
    async waitForTicks () {},
    chat: text => calls.chats.push(text),
    async openChest () {
      return {
        slots: Array.from({ length: 27 }, () => full ? { name: 'dirt', count: 64 } : null),
        inventoryStart: 27,
        containerItems: () => [{ name: 'cobblestone', count: chestCount }],
        async deposit (type, metadata, count) {
          calls.deposits.push({ type, metadata, count });
          inventory[0].count -= count;
          if (!badDeposit) chestCount += count;
        },
        close: () => { calls.closes++; }
      };
    }
  };
  return { bot, calls, inventory };
}

const task = name => ({ name, args: { item: 'cobblestone', count: 2, max_distance: 48 } });

async function runResourceTests () {
  const knowledge = describeSubject({ version: '1.20.1' }, 'cobble stone');
  assert(knowledge.sources.includes('stone'));
  assert(!knowledge.sources.includes('coal_ore'));
  assert(describeSubject({ version: '1.20.1' }, 'stone').dropCandidates.some(drop => drop.item === 'cobblestone' && drop.noSilkTouch));
  assert(describeSubject({ version: '1.20.1' }, 'wheat').dropCandidates.some(drop => drop.blockAge === 7));
  assert.equal(describeSubject({ version: '1.20.1' }, 'made_up_block').found, false);
  assert.deepEqual(describeSubject({ version: '1.20.1' }, 'stone_pickaxe').recipes[0].ingredients, { cobblestone: 3, stick: 2 });

  const safe = fixture({ buriedCount: 100 });
  const mined = await executeTask(safe.bot, task('mine_resource'));
  assert.equal(mined.verified, true, mined.message);
  assert.equal(mined.data.after - mined.data.before, 2);
  assert.deepEqual(safe.calls.digs.map(p => p.x), [8, 9]); // Mountain, not underfoot or coal.
  assert(safe.calls.equipped.every(name => name === 'wooden_pickaxe'));
  const changingRoute = fixture();
  const originalGoto = changingRoute.bot.pathfinder.goto;
  let rejected = false;
  changingRoute.bot.pathfinder.goto = async goal => {
    if (!rejected) { rejected = true; throw new Error('route changed'); }
    return originalGoto(goal);
  };
  const reroute = await executeTask(changingRoute.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
  assert.equal(reroute.verified, true, reroute.message);
  assert.equal(changingRoute.calls.digs[0].x, 9, 'Re-observe and try a different exposed target.');

  for (const settings of [{ reachable: false }, { tool: false }, { silk: true }]) {
    const mock = fixture(settings);
    const result = await executeTask(mock.bot, task('mine_resource'));
    assert.equal(result.success, false);
    assert.equal(mock.calls.digs.length, 0);
  }
  const noChest = fixture({ chest: false });
  const missing = await executeTask(noChest.bot, task('mine_and_deposit'));
  assert.equal(missing.success, false);
  assert.equal(missing.data.error_code, 'CHEST_NOT_FOUND');
  assert.equal(noChest.calls.digs.length, 0, 'Do not mine without an accessible destination.');
  const fullChest = fixture({ full: true });
  const full = await executeTask(fullChest.bot, task('mine_and_deposit'));
  assert.equal(full.success, false);
  assert.equal(full.data.error_code, 'CHEST_FULL');
  assert.equal(fullChest.calls.digs.length, 0, 'Do not mine for a full chest.');

  const delivery = fixture();
  const deposited = await executeTask(delivery.bot, task('mine_and_deposit'));
  assert.equal(deposited.verified, true, deposited.message);
  assert.equal(deposited.data.deposit.chest_after - deposited.data.deposit.chest_before, 2);
  assert.equal(deposited.data.deposit.inventory_before - deposited.data.deposit.inventory_after, 2);
  assert.deepEqual(delivery.calls.deposits, [{ type: data.itemsByName.cobblestone.id, metadata: null, count: 2 }]);
  assert(delivery.calls.closes > 0);

  // Reproduce the live run: twelve held, asked to GET ten, no need to mine more.
  const alreadyHeld = fixture({ tool: false });
  alreadyHeld.inventory[0].count = 12;
  const fromInventory = await executeTask(alreadyHeld.bot, {
    name: 'mine_and_deposit', args: { item: 'cobblestone', count: 10, collection_mode: 'ensure_inventory' }
  });
  assert.equal(fromInventory.verified, true, fromInventory.message);
  assert.equal(alreadyHeld.calls.digs.length, 0);
  assert.equal(alreadyHeld.inventory[0].count, 2);
  assert.equal(fromInventory.data.mine.observed_mined, 0);
  assert.equal(fromInventory.data.mine.target_count, 10);
  assert.equal(fromInventory.data.deposit.chest_after - fromInventory.data.deposit.chest_before, 10);
  assert.deepEqual(alreadyHeld.calls.deposits, [{ type: data.itemsByName.cobblestone.id, metadata: null, count: 10 }]);

  const topUp = fixture();
  const toppedUp = await executeTask(topUp.bot, {
    name: 'mine_resource', args: { item: 'cobblestone', count: 3, collection_mode: 'ensure_inventory' }
  });
  assert.equal(toppedUp.verified, true, toppedUp.message);
  assert.equal(topUp.calls.digs.length, 1);
  assert.equal(topUp.inventory[0].count, 3);

  const invalidMode = fixture();
  const invalid = await executeTask(invalidMode.bot, {
    name: 'mine_and_deposit', args: { item: 'cobblestone', count: 2, collection_mode: 'anything' }
  });
  assert.equal(invalid.success, false);
  assert.equal(invalidMode.calls.digs.length, 0);
  assert.equal(invalidMode.calls.deposits.length, 0);

  const invalidPlan = fixture();
  const rejectedPlan = await executeTask(invalidPlan.bot, { name: 'execute_plan', args: { steps: [
    { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } },
    { name: 'mine_resource', args: { item: 'diamond', count: 1 } }
  ] } });
  assert.equal(rejectedPlan.success, false);
  assert.equal(invalidPlan.calls.digs.length, 0, 'Validate every step before any physical action.');
  assert.equal(invalidPlan.calls.closes, 0);

  const partialPlan = fixture();
  const partial = await executeTask(partialPlan.bot, { name: 'execute_plan', args: { steps: [
    { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } },
    { name: 'give_item', args: { item: 'cobblestone', count: 1, recipient: 'InvisiblePlayer' } }
  ] } });
  assert.equal(partial.success, false);
  assert.equal(partial.data.completed_steps.length, 1);
  assert.equal(partial.data.failed_step, 2);

  const closesOnce = fixture();
  await executeTask(closesOnce.bot, task('deposit_item'));
  assert.equal(closesOnce.calls.closes, 3, 'Preflight, deposit and verifier must each close once, not again in cleanup.');

  const stale = fixture();
  let openNumber = 0;
  stale.bot.openChest = async () => {
    const thisOpen = ++openNumber;
    const source = thisOpen < 3 ? 2 : 0;
    return {
      inventoryStart: 27, inventoryEnd: 63,
      slots: [...Array(27).fill(null), { name: 'cobblestone', count: source }, ...Array(35).fill(null)],
      containerItems: () => thisOpen < 3 ? [] : [{ name: 'cobblestone', count: 2 }],
      async deposit () {}, // bot.inventory intentionally remains stale until close.
      close () { if (thisOpen === 3) stale.inventory[0].count = 0; }
    };
  };
  const freshReadback = await executeTask(stale.bot, task('deposit_item'));
  assert.equal(freshReadback.verified, true, freshReadback.message);
  assert.equal(freshReadback.data.inventory_after, 0, 'Use the reopened window, not stale bot.inventory.');
  assert.equal(freshReadback.data.verification_source, 'reopened_container');

  const dropped = fixture();
  const dropPosition = new Vec3(8, 64, 0);
  const oldBlockAt = dropped.bot.blockAt;
  dropped.bot.dig = async target => {
    dropped.calls.digs.push(target.position);
    dropped.bot.blockAt = pos => pos.equals(dropPosition) ? { name: 'air', position: pos, boundingBox: 'empty' } : oldBlockAt(pos);
    dropped.bot.entities = { drop: {
      position: dropPosition.offset(0.5, 0.1, 0.5), getDroppedItem: () => ({ name: 'cobblestone' })
    } };
  };
  const oldGoto = dropped.bot.pathfinder.goto;
  dropped.bot.pathfinder.goto = async goal => {
    await oldGoto(goal);
    if (goal.x === 8 && goal.y === 64 && goal.z === 0) dropped.inventory[0].count++;
  };
  const recovered = await executeTask(dropped.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
  assert.equal(recovered.verified, true, recovered.message);
  assert.equal(dropped.calls.digs.length, 1, 'Recover the existing drop instead of mining an extra block.');

  const lost = fixture();
  lost.bot.dig = async target => {
    lost.calls.digs.push(target.position);
    lost.bot.entities = { drop: {
      position: target.position.offset(0.5, 0.1, 0.5), getDroppedItem: () => ({ name: 'cobblestone' })
    } };
  };
  const lostGoto = lost.bot.pathfinder.goto;
  lost.bot.pathfinder.goto = async goal => {
    if (goal.x === 8 && goal.y === 64 && goal.z === 0) {
      const error = new Error('Pickup route stopped making progress');
      error.code = 'NO_NAVIGATION_PROGRESS';
      throw error;
    }
    return lostGoto(goal);
  };
  const lostResult = await executeTask(lost.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 2 } });
  assert.equal(lostResult.data.error_code, 'PICKUP_NOT_VERIFIED');
  assert.equal(lostResult.status, 'unknown');
  assert.equal(lost.calls.digs.length, 1, 'A failed recovery route must not cause more blind mining.');

  const mismatch = fixture({ badDeposit: true });
  const failed = await executeTask(mismatch.bot, task('deposit_item'));
  assert.equal(failed.verified, false);
  assert.match(failed.message, /DEPOSIT_NOT_VERIFIED/);
  assert(mismatch.calls.closes > 0);

  const slow = fixture({ navigationDelay: 60 });
  const expired = await executeTask(slow.bot, task('mine_resource'), { timeoutMs: 10 });
  assert.equal(expired.status, 'unknown');
  assert.equal((await executeTask(slow.bot, task('mine_resource'))).success, false, 'Old action must settle before another job.');
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(slow.calls.digs.length, 0, 'A delayed navigation must not resume mining after timeout.');

  const raw = fixture();
  const rawResult = await executeCodeSnippet('await new Promise(r => setTimeout(r, 40)); bot.chat("late action");', raw.bot, 5);
  assert.equal(rawResult.status, 'unknown');
  await new Promise(resolve => setTimeout(resolve, 80));
  assert.deepEqual(raw.calls.chats, []);

  const cancelled = fixture({ navigationDelay: 50 });
  const job = executeTask(cancelled.bot, task('mine_resource'));
  setTimeout(() => cancelOperation(cancelled.bot), 5);
  assert.equal((await job).status, 'unknown');
  await new Promise(resolve => setTimeout(resolve, 80));
  assert.equal(cancelled.calls.digs.length, 0);
  console.log('✓ grounded resources, chest verification, and cancellation tests passed');
}

module.exports = { runResourceTests };
