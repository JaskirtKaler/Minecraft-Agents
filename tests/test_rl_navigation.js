const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const { navigationFrame, executeNavigationStep } = require('../bot/rl_navigation');
const { cancelOperation } = require('../bot/operations');

function fixture () {
  const overrides = new Map();
  const calls = { walks: 0, physical: 0, stops: 0 };
  const make = (name, p) => ({ name, position: p, type: data.blocksByName[name].id,
    stateId: data.blocksByName[name].minStateId, boundingBox: ['air', 'fire', 'water', 'lava'].includes(name) ? 'empty' : 'block',
    shapes: ['air', 'fire', 'water', 'lava'].includes(name) ? [] : [[0, 0, 0, 1, 1, 1]], getProperties: () => ({}) });
  const bot = {
    username: 'RLFixture', version: '1.20.1', registry: data,
    entity: { position: new Vec3(0.5, 64, 0.5), yaw: 0, pitch: 0,
      velocity: new Vec3(0, 0, 0), onGround: true }, health: 20, food: 20,
    game: { dimension: 'minecraft:overworld', gameMode: 'survival' },
    inventory: { items: () => [], slots: [] }, entities: {}, findBlocks: () => [],
    blockAt: p => overrides.has(p.toString()) ? overrides.get(p.toString()) : make(p.y < 64 ? 'stone' : 'air', p),
    pathfinder: { movements: { canDig: false, allow1by1towers: false },
      getPathTo: () => ({ status: 'success', cost: 1 }),
      async goto (goal) { calls.walks++; bot.entity.position = new Vec3(goal.x + 0.5, goal.y, goal.z + 0.5); },
      setGoal: () => { calls.stops++; } },
    async waitForTicks () {}, clearControlStates () {}, stopDigging () {}
  };
  for (const key of ['dig', 'craft', 'equip', 'placeBlock', 'openContainer']) {
    bot[key] = () => { calls.physical++; throw new Error('Navigation must not mine/craft/transfer.'); };
  }
  return { bot, calls, set: (x, y, z, name) => {
    const p = new Vec3(x, y, z);
    overrides.set(p.toString(), name === null ? null : make(name, p));
  } };
}

function request (frame, action = 0) {
  return { action, expectedWorld: frame.observation.state.world, expectedPosition: frame.observation.terrain.center };
}

async function runRlNavigationTests () {
  const original = { enable: process.env.ENABLE_RL_NAVIGATION, world: process.env.MC_WORLD_ID };
  try {
    delete process.env.ENABLE_RL_NAVIGATION;
    process.env.MC_WORLD_ID = 'normal';
    const disabled = fixture();
    assert.throws(() => navigationFrame(disabled.bot), /explicitly enabled/);
    assert.equal((await executeNavigationStep(disabled.bot, { action: 0 })).data.error_code, 'RL_DISABLED');
    assert.equal(disabled.calls.walks, 0);
    process.env.ENABLE_RL_NAVIGATION = 'true';
    assert.throws(() => navigationFrame(disabled.bot), /disposable/);
    process.env.MC_WORLD_ID = 'practice:rl:fixture';
    const open = fixture();
    const frame = navigationFrame(open.bot);
    assert.deepEqual(frame.actionMask, [true, true, true, true, true]);
    const moved = await executeNavigationStep(open.bot, request(frame));
    assert.equal(moved.verified, true, moved.message);
    assert.equal(open.calls.walks, 1);
    assert.equal(open.calls.physical, 0);
    assert.deepEqual(moved.data.after, { x: 1, y: 64, z: 0 });
    assert.equal((await executeNavigationStep(open.bot, request(frame))).data.error_code, 'RL_POSITION_CHANGED');
    const stale = request(navigationFrame(open.bot));
    stale.expectedWorld = { ...stale.expectedWorld, sessionId: 'old-session' };
    assert.equal((await executeNavigationStep(open.bot, stale)).data.error_code, 'RL_SESSION_CHANGED');
    for (const hazard of ['water', 'lava', 'fire', 'powder_snow']) {
      const blocked = fixture();
      blocked.set(1, 64, 0, hazard);
      const unsafe = navigationFrame(blocked.bot);
      assert.equal(unsafe.actionMask[0], false, hazard);
      assert.equal((await executeNavigationStep(blocked.bot, request(unsafe))).data.error_code, 'RL_MASKED_ACTION');
      assert.equal(blocked.calls.walks, 0);
    }
    for (const name of [null, 'air', 'magma_block']) {
      const unsafeFloor = fixture();
      unsafeFloor.set(1, 63, 0, name);
      assert.equal(navigationFrame(unsafeFloor.bot).actionMask[0], false);
    }
    const airborne = fixture();
    airborne.bot.entity.onGround = false;
    assert.deepEqual(navigationFrame(airborne.bot).actionMask, [false, false, false, false, true]);
    const badRoute = fixture();
    badRoute.bot.pathfinder.getPathTo = () => ({ status: 'success', cost: 10 });
    assert.equal((await executeNavigationStep(badRoute.bot, request(navigationFrame(badRoute.bot)))).data.error_code, 'RL_NO_SHORT_ROUTE');
    assert.equal(badRoute.calls.walks, 0);
    const wait = fixture();
    assert.equal((await executeNavigationStep(wait.bot, request(navigationFrame(wait.bot), 4))).verified, true);
    assert.equal(wait.calls.walks, 0);
    assert.equal((await executeNavigationStep(wait.bot, { action: true })).data.error_code, 'INVALID_RL_ACTION');
    const cancelled = fixture();
    let release;
    cancelled.bot.pathfinder.goto = async () => new Promise(resolve => { release = resolve; });
    const pending = executeNavigationStep(cancelled.bot, request(navigationFrame(cancelled.bot)));
    await new Promise(resolve => setImmediate(resolve));
    cancelOperation(cancelled.bot);
    assert.equal((await pending).data.error_code, 'CANCELLED');
    release();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(cancelled.calls.physical, 0);
    console.log('✓ isolated RL walking, masks, session guards and cancellation passed');
  } finally {
    for (const [key, value] of [['ENABLE_RL_NAVIGATION', original.enable], ['MC_WORLD_ID', original.world]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

if (require.main === module) runRlNavigationTests().catch(e => { console.error(e); process.exitCode = 1; });
module.exports = { fixture, runRlNavigationTests };
