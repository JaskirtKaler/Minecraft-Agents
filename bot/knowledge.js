/**
 * Read-only, version-matched game knowledge. Registry drop tables list possible
 * outcomes/conditions, not unconditional guarantees or calibrated probabilities.
 * World experiences belong in memory; player claims cannot overwrite mechanics.
 */
const minecraftData = require('minecraft-data');
const nbt = require('prismarine-nbt');

function dataFor (bot) {
  return minecraftData(bot.version);
}

function normalizeSubject (text) {
  const name = String(text || '').toLowerCase().replace(/^minecraft:/, '').trim().replace(/\s+/g, '_');
  return ({ cobble_stone: 'cobblestone', cobble: 'cobblestone', wood: 'oak_log', oak_wood: 'oak_log' })[name] || name;
}

// Small explicit mechanics table, NOT an action plan or curriculum. Registry
// existence is checked for the actual server version before exposing a rule.
const CROP_ITEMS = Object.freeze({ wheat_seeds: 'wheat', beetroot_seeds: 'beetroots', carrot: 'carrots', potato: 'potatoes' });
const SAPLINGS = ['oak', 'birch', 'spruce', 'jungle', 'acacia', 'dark_oak', 'cherry'].map(name => name + '_sapling');
// The bundled Mojang 1.20.1 minecraft:dirt block tag. Growth conditions are
// distinct from placement: this is not a promise that every sapling will grow.
const SAPLING_SOILS = ['dirt', 'grass_block', 'podzol', 'coarse_dirt', 'mycelium', 'rooted_dirt',
  'moss_block', 'mud', 'muddy_mangrove_roots'];
function plantingRule (bot, name) {
  const data = dataFor(bot);
  const sapling = SAPLINGS.includes(name);
  const resultBlock = sapling ? name : CROP_ITEMS[name];
  if (!resultBlock || !data.itemsByName[name] || !data.blocksByName[resultBlock] || !data.blocksByName.farmland) return null;
  const soils = (sapling ? SAPLING_SOILS : ['farmland']).filter(soil => data.blocksByName[soil]);
  return { item: name, result_block: resultBlock, category: sapling ? 'sapling' : 'crop',
    soils, preparation_soils: sapling ? [] : ['dirt', 'grass_block'], soil: soils[0], face: 'top',
    crop_offset: { x: 0, y: 1, z: 0 },
    note: sapling ? 'Planting is not tree growth; species-specific spacing, light and headroom affect growth.' : 'Verify crop block and inventory change.',
    source: 'explicit placement mechanics; sapling soils from bundled Mojang 1.20.1 dirt tag; names checked against installed registry' };
}

function cropRules (bot) {
  return Object.keys(CROP_ITEMS).map(name => plantingRule(bot, name)).filter(Boolean);
}

function describeSubject (bot, text) {
  const data = dataFor(bot);
  const name = normalizeSubject(text);
  const block = data.blocksByName[name];
  const item = data.itemsByName[name];
  if (!block && !item) return { found: false, name, version: bot.version };
  const sources = (data.blockLootArray || []).filter(entry =>
    entry.drops.some(drop => drop.item === name && !drop.silkTouch)
  ).map(entry => entry.block).slice(0, 12);
  const recipes = item ? (data.recipes[item.id] || []).slice(0, 3).map(recipe => {
    const ids = recipe.ingredients || (recipe.inShape || []).flat();
    const counts = {};
    for (const id of ids) {
      if (id == null || id < 0) continue;
      const ingredient = data.items[id];
      if (ingredient) counts[ingredient.name] = (counts[ingredient.name] || 0) + 1;
    }
    return { ingredients: counts, resultCount: recipe.result.count };
  }) : [];
  return {
    found: true, name, version: bot.version, source: 'installed minecraft-data registry',
    isItem: !!item, isBlock: !!block, planting: plantingRule(bot, name),
    plantedBy: [...Object.keys(CROP_ITEMS), ...SAPLINGS].map(item => plantingRule(bot, item))
      .filter(rule => rule && rule.result_block === name),
    hardness: block ? block.hardness : null,
    diggable: block ? block.diggable : null,
    requiredTools: Object.keys((block && block.harvestTools) || {}).map(id => data.items[id]?.name).filter(Boolean),
    properties: block ? block.states : [],
    dropCandidates: data.blockLoot[name]?.drops || [],
    sources, recipes,
    note: 'Drops depend on tools, enchantments, crop age and server rules; verify inventory changes.'
  };
}

function hasSilkTouch (item) {
  const raw = item.nbt ? nbt.simplify(item.nbt) : {};
  const enchants = [...(item.enchants || []), ...(raw.Enchantments || [])];
  return enchants.some(enchant => String(enchant.name || enchant.id).replace('minecraft:', '') === 'silk_touch');
}

function harvestTool (bot, block) {
  const definition = dataFor(bot).blocksByName[block.name];
  const tools = bot.inventory.items().filter(item => {
    if (hasSilkTouch(item)) return false;
    if (definition.harvestTools && !definition.harvestTools[item.type]) return false;
    if (typeof block.canHarvest === 'function' && !block.canHarvest(item.type)) return false;
    return item.name.endsWith('_pickaxe');
  });
  // Select from actual available tools; do not assume the gift was wooden.
  return tools.sort((a, b) => {
    const speed = tool => typeof block.digTime === 'function' ? block.digTime(tool.type) : 0;
    return speed(a) - speed(b);
  })[0] || null;
}

module.exports = { describeSubject, harvestTool, hasSilkTouch, normalizeSubject, dataFor, plantingRule, cropRules };
