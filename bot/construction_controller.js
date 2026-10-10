/** Trusted RPC lifecycle. No model tool may install goals or claim ownership. */
const construction = require('./construction');
const { getWorldIdentity } = require('./state');
const { operationStatus } = require('./operations');
const { dataFor } = require('./knowledge');

function reject (code, message) { throw Object.assign(new Error(message), { code }); }
function createConstructionController (bot, dependencies = {}) {
  const worldIdentity = dependencies.worldIdentity || getWorldIdentity;
  const status = dependencies.operationStatus || operationStatus;
  const backend = dependencies.backend || construction;
  const registry = dependencies.registry || (() => dataFor(bot).blocksByName);
  let handle = null;
  let frozen = null;
  function clear () {
    if (handle) backend.endConstructionTask(bot, handle);
    handle = null;
    frozen = null;
    return { active: false };
  }
  function validateWorld (world) {
    const current = worldIdentity(bot);
    if (!world || ['id', 'sessionId', 'dimension'].some(key =>
      typeof world[key] !== 'string' || !world[key] || world[key] !== current[key])) {
      reject('STALE_CONSTRUCTION_WORLD', 'Construction contract belongs to a different world/session.');
    }
  }
  function set (contract) {
    if (contract === null) return clear(); // Revocation is allowed while work settles.
    if (status(bot).busy) reject('BUSY', 'Cannot install construction authority during an unsettled operation.');
    if (!contract || typeof contract.task_id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(contract.task_id)) {
      reject('INVALID_CONSTRUCTION_CONTRACT', 'Missing bounded controller task identity.');
    }
    validateWorld(contract.world);
    const cells = contract.desired_cells;
    if (!cells || typeof cells !== 'object' || Array.isArray(cells) ||
        !Object.keys(cells).length || Object.keys(cells).length > 4096) {
      reject('INVALID_CONSTRUCTION_CONTRACT', 'Expected 1–4096 distinct final cells.');
    }
    const blocks = registry();
    const expected = Object.entries(cells).map(([key, block]) => {
      if (!/^-?\d+,-?\d+,-?\d+$/.test(key)) reject('INVALID_CONSTRUCTION_CONTRACT', 'Malformed final cell coordinate.');
      const coordinates = key.split(',').map(Number);
      if (!coordinates.every(Number.isSafeInteger) || coordinates.join(',') !== key ||
          typeof block !== 'string' || !blocks[block]) {
        reject('INVALID_CONSTRUCTION_CONTRACT', 'Final cells require canonical coordinates and installed block names.');
      }
      return { position: { x: coordinates[0], y: coordinates[1], z: coordinates[2] }, block };
    });
    const canonical = JSON.stringify({ world: ['id', 'sessionId', 'dimension'].map(key => contract.world[key]),
      cells: Object.entries(cells).sort(([a], [b]) => a.localeCompare(b)) });
    if (frozen && frozen.task_id === contract.task_id) {
      if (frozen.canonical !== canonical) reject('IMMUTABLE_CONSTRUCTION_CONTRACT', 'An accepted task cannot change its final cells.');
      validateWorld(frozen.world);
      return { active: true, task_id: frozen.task_id, cell_count: expected.length };
    }
    clear();
    handle = backend.beginConstructionTask(bot, contract.task_id, expected,
      { worldSessionId: contract.world.sessionId });
    frozen = { task_id: contract.task_id, world: { ...contract.world }, canonical };
    return { active: true, task_id: frozen.task_id, cell_count: expected.length };
  }
  function current (taskId) {
    if (!handle) {
      if (taskId != null) reject('STALE_CONSTRUCTION_TASK', 'Construction task authority has been revoked.');
      return null;
    }
    try { validateWorld(frozen.world); } catch (error) { clear(); throw error; }
    if (taskId !== frozen.task_id) reject('STALE_CONSTRUCTION_TASK', 'Tool does not belong to the accepted construction task.');
    return handle;
  }
  function facts (taskId) {
    const active = current(taskId);
    return active ? backend.constructionStatus(bot, active) : { active: false, repairable: [] };
  }
  function assertLegacyAllowed () {
    if (handle) reject('CONSTRUCTION_TASK_ACTIVE',
      'Legacy task/code execution is unavailable during an accepted construction task; use typed tools.');
  }
  return { set, clear, current, facts, assertLegacyAllowed };
}
module.exports = { createConstructionController };
