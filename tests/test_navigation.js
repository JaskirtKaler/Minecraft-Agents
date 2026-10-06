const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const Block = require('../bot/node_modules/prismarine-block')('1.20.1');
const { goals, Movements } = require('../bot/node_modules/mineflayer-pathfinder');
const Move = require('../bot/node_modules/mineflayer-pathfinder/lib/move');
const { executeTask } = require('../bot/skills');
const { cancelOperation } = require('../bot/operations');
const { planStaircase, walkTo, LIMITS } = require('../bot/navigation');

function pitFixture ({ digDelay = 0, ceiling = 67, blocked = false, tool = true } = {}) {
  const edits = new Map();
  const key = p => `${p.x},${p.y},${p.z}`;
  const set = (p, name) => edits.set(key(p), name);
  const start = new Vec3(0, 64, 0);
  const chestPosition = new Vec3(4, 68, 0);
  set(start, 'air'); set(start.offset(0, 1, 0), 'air');
  set(chestPosition, 'chest');
  const inventory = [{ name: 'cobblestone', type: data.itemsByName.cobblestone.id, count: 0 }];
  if (tool) inventory.push({ name: 'wooden_pickaxe', type: data.itemsByName.wooden_pickaxe.id, count: 1 });
  const calls = { digs: [], walks: [], deposits: [], tosses: [], stops: 0 };
  let chestCount = 0;
  const bot = {
    version: '1.20.1', registry: require('../bot/node_modules/prismarine-registry')('1.20.1'),
    game: { minY: -64, maxY: 320 },
    entity: { position: start.clone(), onGround: true }, entities: {},
    inventory: { items: () => inventory.filter(item => item.count > 0) },
    players: { Pilot6117: { entity: { username: 'Pilot6117', position: new Vec3(4, 68, 0) } } },
    blockAt (p) {
      const name = edits.get(key(p)) || (p.y <= ceiling ? (blocked && p.y >= 64 ? 'bedrock' : 'stone') : 'air');
      const b = Block.fromStateId(data.blocksByName[name].defaultState);
      b.position = p.clone();
      return b;
    },
    findBlocks ({ matching, useExtraInfo, count }) {
      return [...edits].map(([encoded]) => bot.blockAt(new Vec3(...encoded.split(',').map(Number))))
        .filter(matching).filter(b => typeof useExtraInfo !== 'function' || useExtraInfo(b))
        .slice(0, count).map(b => b.position);
    },
    canDigBlock: b => b.position.distanceTo(bot.entity.position) < 4.5,
    async equip () {},
    async unequip () {},
    async dig (b) {
      calls.digs.push({ position: b.position.clone(), feet: bot.entity.position.clone(), block: b.name });
      if (digDelay) await new Promise(resolve => setTimeout(resolve, digDelay));
      set(b.position, 'air');
      if (b.name === 'stone') inventory[0].count++;
    },
    async waitForTicks () {},
    async openChest () {
      return {
        slots: Array(27).fill(null), inventoryStart: 27,
        containerItems: () => chestCount ? [{ name: 'cobblestone', count: chestCount }] : [],
        async deposit (_type, _metadata, count) {
          calls.deposits.push(count); inventory[0].count -= count; chestCount += count;
        }, close () {}
      };
    },
    async toss (_type, _metadata, count) { calls.tosses.push(count); inventory[0].count -= count; }
  };
  const isAir = p => bot.blockAt(p).boundingBox === 'empty';
  function neighbours (p) {
    const result = [];
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      for (const dy of [0, 1, -1]) {
        const to = p.offset(dx, dy, dz);
        if (!isAir(to) || !isAir(to.offset(0, 1, 0)) || bot.blockAt(to.offset(0, -1, 0)).boundingBox !== 'block') continue;
        if (dy > 0 && !isAir(p.offset(0, 2, 0))) continue;
        result.push(to);
      }
    }
    return result;
  }
  function walkingRoute (goal) {
    const queue = [{ p: bot.entity.position, path: [] }];
    const visited = new Set();
    while (queue.length && visited.size < 500) {
      const { p, path } = queue.shift();
      if (visited.has(key(p))) continue;
      visited.add(key(p));
      if (goal.isEnd(p)) return path;
      for (const to of neighbours(p)) {
        if (Math.abs(to.x) <= 10 && Math.abs(to.z) <= 10 && to.y >= 63 && to.y <= 75) queue.push({ p: to, path: [...path, to] });
      }
    }
    return null;
  }
  const realMovements = new Movements(bot);
  realMovements.canDig = false;
  realMovements.allow1by1towers = false;
  realMovements.scafoldingBlocks = [];
  realMovements.allowParkour = false;
  realMovements.maxDropDown = 1;
  bot.pathfinder = {
    movements: realMovements,
    getPathTo (_moves, goal) {
      const route = walkingRoute(goal);
      return { status: route ? 'success' : 'noPath', cost: route?.length || 0 };
    },
    async goto (goal) {
      assert.equal(this.movements.canDig, false);
      const route = walkingRoute(goal);
      assert(route, 'Walking must use verified, supported clearance without implicit digging.');
      for (const to of route) {
        const from = bot.entity.position;
        const neighbours = realMovements.getNeighbors(new Move(from.x, from.y, from.z, 0, 0));
        assert(neighbours.some(n => n.x === to.x && n.y === to.y && n.z === to.z && !n.toBreak.length && !n.toPlace.length),
          'The installed pathfinder must support this step without more digging or placement.');
        calls.walks.push(to.clone()); bot.entity.position = to.clone();
      }
    },
    setGoal () { calls.stops++; }
  };
  return { bot, calls, inventory, set, start, chestPosition };
}

