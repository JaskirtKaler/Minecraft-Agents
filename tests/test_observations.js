const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { getObservation } = require('../bot/observations');

function fixture () {
  const blocks = new Map();
  const add = (name, type, stateId, x, y, z, shapes, properties = {}) => {
    const position = new Vec3(x, y, z);
    blocks.set(position.toString(), { name, type, stateId, position,
      boundingBox: shapes.length ? 'block' : 'empty', shapes, getProperties: () => properties });
  };
  add('stone', 1, 1, -1, 63, 2, [[0, 0, 0, 1, 1, 1]]);
  add('stone', 1, 1, 7, 64, 2, [[0, 0, 0, 1, 1, 1]]);
  add('oak_slab', 2, 2, -2, 64, 2, [[0, 0, 0, 1, 0.5, 1]]);
  add('lava', 3, 3, 0, 64, 2, []);
  add('water', 4, 4, 1, 64, 2, []);
  add('chest', 5, 5, 1, 64, 3, [[0.0625, 0, 0.0625, 0.9375, 0.875, 0.9375]], { waterlogged: true });
  add('wheat', 6, 107, -1, 64, 3, [], { age: 7 });
  const calls = { blockAt: 0, physical: 0 };
  const items = [{ name: 'wooden_pickaxe', type: 10, count: 1, slot: 36, durabilityUsed: 5 }];
  const bot = {
    username: 'FixtureBot', version: '1.20.1',
    entity: { position: new Vec3(-0.2, 64, 2.3), velocity: new Vec3(0.1, 0, 0),
      yaw: 0, pitch: 0, onGround: true },
    health: 20, food: 20, oxygenLevel: 20,
    game: { dimension: 'minecraft:overworld', gameMode: 'survival' },
    registry: { itemsByName: { wooden_pickaxe: { id: 10, maxDurability: 59 } } },
    inventory: { items: () => items, slots: [] },
    entities: {},
    findBlocks: ({ matching, count }) => [...blocks.values()].filter(matching).slice(0, count).map(b => b.position),
    blockAt (position) {
      calls.blockAt++;
      if (position.x === 2) return null;
      return blocks.get(position.toString()) || { name: 'air', type: 0, stateId: 0,
        position, boundingBox: 'empty', shapes: [] };
    }
  };
  for (const method of ['dig', 'craft', 'equip', 'openContainer', 'setControlState', 'look']) {
    bot[method] = () => { calls.physical++; throw new Error('Read-only observation performed an action.'); };
  }
  return { bot, calls, items };
}

function index (terrain, dx, dy, dz) {
  const [x, y, z] = terrain.shape;
  return ((dx + (x - 1) / 2) * y + dy + (y - 1) / 2) * z + dz + (z - 1) / 2;
}

async function runObservationTests () {
  const { bot, calls, items } = fixture();
  const snapshot = getObservation(bot);
  assert.equal(snapshot.schemaVersion, 1);
  assert.equal(snapshot.gameVersion, '1.20.1');
  assert.equal(snapshot.lineOfSightFiltered, false);
  assert.equal(snapshot.landmarkListComplete, false);
  assert.deepEqual(snapshot.terrain.center, { x: -1, y: 64, z: 2 });
  assert.deepEqual(snapshot.terrain.shape, [7, 5, 7]);
  for (const key of ['blockIds', 'stateIds', 'loaded', 'collision', 'fluid']) {
    assert.equal(snapshot.terrain[key].length, 245);
  }
  const stone = index(snapshot.terrain, 0, -1, 0);
  const slab = index(snapshot.terrain, -1, 0, 0);
  const lava = index(snapshot.terrain, 1, 0, 0);
  const water = index(snapshot.terrain, 2, 0, 0);
  const waterlogged = index(snapshot.terrain, 2, 0, 1);
  const crop = index(snapshot.terrain, 0, 0, 1);
  assert.equal(snapshot.terrain.blockIds[stone], 1);
  assert.equal(snapshot.terrain.collision[stone], 1);
  assert.equal(snapshot.terrain.collision[slab], 2);
  assert.equal(snapshot.terrain.fluid[lava], 2);
  assert.equal(snapshot.terrain.fluid[water], 1);
  assert.equal(snapshot.terrain.fluid[waterlogged], 1);
  assert.equal(snapshot.terrain.stateIds[crop], 107);
  const unknown = index(snapshot.terrain, 3, 0, 0);
  assert.equal(snapshot.terrain.loaded[unknown], false);
  assert.equal(snapshot.terrain.blockIds[unknown], null);
  assert.equal(snapshot.terrain.stateIds[unknown], null);
  assert.equal(snapshot.terrain.collision[unknown], -1);
  assert.equal(snapshot.terrain.fluid[unknown], -1);
  assert.equal(snapshot.terrain.fullyLoaded, false);
  assert.deepEqual(snapshot.motion, { position: { x: -0.2, y: 64, z: 2.3 },
    yaw: 0, pitch: 0, velocity: { x: 0.1, y: 0, z: 0 }, onGround: true });
  assert.equal(snapshot.inventoryDetails[0].maxDurability, 59);
  assert.equal(snapshot.inventoryDetails[0].durabilityUsed, 5);
  items[0].count = 2;
  assert.equal(snapshot.inventoryDetails[0].count, 1, 'Snapshots must not retain mutable inventory objects.');
  assert.equal(calls.physical, 0);
  assert(snapshot.landmarks.some(l => l.name === 'stone' && l.position.x === 7 && l.relative.x === 7.2));
  assert(snapshot.landmarks.every((l, i, all) => i === 0 || l.distance >= all[i - 1].distance));

  const compact = getObservation(bot, { horizontalRadius: 0, verticalRadius: 0 });
  assert.deepEqual(compact.terrain.shape, [1, 1, 1]);
  const maximum = getObservation(bot, { horizontalRadius: 5, verticalRadius: 3 });
  assert.equal(maximum.terrain.loaded.length, 847);
  for (const options of [null, [], { horizontalRadius: -1 }, { horizontalRadius: 6 },
    { verticalRadius: 4 }, { verticalRadius: 1.5 }, { horizontalRadius: true }, { arbitrary: 1 }]) {
    assert.throws(() => getObservation(bot, options), /options|integer/);
  }
  const notReady = getObservation({ version: '1.20.1' });
  assert.equal(notReady.state.ready, false);
  assert.equal(notReady.motion, null);
  assert.equal(notReady.terrain, null);
  assert.deepEqual(notReady.inventoryDetails, []);
  assert.deepEqual(notReady.landmarks, []);

  const original = bot.blockAt;
  bot.blockAt = p => ({ ...original(p), boundingBox: 'block', shapes: undefined });
  assert.equal(getObservation(bot, { horizontalRadius: 0, verticalRadius: 0 }).terrain.collision[0], -1);
  console.log('✓ read-only structured terrain, movement, inventory and landmark observations passed');
}

if (require.main === module) runObservationTests().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { fixture, runObservationTests };
