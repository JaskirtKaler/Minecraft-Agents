const assert = require('assert/strict');
const { Vec3 } = require('../bot/node_modules/vec3');
const { Recipe } = require('../bot/node_modules/prismarine-recipe')('1.20.1');
const data = require('../bot/node_modules/minecraft-data')('1.20.1');
const { describeSubject } = require('../bot/knowledge');
const { targetFacts, farmingTargets, craftingBudget, plantingSites } = require('../bot/grounding');
const { executeTool } = require('../bot/tools');

async function runGroundingTests () {
  const recipes = id => Recipe.find(id);
  const bot = { version: '1.20.1', recipesAll: id => recipes(id),
    inventory: { items: () => [{ name: 'oak_log', count: 3 }] } };
  const seed = describeSubject(bot, 'wheat_seeds');
  assert.equal(seed.isItem, true);
  assert.equal(seed.isBlock, false);
  assert.equal(seed.planting.result_block, 'wheat');
  assert.equal(describeSubject(bot, 'wheat').isBlock, true);
  for (const [name, crop] of [['carrot', 'carrots'], ['potato', 'potatoes'], ['beetroot_seeds', 'beetroots']]) {
    assert.equal(describeSubject(bot, name).planting.result_block, crop);
  }
  const steps = [{ item: 'oak_planks', count: 8 }, { item: 'stick', count: 4 }];
  const budget = craftingBudget(bot, steps, { oak_log: 3 });
  assert.equal(budget.feasible_materials, true);
  assert.equal(budget.inventory_after.oak_planks, 6);
  assert.equal(budget.inventory_after.stick, 4);
  assert.equal(budget.steps[0].rounds, 2);
  assert.equal(budget.steps[1].ingredients_needed.oak_planks, 2);
  assert.deepEqual(bot.inventory.items(), [{ name: 'oak_log', count: 3 }]);
  const overshoot = craftingBudget(bot, [{ item: 'oak_planks', count: 2 }], { oak_log: 1 });
  assert.equal(overshoot.steps[0].produced_output, 4);
  assert.equal(overshoot.steps[0].surplus_output, 2);
  const missing = craftingBudget(bot, [{ item: 'stick', count: 4 }], {});
  assert.equal(missing.feasible_materials, false);
  assert.equal(missing.steps[0].shortfalls.oak_planks, 2);
  assert.throws(() => craftingBudget(bot, [], {}));
  assert.throws(() => craftingBudget(bot, [{ item: 'stick', count: true }], {}));
  assert.throws(() => craftingBudget(bot, [{ item: 'stick', count: 4, recipe_index: 999 }], {}));
  const query = await executeTool(bot, { name: 'craft_budget', args: { steps } });
  assert.equal(query.verified, true, query.message);
  assert.equal(query.data.inventory_after.oak_planks, 6);

  for (const soilY of [62, 64, 67]) {
    const soilPosition = new Vec3(2, soilY, 0);
    const cropPosition = soilPosition.offset(0, 1, 0);
    let crop = 'air', seeds = 2, placements = 0;
    const farmBot = { ...bot, entity: { position: new Vec3(0, soilY + 1, 0) },
      inventory: { items: () => [{ name: 'wheat_seeds', count: seeds }] },
      findBlocks: () => [soilPosition],
      blockAt: p => ({ name: p.equals(soilPosition) ? 'farmland' : p.equals(cropPosition) ? crop : 'air',
        position: p, boundingBox: p.equals(soilPosition) ? 'block' : 'empty', getProperties: () => ({}) }),
      equip: async () => {}, waitForTicks: async () => {},
      placeBlock: async (reference, face) => {
        assert.deepEqual(reference.position, soilPosition);
        assert.deepEqual(face, new Vec3(0, 1, 0));
        crop = 'wheat'; seeds--; placements++;
      }
    };
    const targets = farmingTargets(farmBot);
    assert.deepEqual(targets[0].planting_position, cropPosition);
    assert.equal(targets[0].planting_cell, 'empty');
    assert.equal(targets[0].occupied_by_bot, false);
    farmBot.entity.position = cropPosition.offset(0.5, -0.0625, 0.5);
    assert.equal(farmingTargets(farmBot)[0].occupied_by_bot, true);
    farmBot.entity.position = new Vec3(0, soilY + 1, 0);
    assert.equal(targetFacts(farmBot, farmBot.blockAt(cropPosition)).below.name, 'farmland');
    const wrongHeight = await executeTool(farmBot, { name: 'place', args: { item: 'wheat_seeds', position: cropPosition.offset(0, 1, 0) } });
    assert.equal(wrongHeight.data.error_code, 'INVALID_PLANTING_TARGET');
    assert.equal(placements, 0);
    const planted = await executeTool(farmBot, { name: 'place', args: { item: 'wheat_seeds', position: cropPosition } });
    assert.equal(planted.verified, true, planted.message);
    assert.equal(planted.data.name, 'wheat');
    assert.equal(farmingTargets(farmBot)[0].planting_cell, 'occupied');
    const occupied = await executeTool(farmBot, { name: 'place', args: { item: 'wheat_seeds', position: cropPosition } });
    assert.equal(occupied.data.error_code, 'OCCUPIED_BLOCK');
    assert.equal(placements, 1);
    const realBlockAt = farmBot.blockAt;
    farmBot.blockAt = p => p.equals(cropPosition) ? null : realBlockAt(p);
    assert.equal(farmingTargets(farmBot)[0].planting_cell, 'unknown');
  }
  let walkingGoal;
  const walkingBot = { ...bot, entity: { position: new Vec3(0, 65, 0) },
    blockAt: p => ({ name: 'air', position: p }),
    pathfinder: { goto: async goal => {
      walkingGoal = goal;
      const nodes = [new Vec3(5, 65, 4), new Vec3(6, 65, 4), new Vec3(5, 65, 5)];
      const reached = nodes.find(node => goal.isEnd(node));
      assert(reached, 'Test route needs a node that satisfies the requested goal.');
      walkingBot.entity.position = reached;
    } } };
  const walkArgs = { position: { x: 5, y: 65, z: 4 }, adjacent: true };
  assert.equal((await executeTool(walkingBot, { name: 'walk_to', args: walkArgs })).success, true);
  assert.equal(walkingGoal.isEnd({ x: 5, y: 65, z: 4 }), false);
  assert.equal(walkingGoal.isEnd({ x: 5, y: 64, z: 4 }), false);
  assert.equal(walkingGoal.isEnd({ x: 5, y: 63, z: 4 }), false);
  assert.equal(walkingGoal.isEnd({ x: 6, y: 65, z: 4 }), true);
  assert.equal(walkingGoal.isEnd({ x: 5, y: 65, z: 5 }), true);
  assert.equal((await executeTool(walkingBot, { name: 'walk_to', args: { ...walkArgs, adjacent: false } })).success, true);
  assert.equal(walkingGoal.isEnd({ x: 5, y: 65, z: 4 }), true);
  const treeRule = describeSubject(bot, 'oak_sapling').planting;
  assert.equal(treeRule.result_block, 'oak_sapling');
  assert.ok(treeRule.soils.includes('grass_block'));
  assert.ok(describeSubject(bot, 'oak_sapling').plantedBy.length);
  let tree = 'air', saplings = 2;
  const soil = new Vec3(2, 67, 0), cell = soil.offset(0, 1, 0);
  const treeBot = { ...bot, entity: { position: new Vec3(0, 68, 0) },
    inventory: { items: () => [{ name: 'oak_sapling', count: saplings }] }, findBlocks: () => [soil],
    blockAt: p => ({ name: p.equals(soil) ? 'grass_block' : p.equals(cell) ? tree : 'air',
      position: p, boundingBox: p.equals(soil) ? 'block' : 'empty' }), equip: async () => {}, waitForTicks: async () => {},
    placeBlock: async reference => { assert.deepEqual(reference.position, soil); tree = 'oak_sapling'; saplings--; } };
  assert.deepEqual(plantingSites(treeBot, 'oak_sapling').sites[0].planting_position, cell);
  const plantedTree = await executeTool(treeBot, { name: 'place', args: { item: 'oak_sapling', position: cell } });
  assert.equal(plantedTree.verified, true, plantedTree.message);
  assert.equal(plantedTree.data.name, 'oak_sapling');
  assert.equal(saplings, 1);
  assert.equal(plantingSites(treeBot, 'oak_sapling').sites.length, 0);
  console.log('✓ crop/soil targets, unknown cells, planting checks, recipe batches and proposed-plan budgets passed');
}

if (require.main === module) runGroundingTests().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { runGroundingTests };