async function runNavigationTests () {
  let stopped = 0;
  let alternate = 0;
  const circling = {
    entity: { get position () { return new Vec3(alternate++ % 2 ? -1 : 0, 64, 0); } },
    pathfinder: { goto: () => new Promise(() => {}), setGoal: () => { stopped++; } }
  };
  await assert.rejects(walkTo(circling, new goals.GoalBlock(10, 64, 0), {
    timeoutMs: 500, stalledMs: 20, intervalMs: 5
  }), error => error.code === 'NO_NAVIGATION_PROGRESS');
  assert.equal(stopped, 1, 'Circling should be stopped without waiting for the full task deadline.');

  const pit = pitFixture();
  const escaped = await executeTask(pit.bot, { name: 'escape_staircase', args: { rise: 4 } });
  assert.equal(escaped.verified, true, escaped.message);
  assert.equal(pit.bot.entity.position.y, 68);
  assert(escaped.data.steps <= LIMITS.steps);
  assert(pit.calls.digs.length <= LIMITS.blocks);
  for (const dig of pit.calls.digs) {
    assert(dig.position.y >= dig.feet.y, 'Never mine underfoot to escape.');
    assert.equal(pit.bot.blockAt(dig.feet.offset(0, -1, 0)).name, 'stone', 'Preserve staircase supports.');
  }

  const chest = pitFixture();
  const delivered = await executeTask(chest.bot, { name: 'mine_and_deposit', args: { item: 'cobblestone', count: 2 } });
  assert.equal(delivered.verified, true, delivered.message);
  assert(chest.bot.entity.position.y >= chest.chestPosition.y, 'Exit to the chest level, not underneath it.');
  assert.equal(chest.bot.blockAt(chest.chestPosition.offset(0, -1, 0)).name, 'stone', 'Do not undermine the chest.');
  assert.deepEqual(chest.calls.deposits, [2]);
  assert(delivered.data.deposit.navigation.steps > 0);
  assert(delivered.data.mine.after >= 2, 'Staircase stone contributes to the collection instead of being forgotten.');
  assert.equal(delivered.data.mine.mined_positions.length, 0, 'Enough was collected while escaping; no extra resource mining needed.');
  assert(chest.calls.digs.every(dig => dig.block !== 'chest'));

  const deep = pitFixture({ ceiling: 80 });
  const climbed = await executeTask(deep.bot, { name: 'escape_staircase', args: { rise: 8 } });
  assert.equal(climbed.verified, true, climbed.message);
  assert.equal(deep.bot.entity.position.y, 72);
  assert(deep.calls.digs.length <= LIMITS.blocks);

  const unknown = pitFixture();
  const knownBlockAt = unknown.bot.blockAt;
  unknown.bot.blockAt = p => p.y > 65 ? null : knownBlockAt(p);
  assert.equal((await executeTask(unknown.bot, { name: 'escape_staircase', args: { rise: 4 } })).success, false);
  assert.equal(unknown.calls.digs.length, 0);

  const player = pitFixture();
  player.inventory[0].count = 3;
  const given = await executeTask(player.bot, { name: 'give_item', args: { item: 'cobblestone', count: 2, recipient: 'Pilot6117' } });
  assert.equal(given.verified, true, given.message);
  assert.deepEqual(player.calls.tosses, [2]);
  assert(player.bot.entity.position.y > 64, 'Player approach may use controlled uphill escape.');

  for (const settings of [{ blocked: true }, { tool: false }]) {
    const unsafe = pitFixture(settings);
    const result = await executeTask(unsafe.bot, { name: 'escape_staircase', args: { rise: 4 } });
    assert.equal(result.success, false);
    assert.equal(unsafe.calls.digs.length, 0, 'No excavation before a complete bounded plan.');
  }
  const protectedPit = pitFixture();
  protectedPit.set(new Vec3(1, 65, 0), 'chest');
  const plan = await planStaircase(protectedPit.bot, new goals.GoalY(68));
  assert(plan.every(step => step.digs.every(p => protectedPit.bot.blockAt(p).name !== 'chest')));

  for (const material of ['lava', 'water', 'gravel', 'sand']) {
    const hazardous = pitFixture();
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      for (let y = 64; y <= 67; y++) hazardous.set(new Vec3(dx, y, dz), material);
    }
    const result = await executeTask(hazardous.bot, { name: 'escape_staircase', args: { rise: 4 } });
    assert.equal(result.success, false, material);
    assert.equal(hazardous.calls.digs.length, 0, 'Do not excavate hazardous escape routes.');
  }
  const changed = pitFixture();
  const originalDig = changed.bot.dig;
  changed.bot.dig = async b => {
    await originalDig(b);
    // Introduce water before the next planned clearance action.
    changed.set(changed.bot.entity.position.offset(0, 2, 0), 'water');
  };
  assert.equal((await executeTask(changed.bot, { name: 'escape_staircase', args: { rise: 4 } })).success, false);
  assert.equal(changed.calls.digs.length, 1);

  const cancelled = pitFixture({ digDelay: 40 });
  const job = executeTask(cancelled.bot, { name: 'escape_staircase', args: { rise: 4 } });
  while (!cancelled.calls.digs.length) await new Promise(resolve => setTimeout(resolve, 1));
  cancelOperation(cancelled.bot);
  assert.equal((await job).status, 'unknown');
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(cancelled.calls.digs.length, 1, 'No next excavation after cancellation.');
  assert.equal(cancelled.calls.walks.length, 0);

  const expired = pitFixture({ digDelay: 40 });
  const timedOut = await executeTask(expired.bot, { name: 'escape_staircase', args: { rise: 4 } }, { timeoutMs: 15 });
  assert.equal(timedOut.status, 'unknown');
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(expired.calls.digs.length, 1, 'An already-issued dig may finish, but no later excavation may begin.');
  assert.equal(expired.calls.walks.length, 0);
  console.log('✓ controlled staircase escape, delivery, hazards, and cancellation tests passed');
}

module.exports = { runNavigationTests };
