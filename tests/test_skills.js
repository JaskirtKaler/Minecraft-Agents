const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { executeTask } = require('../bot/skills');
const { runStateTests } = require('./test_state');
const { runResourceTests } = require('./test_resources');
const { runNavigationTests } = require('./test_navigation');
const { runToolTests } = require('./test_tools');
const { runObservationTests } = require('./test_observations');

function position (x, y, z) {
  return { x, y, z };
}

// A resolved pathfinder.goto() means the bot reached a goal node in the real
// client. Keep successful fakes honest now that walkTo verifies that fact.
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
          const node = position(Math.floor(anchor.x) + dx, Math.floor(anchor.y) + dy, Math.floor(anchor.z) + dz);
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

function makeBot ({ logs = 0, candidateCount = 8, recipientVisible = true, useTossStackOnly = false } = {}) {
  const inventory = logs > 0 ? [{ name: 'oak_log', type: 110, count: logs, slot: 10 }] : [];
  const blocks = new Map();
  const calls = { goto: [], digs: [], tosses: [], tossStacks: [], ticks: 0 };

  for (let i = 0; i < candidateCount; i += 1) {
    const pos = position(i + 1, 64, 0);
    blocks.set(`${pos.x},${pos.y},${pos.z}`, {
      name: 'oak_log',
      position: pos
    });
  }

  const recipient = { username: 'Pilot6117', position: position(4, 64, 4) };
  const bot = {
    entity: { position: position(0, 64, 1) },
    inventory: {
      items: () => inventory.filter(item => item.count > 0)
    },
    players: {
      Pilot6117: { entity: recipientVisible ? recipient : null }
    },
    entities: recipientVisible ? { recipient } : {},
    pathfinder: {
      movements: { canDig: false, allow1by1towers: false },
      getPathTo: () => ({ status: 'success', cost: 1 }),
      async goto (goal) {
        calls.goto.push(goal);
        arriveAtGoal(bot, goal);
      }
    },
    findBlocks ({ matching, count }) {
      return [...blocks.values()]
        .filter(block => matching(block))
        .slice(0, count)
        .map(block => block.position);
    },
    blockAt (pos) {
      return blocks.get(`${pos.x},${pos.y},${pos.z}`) || null;
    },
    canDigBlock (block) {
      return Boolean(block);
    },
    async dig (block) {
      calls.digs.push(block.position);
      blocks.delete(`${block.position.x},${block.position.y},${block.position.z}`);
      const stack = inventory.find(item => item.name === 'oak_log');
      if (stack) stack.count += 1;
      else inventory.push({ name: 'oak_log', type: 110, count: 1, slot: 10 });
    },
    async waitForTicks () {
      calls.ticks += 1;
    }
  };

  if (useTossStackOnly) {
    bot.tossStack = async item => {
      calls.tossStacks.push({ ...item });
      item.count = 0;
    };
  } else {
    bot.toss = async (itemType, metadata, count) => {
      calls.tosses.push({ itemType, metadata, count });
      let remaining = count;
      for (const item of inventory) {
        if (item.type !== itemType || remaining === 0) continue;
        const toTake = Math.min(item.count, remaining);
        item.count -= toTake;
        remaining -= toTake;
      }
      if (remaining !== 0) throw new Error('mock toss was asked for unavailable items');
    };
  }

  return { bot, inventory, calls, recipient };
}

async function testMineLogs () {
  const { bot, calls } = makeBot();
  const result = await executeTask(bot, {
    name: 'mine_logs',
    args: { item: 'oak_log', count: 2, max_distance: 24 }
  });

  assert.equal(result.success, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.requested_count, 2);
  assert.equal(result.data.observed_before, 0);
  assert.equal(result.data.observed_after, 2);
  assert.equal(result.data.blocks_dug, 2);
  assert.equal(calls.digs.length, 2);
  assert.equal(calls.goto.length, 0, 'Already reachable safe logs do not need pathfinding.');
}

async function testSafeOverheadLogNeedsNoNavigation () {
  const { bot, calls } = makeBot({ candidateCount: 1 });
  bot.entity.position = position(1, 62, 0);
  bot.pathfinder.goto = async () => { throw new Error('An overhead reachable log must not need a route.'); };
  const result = await executeTask(bot, { name: 'mine_logs', args: { item: 'oak_log', count: 1 } });
  assert.equal(result.verified, true, result.message);
  assert.deepEqual(calls.digs, [position(1, 64, 0)]);
  assert.equal(calls.goto.length, 0);
  assert.equal(result.data.blocks_dug, 1);
  assert.equal(result.data.observed_after, 1);
}

