/** Minecraft tools, not a list of approved objectives. The model chooses their composition. */
const { Vec3 } = require('vec3');
const { goals } = require('mineflayer-pathfinder');
const { runOperation } = require('./operations');
const { executeTask } = require('./skills');
const { walkTo } = require('./navigation');
const { dataFor, describeSubject } = require('./knowledge');
const { recoverPickup } = require('./pickup');

const READ_TOOLS = new Set(['inspect', 'recipes', 'find_blocks', 'container']);
const SKILLS = new Set(['mine_logs', 'mine_resource', 'give_item', 'deposit_item', 'escape_staircase']);
const count = (bot, name) => bot.inventory.items().filter(item => item.name === name).reduce((n, item) => n + item.count, 0);
function fail (code, message, data = {}) { throw Object.assign(new Error(message), { code, data }); }
function amount (value = 1) {
  if (!Number.isInteger(value) || value < 1 || value > 64) fail('INVALID_ARGUMENT', 'One tool call may process 1–64 items; split larger work into calls.');
  return value;
}
function position (value) {
  if (!value || !['x', 'y', 'z'].every(axis => Number.isInteger(value[axis]))) fail('INVALID_ARGUMENT', 'position needs integer x, y, z.');
  return new Vec3(value.x, value.y, value.z);
}
function loaded (bot, value) {
  const p = position(value);
  if (p.distanceTo(bot.entity.position) > 64) fail('OUT_OF_RANGE', 'Inspect/move closer first; target is outside the 64-block tool radius.');
  const block = bot.blockAt(p);
  if (!block) fail('UNLOADED_BLOCK', 'This block is not loaded.');
  return block;
}
function totals (items) {
  const result = {};
  for (const item of items) if (item) result[item.name] = (result[item.name] || 0) + item.count;
  return result;
}
function describeBlock (block) {
  return { name: block.name, position: block.position, properties: block.getProperties?.() || {},
    diggable: block.diggable, boundingBox: block.boundingBox };
}
async function approach (bot, block) {
  await walkTo(bot, new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z));
  return loaded(bot, block.position);
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

