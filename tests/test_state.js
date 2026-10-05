const assert = require('assert/strict');
const {
  createBlockChangeObservation,
  getBotState,
  getWorldIdentity,
  startWorldSession
} = require('../bot/state');

function vec3 (x, y, z) {
  return {
    x,
    y,
    z,
    offset (dx, dy, dz) {
      return vec3(x + dx, y + dy, z + dz);
    },
    distanceTo (other) {
      return Math.hypot(x - other.x, y - other.y, z - other.z);
    }
  };
}

function block (name, position) {
  return { name, position };
}

function makeBot () {
  const botPosition = vec3(2.2, 64, -3.8);
  const oakPosition = vec3(4, 64, -3);
  const oakLog = block('oak_log', oakPosition);

  return {
    entity: { position: botPosition },
    game: { gameMode: 'survival', dimension: 'minecraft:overworld' },
    health: 20,
    food: 20,
    oxygenLevel: 20,
    inventory: {
      items: () => [{ name: 'oak_log', count: 2, slot: 10 }],
      slots: []
    },
    entities: {},
    findBlocks: ({ matching }) => matching(oakLog) ? [oakPosition] : [],
    blockAt: (position) => {
      if (position.x === oakPosition.x && position.y === oakPosition.y && position.z === oakPosition.z) {
        return oakLog;
      }
      return block('grass_block', position);
    }
  };
}

function saveEnvironment (keys) {
  return Object.fromEntries(keys.map(key => [key, process.env[key]]));
}

function restoreEnvironment (saved) {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

async function runStateTests () {
  const environment = saveEnvironment(['MC_WORLD_ID', 'MC_HOST', 'MC_PORT']);

  try {
    process.env.MC_WORLD_ID = 'unit-test-world';
    process.env.MC_HOST = 'ignored-host';
    process.env.MC_PORT = '25570';

    const bot = makeBot();
    const state = getBotState(bot);

    assert.equal(state.ready, true);
    assert.match(state.observedAt, /^\d{4}-\d{2}-\d{2}T/);
    assert.equal(state.world.id, 'unit-test-world');
    assert.equal(state.world.dimension, 'minecraft:overworld');
    assert.match(state.world.sessionId, /^[0-9a-f]{8}-[0-9a-f-]{27}$/i);
    assert.equal(state.nearbyKeyBlocksComplete, false);
    assert.equal(state.nearbyKeyBlocks.oak_log.length, 1);

    const sameSessionState = getBotState(bot);
    assert.equal(sameSessionState.world.sessionId, state.world.sessionId);

    const nextSession = startWorldSession(bot);
    assert.notEqual(nextSession, state.world.sessionId);
    assert.equal(getBotState(bot).world.sessionId, nextSession);

    const observedAt = '2026-10-05T12:00:00.000Z';
    const logToAir = createBlockChangeObservation(
      bot,
      block('oak_log', vec3(4, 64, -3)),
      block('air', vec3(4, 64, -3)),
      observedAt
    );
    assert.deepEqual(logToAir.position, { x: 4, y: 64, z: -3 });
    assert.equal(logToAir.oldName, 'oak_log');
    assert.equal(logToAir.newName, 'air');
    assert.equal(logToAir.observedAt, observedAt);
    assert.equal(logToAir.world.sessionId, nextSession);

    const discoveredChest = createBlockChangeObservation(
      bot,
      null,
      block('chest', vec3(5, 64, -3)),
      observedAt
    );
    assert.equal(discoveredChest.oldName, null);
    assert.equal(discoveredChest.newName, 'chest');

    // Chunk unloads are represented by null and must never be treated as
    // resource depletion. Cosmetic updates stay out of the memory stream too.
    assert.equal(createBlockChangeObservation(bot, block('oak_log', vec3(4, 64, -3)), null), null);
    assert.equal(createBlockChangeObservation(bot, null, null), null);
    assert.equal(
      createBlockChangeObservation(bot, block('dirt', vec3(1, 64, 1)), block('air', vec3(1, 64, 1))),
      null
    );

    delete process.env.MC_WORLD_ID;
    process.env.MC_HOST = 'memory-host';
    process.env.MC_PORT = '25571';
    const fallbackBot = makeBot();
    assert.equal(getWorldIdentity(fallbackBot).id, 'memory-host:25571');

    const unspawned = getBotState({ game: { dimension: 'minecraft:the_nether' } });
    assert.equal(unspawned.ready, false);
    assert.equal(unspawned.world.dimension, 'minecraft:the_nether');
    assert.equal(unspawned.nearbyKeyBlocksComplete, false);
    assert.match(unspawned.world.sessionId, /^[0-9a-f]{8}-[0-9a-f-]{27}$/i);
  } finally {
    restoreEnvironment(environment);
  }

  console.log('✓ world observation state tests passed');
}

if (require.main === module) {
  runStateTests().catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = { runStateTests };
