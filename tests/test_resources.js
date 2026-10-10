const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const { executeTask } = require('../bot/skills');
const { describeSubject } = require('../bot/knowledge');
const { executeCodeSnippet } = require('../bot/sandbox');
const { cancelOperation } = require('../bot/operations');
const { depositItem, safeStances } = require('../bot/resources');
const { harvestTool } = require('../bot/knowledge');
const { goals } = require('../bot/node_modules/mineflayer-pathfinder');

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
        arriveAtGoal(bot, goal);
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

function boundChestFixture () {
  const near = new Vec3(1, 64, 1);
  const bound = new Vec3(8, 64, 0);
  const stored = new Map([[near.toString(), 0], [bound.toString(), 4]]);
  const inventory = [{ name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: 5 }];
  const calls = { goals: [], opens: [], deposits: [], closes: 0 };
  const bot = {
    version: '1.20.1', entity: { position: new Vec3(0, 64, 0) }, inventory: { items: () => inventory.filter(item => item.count) },
    pathfinder: { movements: { canDig: false, allow1by1towers: false },
      getPathTo: () => ({ status: 'success', cost: 1 }),
      goto: async goal => { calls.goals.push(goal); arriveAtGoal(bot, goal); }, setGoal () {} },
    findBlocks: () => [near, bound],
    blockAt: p => stored.has(p.toString()) ? { name: 'chest', position: p } : { name: 'air', position: p },
    waitForTicks: async () => {},
    async openChest (block) {
      calls.opens.push(block.position.toString());
      const name = block.position.toString();
      const count = stored.get(name);
      return { inventoryStart: 27, inventoryEnd: 63,
        slots: [{ name: 'cobblestone', count }, ...Array(26).fill(null), { ...inventory[0] }, ...Array(35).fill(null)],
        containerItems: () => [{ name: 'cobblestone', count }],
        deposit: async (_type, _metadata, quantity) => {
          calls.deposits.push({ position: name, quantity });
          stored.set(name, stored.get(name) + quantity);
          inventory[0].count -= quantity;
        },
        close: () => { calls.closes++; }
      };
    }
  };
  const constraint = { item: 'cobblestone', position: bound, maximum: 6 };
  return { bot, near, bound, stored, inventory, calls, constraint };
}