async function executeTool (bot, tool, options = {}) {
  const name = tool?.name;
  const args = tool?.args || {};
  if (SKILLS.has(name)) return executeTask(bot, { name, args }, { ...options, timeoutMs: options.timeoutMs ?? 60000 });
  try {
    const result = await runOperation(bot, async b => {
      options.onProgress?.('tool: ' + name);
      if (name === 'inspect') {
        return args.position ? describeBlock(loaded(b, args.position)) : describeSubject(b, args.item);
      }
      if (name === 'find_blocks') {
        const names = Array.isArray(args.names) ? args.names : [args.name];
        if (!names.length || names.length > 16 || !names.every(n => typeof n === 'string')) fail('INVALID_ARGUMENT', 'Supply 1–16 block names.');
        const radius = args.radius ?? 32;
        if (!Number.isInteger(radius) || radius < 1 || radius > 64) fail('INVALID_ARGUMENT', 'radius must be 1–64.');
        return { blocks: (b.findBlocks({ matching: block => block && names.includes(block.name),
          maxDistance: radius, count: 24 }) || []).map(p => describeBlock(b.blockAt(p))).filter(Boolean) };
      }
      if (name === 'recipes') {
        const item = dataFor(b).itemsByName[args.item];
        if (!item) fail('UNKNOWN_ITEM', 'Unknown item: ' + args.item);
        const quantity = amount(args.count);
        return { item: item.name, requested_output: quantity,
          recipes: b.recipesAll(item.id, null, true).slice(0, 24).map(r => recipeInfo(b, r)),
          craftable_now_with_table: b.recipesFor(item.id, null, quantity, true).slice(0, 4).map(r => recipeInfo(b, r)),
          craftable_now_without_table: b.recipesFor(item.id, null, quantity, null).slice(0, 4).map(r => recipeInfo(b, r)) };
      }
      if (name === 'walk_to') {
        const p = position(args.position);
        loaded(b, p);
        const goal = args.adjacent ? new goals.GoalGetToBlock(p.x, p.y, p.z) : new goals.GoalBlock(p.x, p.y, p.z);
        await walkTo(b, goal);
        return { position: b.entity.position };
      }
      if (name === 'equip') {
        const item = b.inventory.items().find(i => i.name === args.item);
        if (!item) fail('MISSING_ITEM', 'Not carrying ' + args.item);
        await b.equip(item, 'hand');
        return { held: args.item };
      }
      if (name === 'craft') {
        const item = dataFor(b).itemsByName[args.item];
        if (!item) fail('UNKNOWN_ITEM', 'Unknown item: ' + args.item);
        const quantity = amount(args.count);
        let table = null;
        if (args.table) {
          table = await approach(b, loaded(b, args.table));
          if (table.name !== 'crafting_table') fail('NOT_CRAFTING_TABLE', 'The selected block is ' + table.name);
        }
        const freshCount = async () => {
          if (!table || !b.openBlock) return count(b, item.name);
          // Reopening the server's crafting window supplies a fresh player
          // inventory snapshot instead of optimistic/stale close-window copies.
          const window = await b.openBlock(table);
          try {
            return totals(window.slots.slice(window.inventoryStart, window.inventoryEnd))[item.name] || 0;
          } finally {
            window.close();
            // Let the server settle close-window inventory/cursor handling
            // before another recipe opens a window and consumes materials.
            if (b.waitForTicks) await b.waitForTicks(2);
          }
        };
        const before = await freshCount();
        const recipes = b.recipesFor(item.id, null, quantity, table);
        if (!recipes.length) fail('CRAFT_REQUIREMENTS', 'Materials or crafting table are missing. Inspect recipes and inventory before retrying.',
          { recipes: b.recipesAll(item.id, null, true).slice(0, 8).map(r => recipeInfo(b, r)) });
        const recipe = recipes[0];
        const rounds = Math.ceil(quantity / recipe.result.count);
        const expected = rounds * recipe.result.count;
        // One recipe execution per guarded call: a revoked operation cannot
        // start another round. An already-issued craft may have partial effects.
        let observed = before;
        for (let round = 0; round < rounds; round++) {
          await b.craft(recipe, 1, table);
          // A close/open sequence needs the server's final inventory updates
          // before the next guarded recipe execution uses a fresh window.
          if (b.waitForTicks) await b.waitForTicks(2);
          const next = await freshCount();
          if (next - observed !== recipe.result.count) fail('CRAFT_NOT_VERIFIED', 'One recipe execution did not produce the expected fresh inventory delta; stopped before another craft.',
            { before, after: next, expected, completed_rounds: round });
          observed = next;
        }
        if (b.waitForTicks) await b.waitForTicks(2);
        const after = observed;
        if (after - before !== expected) fail('CRAFT_NOT_VERIFIED', 'Crafting inventory delta differs from recipe output.', { before, after, expected });
        return { item: item.name, before, after, produced: expected, recipe: recipeInfo(b, recipe) };
      }
      if (name === 'place') {
        const target = loaded(b, args.position);
        if (!['air', 'cave_air', 'void_air'].includes(target.name)) fail('OCCUPIED_BLOCK', 'Placement cell is occupied by ' + target.name);
        const p = target.position;
        const feet = b.entity.position;
        if (Math.floor(feet.x) === p.x && Math.floor(feet.z) === p.z && [Math.floor(feet.y), Math.floor(feet.y) + 1].includes(p.y)) {
          fail('OCCUPIED_BY_BOT', 'Walk out of the placement cell first.');
        }
        const item = b.inventory.items().find(i => i.name === args.item);
        if (!item) fail('MISSING_ITEM', 'Not carrying ' + args.item);
        let reference, face;
        for (const vector of [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(0, -1, 0)]) {
          const block = b.blockAt(p.minus(vector));
          if (block?.boundingBox === 'block') { reference = block; face = vector; break; }
        }
        if (!reference) fail('NO_PLACEMENT_SUPPORT', 'No loaded solid reference block supports placement.');
        if (p.distanceTo(b.entity.position) > 4.5) fail('OUT_OF_REACH', 'walk_to an adjacent cell before placing.');
        const before = count(b, args.item);
        await b.equip(item, 'hand');
        await b.placeBlock(reference, face);
        if (b.waitForTicks) await b.waitForTicks(2);
        const after = loaded(b, p);
        if (after.name === target.name || count(b, args.item) >= before) fail('PLACE_NOT_VERIFIED', 'Placement was not confirmed by block and inventory changes.');
        return { ...describeBlock(after), inventory_before: before, inventory_after: count(b, args.item) };
      }
      if (name === 'dig') {
        const target = loaded(b, args.position);
        // Reachable headroom/obstruction can be cleared without first finding
        // a walking route through it. Let Minecraft's reach check decide.
        const block = b.canDigBlock(target) ? target : await approach(b, target);
        if (!block.diggable || !b.canDigBlock(block)) fail('CANNOT_DIG', 'Block is unbreakable or outside dig reach.');
        if (args.tool) {
          const tool = b.inventory.items().find(i => i.name === args.tool);
          if (!tool) fail('MISSING_ITEM', 'Not carrying ' + args.tool);
          await b.equip(tool, 'hand');
        }
        const before = args.collect_item ? count(b, args.collect_item) : null;
        await b.dig(block);
        if (b.waitForTicks) await b.waitForTicks(2);
        const after = loaded(b, block.position);
        if (after.name === block.name) fail('DIG_NOT_VERIFIED', 'The target block did not change.');
        if (args.collect_item) {
          const held = await recoverPickup(b, args.collect_item, before, block.position);
          if (held <= before) fail('PICKUP_NOT_VERIFIED', 'The block changed, but the requested drop was not collected.');
        }
        return { broken: block.name, replacement: after.name, position: block.position };
      }
      if (name === 'use_on_block') {
        const block = await approach(b, loaded(b, args.position));
        const item = b.inventory.items().find(i => i.name === args.item);
        if (!item) fail('MISSING_ITEM', 'Not carrying ' + args.item);
        await b.equip(item, 'hand');
        await b.activateBlock(block);
        if (b.waitForTicks) await b.waitForTicks(3);
        return { before: describeBlock(block), after: describeBlock(loaded(b, block.position)),
          note: 'Interaction sent; verify the intended resulting block state.' };
      }
      if (name === 'container' || name === 'withdraw' || name === 'deposit') {
        const block = await approach(b, loaded(b, args.position));
        if (!['chest', 'trapped_chest', 'barrel'].includes(block.name)) fail('NOT_CONTAINER', 'Selected block is not a supported container.');
        const window = await b.openContainer(block);
        try {
          const before = totals(window.containerItems());
          if (name === 'container') return { position: block.position, items: before };
          const item = dataFor(b).itemsByName[args.item];
          if (!item) fail('UNKNOWN_ITEM', 'Unknown item: ' + args.item);
          const quantity = amount(args.count);
          const playerBefore = totals(window.slots.slice(window.inventoryStart, window.inventoryEnd));
          await window[name](item.id, null, quantity);
          window.close();
          if (b.waitForTicks) await b.waitForTicks(2);
          const verify = await b.openContainer(block);
          try {
            const after = totals(verify.containerItems());
            const playerAfter = totals(verify.slots.slice(verify.inventoryStart, verify.inventoryEnd));
            const delta = (after[args.item] || 0) - (before[args.item] || 0);
            const playerDelta = (playerAfter[args.item] || 0) - (playerBefore[args.item] || 0);
            const expected = name === 'deposit' ? quantity : -quantity;
            if (delta !== expected || playerDelta !== -expected) fail('TRANSFER_NOT_VERIFIED', 'Fresh container and player counts did not confirm the transfer.', { before, after, player_before: playerBefore, player_after: playerAfter });
            return { before, after, player_before: playerBefore, player_after: playerAfter, item: args.item, quantity, direction: name };
          } finally { verify.close(); }
        } finally { window.close(); }
      }
      fail('UNKNOWN_TOOL', 'Unknown Minecraft tool: ' + name);
    }, { timeoutMs: options.timeoutMs ?? 60000 });
    return { success: true, verified: !['use_on_block'].includes(name), data: result };
  } catch (error) {
    return { success: false, verified: false, status: /NOT_VERIFIED$/.test(error.code || '') || ['CANCELLED', 'TASK_TIMEOUT'].includes(error.code) ? 'unknown' : 'failed',
      message: error.message, data: { ...(error.data || {}), error_code: error.code || 'TOOL_FAILED' } };
  }
}
module.exports = { executeTool, READ_TOOLS };