async function testLogNavigationAllowsSafeOverheadAndBesideStances () {
  const { bot, calls } = makeBot({ candidateCount: 1 });
  let reached = false;
  bot.canDigBlock = block => reached && Boolean(block);
  bot.pathfinder.goto = async goal => {
    calls.goto.push(goal);
    assert.equal(goal.constructor.name, 'GoalSafeGetToLog');
    assert.equal(goal.isEnd(position(1, 65, 0)), false, 'Do not stand on the log.');
    assert.equal(goal.isEnd(position(1, 62, 0)), true, 'A same-column log overhead is not underfoot.');
    assert.equal(goal.isEnd(position(1, 64, 1)), true, 'Approach beside it.');
    assert.equal(goal.isEnd(position(1, 63, 1)), true, 'A lower beside stance is also safe.');
    bot.entity.position = position(1, 64, 1);
    reached = true;
  };
  const result = await executeTask(bot, { name: 'mine_logs', args: { item: 'oak_log', count: 1 } });
  assert.equal(result.verified, true, result.message);
  assert.equal(calls.goto.length, 1);
  assert.equal(calls.digs.length, 1);
}

async function testStandingOnLogMustMoveBeforeDigging () {
  const { bot, calls } = makeBot({ candidateCount: 1 });
  bot.entity.position = position(1, 65, 0);
  bot.pathfinder.goto = async goal => {
    calls.goto.push(goal);
    assert.equal(calls.digs.length, 0, 'Reach alone must not authorize underfoot mining.');
    assert.equal(goal.isEnd(bot.entity.position), false);
    bot.entity.position = position(2, 64, 0);
  };
  const result = await executeTask(bot, { name: 'mine_logs', args: { item: 'oak_log', count: 1 } });
  assert.equal(result.verified, true, result.message);
  assert.equal(calls.goto.length, 1);
  assert.equal(calls.digs.length, 1);
}

async function testNavigationDoesNotAuthorizeAnUnsafeActualStance () {
  const { bot, calls } = makeBot({ candidateCount: 1 });
  bot.entity.position = position(1, 65, 0);
  // The mock route reports completion without moving. Actual stance still wins.
  bot.pathfinder.goto = async goal => { calls.goto.push(goal); };
  const result = await executeTask(bot, { name: 'mine_logs', args: { item: 'oak_log', count: 1 } });
  assert.equal(result.verified, false);
  assert.equal(result.data.error_code, 'NO_REACHABLE_LOG');
  assert.equal(calls.goto.length, 1);
  assert.equal(calls.digs.length, 0);
  assert.equal(result.data.blocks_dug, 0);
}

async function testFailedLogPickupDoesNotMineAnotherLog () {
  const { bot, calls } = makeBot();
  bot.dig = async block => {
    calls.digs.push(block.position);
    bot.entities = { drop: {
      position: new Vec3(block.position.x + 0.5, block.position.y, block.position.z + 0.5),
      getDroppedItem: () => ({ name: 'oak_log' })
    } };
  };
  bot.pathfinder.goto = async goal => {
    if (goal.constructor.name === 'GoalBlock') throw new Error('Drop is no longer reachable');
    calls.goto.push(goal);
    arriveAtGoal(bot, goal);
  };
  const result = await executeTask(bot, { name: 'mine_logs', args: { item: 'oak_log', count: 2 } });
  assert.equal(result.status, 'unknown');
  assert.equal(result.data.error_code, 'PICKUP_NOT_VERIFIED');
  assert.equal(calls.digs.length, 1, 'Failed pickup must stop the log loop, including recovery route errors.');
}

async function testGiveExactCount () {
  const { bot, calls } = makeBot({ logs: 5 });
  const result = await executeTask(bot, {
    name: 'give_item',
    args: { item: 'oak_log', count: 2, recipient: 'Pilot6117' }
  });

  assert.equal(result.success, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.observed_before, 5);
  assert.equal(result.data.observed_after, 3);
  assert.deepEqual(calls.tosses, [{ itemType: 110, metadata: null, count: 2 }]);
  assert.equal(calls.goto[0].constructor.name, 'GoalNear');
}

