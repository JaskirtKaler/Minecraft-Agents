/** Read-only perception for a future lightweight controller, not an action policy. */
const { Vec3 } = require('vec3');
const { getBotState } = require('./state');

function integerOption (value, fallback, maximum, name) {
  const result = value === undefined ? fallback : value;
  if (!Number.isInteger(result) || result < 0 || result > maximum) {
    throw new RangeError(`${name} must be an integer from 0 to ${maximum}.`);
  }
  return result;
}

function finite (value) { return Number.isFinite(value) ? value : null; }
function vector (value) {
  return value && ['x', 'y', 'z'].every(axis => Number.isFinite(value[axis]))
    ? { x: value.x, y: value.y, z: value.z } : null;
}
function id (value) { return Number.isInteger(value) && value >= 0 ? value : null; }
function collision (block) {
  if (block.boundingBox === 'empty') return 0;
  if (Array.isArray(block.shapes)) {
    if (!block.shapes.length) return 0;
    if (block.shapes.length === 1 && block.shapes[0].length === 6 &&
        block.shapes[0].every((value, index) => value === [0, 0, 0, 1, 1, 1][index])) return 1;
    return 2;
  }
  // A generic 'block' bounding box is not proof of a full cube (e.g. a slab).
  return -1;
}
function fluid (block) {
  if (['lava', 'flowing_lava'].includes(block.name)) return 2;
  if (['water', 'flowing_water', 'bubble_column'].includes(block.name) ||
      block.getProperties?.()?.waterlogged === true) return 1;
  return typeof block.name === 'string' ? 0 : -1;
}

function getObservation (bot, options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options) ||
      Object.keys(options).some(key => !['horizontalRadius', 'verticalRadius'].includes(key))) {
    throw new TypeError('Observation options accept only horizontalRadius and verticalRadius.');
  }
  const radius = integerOption(options.horizontalRadius, 3, 5, 'horizontalRadius');
  const height = integerOption(options.verticalRadius, 2, 3, 'verticalRadius');
  const state = getBotState(bot);
  const observation = {
    schemaVersion: 1,
    source: 'mineflayer',
    gameVersion: typeof bot?.version === 'string' ? bot.version : null,
    state,
    motion: null,
    terrain: null,
    inventoryDetails: [],
    landmarks: [],
    landmarkListComplete: false,
    lineOfSightFiltered: false
  };
  if (!state.ready) return observation;
  const position = vector(bot.entity.position);
  if (!position || typeof bot.blockAt !== 'function') {
    throw new TypeError('Spawned bot requires a finite position and loaded-block access.');
  }
  observation.motion = {
    position,
    yaw: finite(bot.entity.yaw),
    pitch: finite(bot.entity.pitch),
    velocity: vector(bot.entity.velocity),
    onGround: typeof bot.entity.onGround === 'boolean' ? bot.entity.onGround : null
  };
  const center = new Vec3(Math.floor(position.x), Math.floor(position.y), Math.floor(position.z));
  const terrain = {
    center: vector(center),
    shape: [2 * radius + 1, 2 * height + 1, 2 * radius + 1],
    axisOrder: 'xyz',
    blockIds: [],
    stateIds: [],
    loaded: [],
    collision: [],
    fluid: [],
    fullyLoaded: true
  };
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dy = -height; dy <= height; dy++) {
      for (let dz = -radius; dz <= radius; dz++) {
        const block = bot.blockAt(center.offset(dx, dy, dz));
        terrain.loaded.push(Boolean(block));
        terrain.blockIds.push(block ? id(block.type) : null);
        terrain.stateIds.push(block ? id(block.stateId) : null);
        terrain.collision.push(block ? collision(block) : -1);
        terrain.fluid.push(block ? fluid(block) : -1);
        if (!block) terrain.fullyLoaded = false;
      }
    }
  }
  observation.terrain = terrain;
  observation.inventoryDetails = bot.inventory.items().map(item => {
    const definition = bot.registry?.itemsByName?.[item.name];
    return {
      name: item.name, count: item.count, slot: item.slot,
      itemId: id(item.type),
      durabilityUsed: id(item.durabilityUsed),
      maxDurability: Number.isInteger(definition?.maxDurability) && definition.maxDurability > 0
        ? definition.maxDurability : null
    };
  });
  for (const positions of Object.values(state.nearbyKeyBlocks)) {
    for (const candidate of positions) {
      const block = bot.blockAt(new Vec3(candidate.x, candidate.y, candidate.z));
      if (!block) continue;
      const p = vector(block.position);
      if (!p) continue;
      const relative = { x: p.x - position.x, y: p.y - position.y, z: p.z - position.z };
      observation.landmarks.push({
        name: block.name, position: p, relative,
        distance: Math.hypot(relative.x, relative.y, relative.z)
      });
    }
  }
  observation.landmarks.sort((a, b) => a.distance - b.distance || a.name.localeCompare(b.name) ||
    a.position.x - b.position.x || a.position.y - b.position.y || a.position.z - b.position.z);
  observation.landmarks = observation.landmarks.slice(0, 24);
  return observation;
}

module.exports = { getObservation };
