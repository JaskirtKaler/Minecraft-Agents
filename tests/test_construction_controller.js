/** Trusted controller lifecycle without a server/model. */
const assert = require('assert');
const { createConstructionController } = require('../bot/construction_controller');
let world = { id: 'test', sessionId: 'one', dimension: 'overworld' };
let busy = false;
const starts = [], ends = [];
const bot = {};
const controller = createConstructionController(bot, {
  worldIdentity: () => world,
  operationStatus: () => ({ busy }),
  registry: () => ({ air: {}, cobblestone: {}, chest: {} }),
  backend: {
    beginConstructionTask: (raw, id, cells, options) => {
      const handle = { id };
      starts.push({ raw, id, cells, options, handle });
      return handle;
    },
    endConstructionTask: (raw, handle) => ends.push(handle),
    constructionStatus: (raw, handle) => ({ active: true, task_id: handle.id })
  }
});
const contract = { task_id: 'task', world: { ...world }, desired_cells: { '-1,64,-8': 'air', '0,64,-8': 'cobblestone' } };
assert.equal(controller.set(contract).active, true);
assert.throws(() => controller.assertLegacyAllowed(), e => e.code === 'CONSTRUCTION_TASK_ACTIVE');
assert.equal(starts[0].raw, bot);
assert.equal(starts[0].options.worldSessionId, 'one');
assert.equal(controller.current('task'), starts[0].handle);
assert.throws(() => controller.current('forged'), e => e.code === 'STALE_CONSTRUCTION_TASK');
assert.throws(() => controller.current(null), e => e.code === 'STALE_CONSTRUCTION_TASK');
assert.equal(controller.facts('task').task_id, 'task');
assert.equal(controller.set({ ...contract, desired_cells: { '0,64,-8': 'cobblestone', '-1,64,-8': 'air' } }).active, true);
assert.equal(starts.length, 1, 'Idempotent handshake must retain ownership ledger.');
assert.throws(() => controller.set({ ...contract, desired_cells: { '-1,64,-8': 'cobblestone' } }),
  e => e.code === 'IMMUTABLE_CONSTRUCTION_CONTRACT');
for (const desired_cells of [{ '01,64,0': 'air' }, { '-0,64,0': 'air' }, { '0,64,0': 'invented' }, {}, []]) {
  assert.throws(() => controller.set({ ...contract, task_id: 'new', desired_cells }),
    e => e.code === 'INVALID_CONSTRUCTION_CONTRACT');
}
busy = true;
assert.throws(() => controller.set({ ...contract, task_id: 'new' }), e => e.code === 'BUSY');
assert.equal(controller.set(null).active, false, 'Cancellation must revoke while work settles.');
assert.equal(ends.length, 1);
assert.throws(() => controller.current('task'), e => e.code === 'STALE_CONSTRUCTION_TASK');
assert.equal(controller.current(null), null);
assert.doesNotThrow(() => controller.assertLegacyAllowed());
busy = false;
controller.set(contract);
world = { ...world, sessionId: 'two' };
assert.throws(() => controller.current('task'), e => e.code === 'STALE_CONSTRUCTION_WORLD');
assert.equal(ends.length, 2, 'World mismatch immediately revokes the ledger.');
assert.throws(() => controller.set(contract), e => e.code === 'STALE_CONSTRUCTION_WORLD');
console.log('✓ trusted construction RPC lifecycle tests passed');
