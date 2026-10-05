const assert = require('assert/strict');
const { executeTask } = require('../bot/skills');

function position (x, y, z) {
  return { x, y, z };
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
    inventory: {
      items: () => inventory.filter(item => item.count > 0)
    },
    players: {
      Pilot6117: { entity: recipientVisible ? recipient : null }
    },
    entities: recipientVisible ? { recipient } : {},
    pathfinder: {
      async goto (goal) {
        calls.goto.push(goal);
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
  assert.equal(calls.goto[0].constructor.name, 'GoalGetToBlock');
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

async function run () {
  await testMineLogs();
  await testGiveExactCount();
  await testNoOverDelivery();
  await testEntityFallback();
  await testSafeTossStackFallback();
  await testMineAndGive();
  await testActionableNoLogsError();
  console.log('✓ deterministic skills tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