function elevatedResourceFixture ({ rise = 1, floor = 'grass_block', exposedTop = true,
  unloadedHead = false, reachable = true, unsafeActualStance = false } = {}) {
  // Geometry reduced from the October 10 normal-world survey: stone (18,79,0)
  // has an exposed top, while neighboring grass supports feet at (17,80,0).
  const target = new Vec3(18, 79, 0);
  const stance = target.offset(-1, rise, 0);
  const edits = new Map();
  const makeBlock = (name, p) => ({ name, position: p,
    boundingBox: data.blocksByName[name]?.boundingBox,
    canHarvest: type => !!data.blocksByName[name]?.harvestTools?.[type] });
  const put = (p, name) => edits.set(p.toString(), makeBlock(name, p));
  for (let x = 16; x <= 20; x++) for (let y = 77; y <= 83; y++) for (let z = -2; z <= 2; z++) {
    put(new Vec3(x, y, z), 'stone');
  }
  if (exposedTop) put(target.offset(0, 1, 0), 'air');
  put(stance.offset(0, -1, 0), floor);
  put(stance, 'air');
  if (unloadedHead) edits.delete(stance.offset(0, 1, 0).toString());
  else put(stance.offset(0, 1, 0), 'air');
  const inventory = [
    { name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: 2 },
    { name: 'wooden_pickaxe', type: data.itemsByName.wooden_pickaxe.id, count: 1 }
  ];
  const calls = { goals: [], digs: [] };
  const bot = {
    version: '1.20.1', entity: { position: new Vec3(14.5, 78, 2.5) },
    inventory: { items: () => inventory },
    blockAt: p => edits.get(p.toString()) || null,
    findBlocks: ({ matching, useExtraInfo }) => {
      const block = bot.blockAt(target);
      return block && matching(block) && useExtraInfo(block) ? [target] : [];
    },
    pathfinder: { movements: { canDig: false, allow1by1towers: false },
      getPathTo: () => ({ status: reachable ? 'success' : 'noPath', cost: 1 }),
      goto: async goal => {
        calls.goals.push(goal);
        bot.entity.position = unsafeActualStance ? target.offset(0, 1, 0) : bot.entity.position;
        if (!unsafeActualStance) arriveAtGoal(bot, goal);
      }, setGoal () {} },
    canDigBlock: block => block.position.offset(0.5, 0.5, 0.5).distanceTo(bot.entity.position.offset(0, 1.65, 0)) <= 5.1,
    equip: async () => {}, waitForTicks: async () => {},
    dig: async block => { calls.digs.push(block.position); put(block.position, 'air'); inventory[0].count++; }
  };
  return { bot, calls, target, stance, inventory };
}

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

  for (const rise of [1, 2]) {
    const ledge = elevatedResourceFixture({ rise });
    assert.deepEqual(safeStances(ledge.bot, ledge.bot.blockAt(ledge.target)), [ledge.stance],
      'Only the observed, supported upper neighboring stance should be eligible.');
    assert.equal(harvestTool(ledge.bot, ledge.bot.blockAt(ledge.target)).name, 'wooden_pickaxe',
      'The actual wooden pickaxe remains a valid stone harvest tool.');
    const result = await executeTask(ledge.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
    assert.equal(result.verified, true, result.message);
    assert.deepEqual(ledge.calls.digs, [ledge.target]);
    assert.equal(ledge.calls.goals[0].y, ledge.target.y + rise);
    assert.notEqual(ledge.calls.goals[0].x, ledge.target.x, 'Never mine underneath the standing column.');
    assert.equal(result.data.observed_mined, 1);
  }
  for (const settings of [{ floor: 'lava' }, { floor: 'sand' }, { unloadedHead: true },
    { exposedTop: false }, { reachable: false }, { unsafeActualStance: true }]) {
    const rejectedLedge = elevatedResourceFixture(settings);
    const result = await executeTask(rejectedLedge.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
    assert.equal(result.verified, false, JSON.stringify(settings));
    assert.equal(result.data.error_code, 'NO_SAFE_RESOURCE', JSON.stringify(settings));
    assert.equal(rejectedLedge.calls.digs.length, 0, 'Upper stances must retain loaded, terrain, route and actual-underfoot guards.');
    assert(result.data.resource_scan, 'Safe refusal includes concise source/stance/path diagnostics.');
  }

  // Failed ordinary routes must not consume the entire scan before the first
  // valid elevated ledge is checked. Controlled time avoids wall-clock races.
  for (const upperReachable of [true, false]) {
    const ledge = elevatedResourceFixture();
    const originalBlockAt = ledge.bot.blockAt;
    const ordinaryTargets = [30, 34, 38].map(x => new Vec3(x, 79, 0));
    const airCells = new Set();
    for (const target of ordinaryTargets) for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      airCells.add(target.offset(dx, 0, dz).toString());
      airCells.add(target.offset(dx, 1, dz).toString());
    }
    ledge.bot.blockAt = p => {
      if (p.x < 29 || p.x > 39 || p.z < -1 || p.z > 1 || p.y < 77 || p.y > 83) return originalBlockAt(p);
      const name = airCells.has(p.toString()) ? 'air' : 'stone';
      return { name, position: p, boundingBox: data.blocksByName[name].boundingBox,
        canHarvest: type => !!data.blocksByName[name]?.harvestTools?.[type] };
    };
    ledge.bot.findBlocks = ({ matching, useExtraInfo }) => [...ordinaryTargets, ledge.target]
      .filter(p => matching(ledge.bot.blockAt(p)) && useExtraInfo(ledge.bot.blockAt(p)));
    const checked = [];
    const originalNow = Date.now;
    let clock = originalNow();
    Date.now = () => clock;
    try {
      ledge.bot.pathfinder.getPathTo = (_moves, goal) => {
        checked.push(goal);
        clock += goal.x < 20 ? 200 : 300;
        return { status: goal.x < 20 && upperReachable ? 'success' : 'noPath', cost: 1 };
      };
      const result = await executeTask(ledge.bot, { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
      assert(checked.some(goal => goal.x === ledge.stance.x && goal.y === ledge.stance.y),
        'Elevated routes get reserved time despite slow failing ordinary routes.');
      assert.equal(result.verified, upperReachable, result.message);
      assert(checked.length <= 24, 'Each stance class has a bounded route-check count.');
      if (!upperReachable) {
        const scan = result.data.resource_scan;
        assert.equal(scan.candidate_count, 4);
        assert.deepEqual(scan.stance_count, { ordinary: 12, elevated: 1 });
        assert.equal(scan.path_checks.ordinary, 4);
        assert.equal(scan.path_checks.elevated, 1);
        assert.equal(scan.scan_limited.ordinary, true);
        assert(scan.failure_tags.includes('NO_ROUTE_ESTABLISHED_IN_CHECKED_STANCES'));
        assert(scan.failure_tags.includes('PATH_SCAN_INCOMPLETE'));
        assert.equal(ledge.calls.digs.length, 0);
      }
    } finally { Date.now = originalNow; }
  }

  const missingHarvest = fixture({ silk: true });
  const noHarvestResult = await executeTask(missingHarvest.bot,
    { name: 'mine_resource', args: { item: 'cobblestone', count: 1 } });
  assert.equal(noHarvestResult.data.error_code, 'TOOL_REQUIRED');
  assert.equal(missingHarvest.calls.digs.length, 0);
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

  const selected = boundChestFixture();
  const selectedResult = await executeTask(selected.bot, task('deposit_item'), { containerConstraint: selected.constraint });
  assert.equal(selectedResult.verified, true, selectedResult.message);
  assert.equal(selected.stored.get(selected.bound.toString()), 6);
  assert.equal(selected.stored.get(selected.near.toString()), 0, 'Do not deliver to the nearer, non-goal chest.');
  assert(selected.calls.opens.every(p => p === selected.bound.toString()));

  const partialDelivery = boundChestFixture();
  partialDelivery.stored.set(partialDelivery.bound.toString(), 5);
  const overshoot = await executeTask(partialDelivery.bot, task('deposit_item'), { containerConstraint: partialDelivery.constraint });
  assert.equal(overshoot.data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(partialDelivery.calls.deposits.length, 0, 'Do not repeat a full batch after a partial delivery.');
  const remaining = { name: 'deposit_item', args: { item: 'cobblestone', count: 1 } };
  const corrected = await executeTask(partialDelivery.bot, remaining, { containerConstraint: partialDelivery.constraint });
  assert.equal(corrected.verified, true, corrected.message);
  assert.equal(partialDelivery.stored.get(partialDelivery.bound.toString()), 6);

  const changedChest = boundChestFixture();
  const honestBoundOpen = changedChest.bot.openChest;
  changedChest.bot.openChest = async block => {
    if (changedChest.calls.opens.length === 1) changedChest.stored.set(changedChest.bound.toString(), 6);
    return honestBoundOpen(block);
  };
  const changed = await executeTask(changedChest.bot, remaining, { containerConstraint: changedChest.constraint });
  assert.equal(changed.data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(changedChest.calls.deposits.length, 0, 'Recheck the deposit window after an external chest change.');
  assert.equal(changedChest.stored.get(changedChest.bound.toString()), 6);

  const missingBound = boundChestFixture();
  missingBound.stored.delete(missingBound.bound.toString());
  const noFallback = await executeTask(missingBound.bot, remaining, { containerConstraint: missingBound.constraint });
  assert.equal(noFallback.data.error_code, 'CHEST_CHANGED');
  assert.equal(missingBound.calls.goals.length, 0);
  assert.equal(missingBound.calls.opens.length, 0, 'A missing goal chest must not fall back to another chest.');

  const fullBound = boundChestFixture();
  const normalFullOpen = fullBound.bot.openChest;
  fullBound.bot.openChest = async block => {
    const window = await normalFullOpen(block);
    window.slots.splice(0, 27, ...Array.from({ length: 27 }, () => ({ name: 'dirt', count: 64 })));
    return window;
  };
  const fullDestination = await executeTask(fullBound.bot, remaining, { containerConstraint: fullBound.constraint });
  assert.equal(fullDestination.data.error_code, 'CHEST_FULL');
  assert.deepEqual(fullBound.calls.opens, [fullBound.bound.toString()], 'Do not fall back from the full goal chest.');
  assert.equal(fullBound.calls.deposits.length, 0);

  for (const invalidConstraint of [null, {},
    { item: 'dirt', position: selected.bound, maximum: 6 },
    { item: 'cobblestone', position: { x: 8.1, y: 64, z: 0 }, maximum: 6 },
    { item: 'cobblestone', position: selected.bound, minimum: 0, maximum: '6' },
    { item: 'cobblestone', position: selected.bound, minimum: 7, maximum: 6 }]) {
    const malformed = boundChestFixture();
    const result = await executeTask(malformed.bot, remaining, { containerConstraint: invalidConstraint });
    assert.equal(result.data.error_code, 'INVALID_CONSTRAINT');
    assert.equal(malformed.calls.goals.length, 0, 'Validate trusted metadata before navigation.');
    assert.equal(malformed.calls.opens.length, 0);
    assert.equal(malformed.calls.deposits.length, 0);
  }

  const mismatchedTarget = boundChestFixture();
  await assert.rejects(depositItem(mismatchedTarget.bot, { item: 'cobblestone', count: 1, maxDistance: 32 },
    { position: mismatchedTarget.near, goal: new goals.GoalGetToBlock(1, 64, 1) },
    () => {}, mismatchedTarget.constraint), error => error.code === 'INVALID_CONSTRAINT');
  assert.equal(mismatchedTarget.calls.goals.length, 0);
  assert.equal(mismatchedTarget.calls.opens.length, 0);

  const minimumBound = boundChestFixture();
  const belowMinimum = await executeTask(minimumBound.bot, remaining, {
    containerConstraint: { ...minimumBound.constraint, minimum: 6 }
  });
  assert.equal(belowMinimum.data.error_code, 'GOAL_QUANTITY_LIMIT');
  assert.equal(minimumBound.calls.deposits.length, 0);

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
