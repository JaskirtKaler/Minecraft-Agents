const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { executeTool, READ_TOOLS } = require('../bot/tools');

function key (position) {
  return `${position.x},${position.y},${position.z}`;
}

function surveyBot (blocks, calls) {
  return {
    entity: { position: new Vec3(0, 64, 0) },
    blockAt (position) {
      calls.blockAt += 1;
      return blocks.get(key(position)) || null;
    },
    // A region survey must never route, dig, equip, or place.
    pathfinder: { goto: async () => { throw new Error('inspect_region must not navigate'); }, setGoal () {} },
    dig: async () => { throw new Error('inspect_region must not dig'); },
    equip: async () => { throw new Error('inspect_region must not equip'); },
    placeBlock: async () => { throw new Error('inspect_region must not place'); }
  };
}

async function testFullRegionIncludesKnownAirAndExplicitUnloadedCells () {
  const calls = { blockAt: 0 };
  const blocks = new Map([
    ['0,64,0', { name: 'air', position: new Vec3(0, 64, 0), getProperties: () => {
      throw new Error('air properties should not be emitted');
    } }],
    ['1,64,0', { name: 'oak_log', position: new Vec3(1, 64, 0), getProperties: () => ({ axis: 'y' }) }],
    ['1,64,1', { name: 'grass_block', position: new Vec3(1, 64, 1), getProperties: () => ({ snowy: false }) }]
  ]);
  const result = await executeTool(surveyBot(blocks, calls), {
    name: 'inspect_region',
    args: { min: { x: 0, y: 64, z: 0 }, max: { x: 1, y: 64, z: 1 } }
  });

  assert.equal(result.success, true, result.message);
  assert.equal(result.verified, true);
  assert.deepEqual(result.data.min, { x: 0, y: 64, z: 0 });
  assert.deepEqual(result.data.max, { x: 1, y: 64, z: 1 });
  assert.deepEqual(result.data.dimensions, { x: 2, y: 1, z: 2 });
  assert.equal(result.data.cell_count, 4);
  assert.equal(result.data.cells.length, 4);
  assert.equal(calls.blockAt, 4, 'every requested cell is inspected exactly once');

  const cells = new Map(result.data.cells.map(cell => [key(cell.position), cell]));
  assert.deepEqual(cells.get('0,64,0'), { position: { x: 0, y: 64, z: 0 }, name: 'air' });
  assert.deepEqual(cells.get('1,64,0'), {
    position: { x: 1, y: 64, z: 0 }, name: 'oak_log', properties: { axis: 'y' }
  });
  assert.deepEqual(cells.get('0,64,1'), {
    position: { x: 0, y: 64, z: 1 }, name: 'unknown', loaded: false
  });
  assert.deepEqual(cells.get('1,64,1'), {
    position: { x: 1, y: 64, z: 1 }, name: 'grass_block', properties: { snowy: false }
  });
}

async function testAllBoundsAreCheckedBeforeAnyLookup () {
  const outOfRangeCalls = { blockAt: 0 };
  const outOfRange = await executeTool(surveyBot(new Map(), outOfRangeCalls), {
    name: 'inspect_region',
    // The final cell exceeds the radius. A streaming lookup implementation
    // would inspect the preceding cells before noticing it.
    args: { min: { x: 0, y: 64, z: 0 }, max: { x: 65, y: 64, z: 0 } }
  });
  assert.equal(outOfRange.success, false);
  assert.equal(outOfRange.data.error_code, 'OUT_OF_RANGE');
  assert.equal(outOfRangeCalls.blockAt, 0);

  const tooLargeCalls = { blockAt: 0 };
  const tooLarge = await executeTool(surveyBot(new Map(), tooLargeCalls), {
    name: 'inspect_region',
    args: { min: { x: 0, y: 64, z: 0 }, max: { x: 16, y: 79, z: 15 } }
  });
  assert.equal(tooLarge.success, false);
  assert.equal(tooLarge.data.error_code, 'REGION_TOO_LARGE');
  assert.equal(tooLargeCalls.blockAt, 0);

  const invertedCalls = { blockAt: 0 };
  const inverted = await executeTool(surveyBot(new Map(), invertedCalls), {
    name: 'inspect_region',
    args: { min: { x: 2, y: 64, z: 0 }, max: { x: 1, y: 64, z: 0 } }
  });
  assert.equal(inverted.success, false);
  assert.equal(inverted.data.error_code, 'INVALID_REGION');
  assert.equal(invertedCalls.blockAt, 0);
}

async function runRegionInspectionTests () {
  assert(READ_TOOLS.has('inspect_region'));
  await testFullRegionIncludesKnownAirAndExplicitUnloadedCells();
  await testAllBoundsAreCheckedBeforeAnyLookup();
  console.log('✓ bounded read-only region inspection tests passed');
}

if (require.main === module) {
  runRegionInspectionTests().catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = { runRegionInspectionTests };