async function testNoOverDelivery () {
  const { bot, calls } = makeBot({ logs: 1 });
  const result = await executeTask(bot, {
    name: 'give_item',
    args: { item: 'oak_log', count: 2, recipient: 'Pilot6117' }
  });

  assert.equal(result.success, false);
  assert.equal(result.verified, false);
  assert.match(result.message, /INSUFFICIENT_ITEMS/);
  assert.equal(calls.tosses.length, 0);
}

async function testEntityFallback () {
  const { bot, calls, recipient } = makeBot({ logs: 2 });
  bot.players.Pilot6117.entity = null;
  bot.entities = { fallback: recipient };

  const result = await executeTask(bot, {
    name: 'give_item',
    args: { item: 'oak_log', count: 2, recipient: 'Pilot6117' }
  });

  assert.equal(result.success, true);
  assert.equal(calls.tosses[0].count, 2);
}

async function testSafeTossStackFallback () {
  const { bot, calls } = makeBot({ logs: 2, useTossStackOnly: true });
  const result = await executeTask(bot, {
    name: 'give_item',
    args: { item: 'oak_log', count: 2, recipient: 'Pilot6117' }
  });

  assert.equal(result.success, true);
  assert.equal(calls.tossStacks.length, 1);

  const tooSmall = await executeTask(makeBot({ logs: 2, useTossStackOnly: true }).bot, {
    name: 'give_item',
    args: { item: 'oak_log', count: 1, recipient: 'Pilot6117' }
  });
  assert.equal(tooSmall.success, false);
  assert.match(tooSmall.message, /EXACT_TOSS_UNAVAILABLE/);
}

async function testMineAndGive () {
  const { bot, calls } = makeBot({ logs: 1 });
  const result = await executeTask(bot, {
    name: 'mine_and_give',
    args: { item: 'oak_log', count: 3, recipient: 'Pilot6117', max_distance: 24 }
  });

  assert.equal(result.success, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.mine.observed_mined, 3);
  assert.equal(result.data.give.observed_before, 4);
  assert.equal(result.data.give.observed_after, 1);
  assert.equal(calls.digs.length, 3);
  assert.equal(calls.tosses[0].count, 3);
}

async function testActionableNoLogsError () {
  const { bot } = makeBot({ candidateCount: 0 });
  const result = await executeTask(bot, {
    name: 'mine_logs',
    args: { item: 'oak_log', count: 1, max_distance: 16 }
  });

  assert.equal(result.success, false);
  assert.equal(result.verified, false);
  assert.match(result.message, /NO_LOGS_FOUND/);
  assert.equal(result.data.observed_before, 0);
  assert.equal(result.data.observed_after, 0);
}

async function testGetAndGiveUsesHeldLogs () {
  const { bot, calls, inventory } = makeBot({ logs: 5, candidateCount: 0 });
  const result = await executeTask(bot, {
    name: 'mine_and_give',
    args: { item: 'oak_log', count: 3, recipient: 'Pilot6117', collection_mode: 'ensure_inventory' }
  });
  assert.equal(result.verified, true, result.message);
  assert.equal(result.data.mine.observed_mined, 0);
  assert.equal(calls.digs.length, 0);
  assert.equal(calls.tosses[0].count, 3);
  assert.equal(inventory[0].count, 2);
}

async function run () {
  await require('./test_rl_navigation').runRlNavigationTests();
  await testMineLogs();
  await testSafeOverheadLogNeedsNoNavigation();
  await testLogNavigationAllowsSafeOverheadAndBesideStances();
  await testStandingOnLogMustMoveBeforeDigging();
  await testNavigationDoesNotAuthorizeAnUnsafeActualStance();
  await testFailedLogPickupDoesNotMineAnotherLog();
  await testGiveExactCount();
  await testNoOverDelivery();
  await testEntityFallback();
  await testSafeTossStackFallback();
  await testMineAndGive();
  await testGetAndGiveUsesHeldLogs();
  await testActionableNoLogsError();
  await runStateTests();
  await runObservationTests();
  await runResourceTests();
  await runNavigationTests();
  await runToolTests();
  console.log('✓ deterministic skills tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
