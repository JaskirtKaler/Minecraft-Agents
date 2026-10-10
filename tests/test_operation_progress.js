const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { goals } = require('../bot/node_modules/mineflayer-pathfinder');
const { runOperation, operationStatus, cancelOperation } = require('../bot/operations');
const { toolTiming, policy } = require('../bot/tool_timing');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function runProgressTests () {
  assert.equal(toolTiming({ name: 'craft' }).timeoutMs, 60000);
  assert.equal(toolTiming({ name: 'mine_logs', args: { count: 16 } }).timeoutMs, 218000);
  assert.equal(toolTiming({ name: 'mine_logs', args: { count: 64 } }).timeoutMs, policy.gatherMaxMs);
  assert.equal(toolTiming({ name: 'repair_batch', args: { positions: [{}, {}, {}] } }).timeoutMs,
    policy.buildBaseMs + 3 * policy.buildPerCellMs);

  const bot = { entity: { position: new Vec3(0, 64, 0) },
    pathfinder: { setGoal () {} }, stopDigging () {}, clearControlStates () {} };
  let primitiveDigs = 0;
  bot.dig = async () => { primitiveDigs++; };
  const finalBlock = { name: 'cobblestone', position: new Vec3(1, 64, 0) };
  await assert.rejects(runOperation(bot, b => b.dig(finalBlock), { beforeDig: (raw, block) => {
    assert.equal(raw, bot);
    assert.equal(block, finalBlock);
    throw Object.assign(new Error('Correct accepted final block.'), { code: 'CONSTRUCTION_ALREADY_CORRECT' });
  } }), error => error.code === 'CONSTRUCTION_ALREADY_CORRECT');
  assert.equal(primitiveDigs, 0, 'Gathering and direct digging must share the last-moment conformance hook.');
  let collected = 0;
  const gaining = setInterval(() => { collected++; }, 70);
  try {
    assert.equal(await runOperation(bot, async () => { await delay(350); return 'collected'; },
      { timeoutMs: 1000, idleTimeoutMs: 180, progressValue: () => collected, progressIntervalMs: 10 }), 'collected');
  } finally { clearInterval(gaining); }
  assert.equal(operationStatus(bot).busy, false);

  await assert.rejects(runOperation(bot, async () => { await delay(300); },
    { timeoutMs: 1000, idleTimeoutMs: 80, progressValue: () => 0, progressIntervalMs: 10 }),
  error => error.code === 'TASK_TIMEOUT' && error.data.timing.idle_limit_ms === 80);
  assert.equal(operationStatus(bot).active, false);
  assert.equal(operationStatus(bot).busy, true, 'Timeout must not release unsettled work.');
  await assert.rejects(runOperation(bot, async () => {}), error => error.code === 'BUSY');
  await delay(240);
  assert.equal(operationStatus(bot).busy, false);

  let release, lateDigs = 0;
  bot.dig = async () => { lateDigs++; };
  bot.pathfinder.goto = async () => { await new Promise(resolve => { release = resolve; }); };
  const moving = setInterval(() => { bot.entity.position.x += .5; }, 35);
  const job = runOperation(bot, async guarded => {
    await guarded.pathfinder.goto(new goals.GoalBlock(100, 64, 0));
    await guarded.dig({ position: new Vec3(1, 64, 0) });
  }, { timeoutMs: 300, idleTimeoutMs: 140, progressIntervalMs: 10 });
  try {
    await assert.rejects(job, error => error.code === 'TASK_TIMEOUT' && error.data.timing.elapsed_ms >= 290);
  } finally { clearInterval(moving); release(); }
  await delay(20);
  assert.equal(lateDigs, 0, 'A revoked route must never begin another physical action.');
  assert.equal(operationStatus(bot).busy, false);

  // Repeated progress text is not evidence; a stationary/circling route cannot
  // reset the operation's clock without getting closer to its actual target.
  bot.entity.position = new Vec3(0, 64, 0);
  bot.pathfinder.goto = async () => { await delay(200); };
  await assert.rejects(runOperation(bot, b => b.pathfinder.goto(new goals.GoalBlock(100, 64, 0)),
    { timeoutMs: 1000, idleTimeoutMs: 80, progressIntervalMs: 10 }), error => error.code === 'TASK_TIMEOUT');
  await delay(150);
  const cancellable = runOperation(bot, async b => { await b.pathfinder.goto(new goals.GoalBlock(100, 64, 0)); },
    { timeoutMs: 1000, idleTimeoutMs: 500 });
  await delay(10);
  assert.equal(cancelOperation(bot), true);
  await assert.rejects(cancellable, error => error.code === 'CANCELLED');
  await delay(210);
  assert.equal(operationStatus(bot).busy, false);
  console.log('✓ quantity budgets, progress/idle/hard limits, settling exclusion and cancellation passed');
}
if (require.main === module) runProgressTests().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { runProgressTests };
