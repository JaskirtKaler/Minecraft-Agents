/** Read-only target facts and arithmetic for MODEL-proposed crafting steps. */
const { dataFor, cropRules, plantingRule } = require('./knowledge');
const AIR = new Set(['air', 'cave_air', 'void_air']);

function blockFact (block) {
  return block ? { name: block.name, position: block.position, properties: block.getProperties?.() || {},
    diggable: block.diggable, boundingBox: block.boundingBox } : null;
}

function targetFacts (bot, block) {
  const p = block.position;
  const below = bot.blockAt(p.offset(0, -1, 0));
  const above = bot.blockAt(p.offset(0, 1, 0));
  return { ...blockFact(block), below: blockFact(below), above: blockFact(above),
    neighborhoodLoaded: !!below && !!above };
}

function farmingTargets (bot, radius = 16) {
  if (!bot.findBlocks || !bot.blockAt) return [];
  return (bot.findBlocks({ matching: block => block?.name === 'farmland', maxDistance: radius, count: 8 }) || [])
    .map(p => {
      const soil = bot.blockAt(p);
      if (soil?.name !== 'farmland') return null;
      const cropPosition = p.offset(0, 1, 0);
      const occupant = bot.blockAt(cropPosition);
      const feet = bot.entity?.position;
      const occupiedByBot = !!feet && Math.floor(feet.x) === cropPosition.x && Math.floor(feet.z) === cropPosition.z &&
        [Math.floor(feet.y), Math.floor(feet.y) + 1].includes(cropPosition.y);
      return { soil: blockFact(soil), planting_position: cropPosition, occupant: blockFact(occupant),
        planting_cell: !occupant ? 'unknown' : AIR.has(occupant.name) ? 'empty' : 'occupied',
        occupied_by_bot: occupiedByBot,
        valid_items: cropRules(bot).map(rule => ({ item: rule.item, resulting_block: rule.result_block })) };
    }).filter(Boolean);
}

function plantingSites (bot, item, radius = 24) {
  const rule = plantingRule(bot, item);
  if (!rule) throw new Error('No supported planting rule for ' + item + '; inspect the item first.');
  if (!Number.isInteger(radius) || radius < 1 || radius > 32) throw new Error('Planting search radius must be 1–32.');
  const positions = bot.findBlocks({ matching: block => block && rule.soils.includes(block.name),
    useExtraInfo: block => block && AIR.has(bot.blockAt(block.position.offset(0, 1, 0))?.name),
    maxDistance: radius, count: 12 }) || [];
  return { rule, sites: positions.map(p => {
    const soil = bot.blockAt(p), plantingPosition = p.offset(0, 1, 0), cell = bot.blockAt(plantingPosition);
    if (!soil || !rule.soils.includes(soil.name) || !cell || !AIR.has(cell.name)) return null;
    return { soil: blockFact(soil), planting_position: plantingPosition, occupant: blockFact(cell),
      above: blockFact(bot.blockAt(plantingPosition.offset(0, 1, 0))) };
  }).filter(Boolean), complete: false,
  note: 'Loaded candidates only; you choose sites/spacing and approach. Not a reachability or growth guarantee.' };
}

function recipeInfo (bot, recipe) {
  const data = dataFor(bot);
  const ingredients = {};
  for (const entry of recipe.delta) if (entry.count < 0) {
    const name = data.items[entry.id]?.name || String(entry.id);
    ingredients[name] = (ingredients[name] || 0) - entry.count;
  }
  return { ingredients, output: recipe.result.count, requires_table: recipe.requiresTable };
}

function recipeBudget (bot, recipe, requested, inventory) {
  const info = recipeInfo(bot, recipe);
  const rounds = Math.ceil(requested / info.output);
  const effects = {};
  for (const entry of recipe.delta) {
    const name = dataFor(bot).items[entry.id]?.name || String(entry.id);
    effects[name] = (effects[name] || 0) + entry.count * rounds;
  }
  const needed = Object.fromEntries(Object.entries(info.ingredients).map(([name, n]) => [name, n * rounds]));
  const shortfalls = Object.fromEntries(Object.entries(needed).filter(([name, n]) => (inventory[name] || 0) < n)
    .map(([name, n]) => [name, n - (inventory[name] || 0)]));
  const projected = { ...inventory };
  for (const [name, delta] of Object.entries(effects)) projected[name] = (projected[name] || 0) + delta;
  return { ...info, requested_output: requested, rounds, produced_output: rounds * info.output,
    surplus_output: rounds * info.output - requested, ingredients_needed: needed, shortfalls,
    inventory_after: projected, note: 'Arithmetic only; station, reach, and server execution are not guaranteed.' };
}

function craftingBudget (bot, steps, initial) {
  if (!Array.isArray(steps) || !steps.length || steps.length > 16) throw new Error('Supply 1–16 proposed crafting steps.');
  const data = dataFor(bot);
  let inventory = { ...initial };
  const previews = [];
  for (const step of steps) {
    if (!step || typeof step.item !== 'string' || !Number.isInteger(step.count) || step.count < 1 || step.count > 64) {
      throw new Error('Each proposed step needs an item and output count 1–64.');
    }
    const item = data.itemsByName[step.item];
    if (!item) throw new Error('Unknown crafting item: ' + step.item);
    const recipes = bot.recipesAll(item.id, null, true);
    if (!recipes.length) throw new Error('No installed crafting recipe for ' + step.item);
    const budgets = recipes.map(recipe => recipeBudget(bot, recipe, step.count, inventory));
    // Match the first feasible material variant; never generate prerequisites.
    const selected = step.recipe_index ?? Math.max(0, budgets.findIndex(b => !Object.keys(b.shortfalls).length));
    if (!Number.isInteger(selected) || selected < 0 || selected >= recipes.length) throw new Error('Invalid recipe_index.');
    const budget = budgets[selected];
    previews.push({ item: step.item, recipe_index: selected, ...budget });
    if (Object.keys(budget.shortfalls).length) {
      return { feasible_materials: false, steps: previews, inventory_after: inventory,
        note: 'Stopped at the first material shortfall; no actions executed and no dependencies invented.' };
    }
    inventory = budget.inventory_after;
  }
  return { feasible_materials: true, steps: previews, inventory_after: inventory,
    note: 'Preview of your proposed steps only. Required stations may still need placement; no actions executed.' };
}

module.exports = { blockFact, targetFacts, farmingTargets, plantingSites, recipeInfo, recipeBudget, craftingBudget };
