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

module.exports = { describeSubject, harvestTool, hasSilkTouch, normalizeSubject, dataFor };
