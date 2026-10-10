/** Minecraft tools, not a list of approved objectives. The model chooses their composition. */
const { Vec3 } = require('vec3');
const { goals } = require('mineflayer-pathfinder');
const { runOperation } = require('./operations');
const { executeTask } = require('./skills');
const { walkTo } = require('./navigation');
const { dataFor, describeSubject, plantingRule } = require('./knowledge');
const { targetFacts, recipeInfo, recipeBudget, craftingBudget, plantingSites } = require('./grounding');
const { recoverPickup } = require('./pickup');
const { toolTiming } = require('./tool_timing');
const { placeBatch, digBatch, repairBatch, guardConstructionAction, hasConstructionTarget } = require('./construction');

const READ_TOOLS = new Set(['inspect', 'inspect_region', 'recipes', 'craft_budget', 'find_blocks', 'container', 'planting_sites']);
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
function plainPosition (value) { return { x: value.x, y: value.y, z: value.z }; }
function inspectRegionBounds (args) {
  const min = position(args.min);
  const max = position(args.max);
  if (min.x > max.x || min.y > max.y || min.z > max.z) {
    fail('INVALID_REGION', 'min coordinates must be less than or equal to max coordinates.');
  }
  const dimensions = { x: max.x - min.x + 1, y: max.y - min.y + 1, z: max.z - min.z + 1 };
  const cellCount = dimensions.x * dimensions.y * dimensions.z;
  if (!Number.isSafeInteger(cellCount) || cellCount > 4096) {
    fail('REGION_TOO_LARGE', 'inspect_region accepts at most 4096 inclusive cells; split the survey into smaller regions.',
      { min: plainPosition(min), max: plainPosition(max), dimensions, cell_count: cellCount });
  }
  return { min, max, dimensions, cellCount };
}
function inspectRegion (bot, args) {
  // Validate every coordinate before reading a block. This prevents a partial
  // response from being mistaken for a complete survey when one far cell is
  // invalid or outside the tool's existing observation radius.
  const { min, max, dimensions, cellCount } = inspectRegionBounds(args);
  if (!bot.entity || !bot.entity.position) fail('POSITION_UNAVAILABLE', 'The bot position is unavailable for this survey.');
  const positions = [];
  for (let x = min.x; x <= max.x; x++) {
    for (let y = min.y; y <= max.y; y++) {
      for (let z = min.z; z <= max.z; z++) {
        const target = new Vec3(x, y, z);
        if (target.distanceTo(bot.entity.position) > 64) {
          fail('OUT_OF_RANGE', 'Every inspect_region cell must be within the 64-block tool radius; move closer or split the region.',
            { position: plainPosition(target), min: plainPosition(min), max: plainPosition(max) });
        }
        positions.push(target);
      }
    }
  }
  const cells = positions.map(target => {
    const block = bot.blockAt(target);
    if (!block) return { position: plainPosition(target), name: 'unknown', loaded: false };
    const cell = { position: plainPosition(target), name: block.name };
    if (!['air', 'cave_air', 'void_air'].includes(block.name)) cell.properties = block.getProperties?.() || {};
    return cell;
  });
  return { min: plainPosition(min), max: plainPosition(max), dimensions, cell_count: cellCount, cells };
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
async function executeTool (bot, tool, options = {}) {
  const name = tool?.name;
  const args = tool?.args || {};
  // This closure is trusted controller state, not a model-supplied option.
  // Recheck each actual dig, including convenience gathering/staircase tools.
  const context = options.constructionContext;
  if (context) options = { ...options, beforeDig: (raw, block) => {
    guardConstructionAction(raw, { name: name === 'repair_batch' ? 'repair_batch' : 'dig',
      args: { position: plainPosition(block.position), positions: [plainPosition(block.position)] } }, context);
  } };
  if (SKILLS.has(name)) return executeTask(bot, { name, args }, {
    ...toolTiming(tool), ...options, containerConstraint: tool.container_constraint
  });
  try {
    const result = await runOperation(bot, async b => {
      if (context) guardConstructionAction(b, tool, context);
      options.onProgress?.('tool: ' + name);
      if (name === 'inspect') {
        return args.position ? targetFacts(b, loaded(b, args.position)) : describeSubject(b, args.item);
      }
      if (name === 'inspect_region') return inspectRegion(b, args);
      if (['place_batch', 'dig_batch', 'repair_batch'].includes(name)) {
        const batch = name === 'place_batch' ? await placeBatch(b, args, context)
          : name === 'repair_batch' ? await repairBatch(b, args, context) : await digBatch(b, args);
        if (!batch.complete || !batch.verified) fail('BATCH_NOT_COMPLETE',
          'Some requested cells remain unresolved; inspect partial evidence and replan, not completion.', { partial: batch });
        return batch;
      }
      if (name === 'craft_budget') {
        return craftingBudget(b, args.steps, totals(b.inventory.items()));
      }
      if (name === 'planting_sites') return plantingSites(b, args.item, args.radius ?? 24);
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
          recipes: b.recipesAll(item.id, null, true).slice(0, 24).map((r, index) => ({ recipe_index: index,
            ...recipeBudget(b, r, quantity, totals(b.inventory.items())) })),
          craftable_now_with_table: b.recipesFor(item.id, null, quantity, true).slice(0, 4).map(r => recipeInfo(b, r)),
          craftable_now_without_table: b.recipesFor(item.id, null, quantity, null).slice(0, 4).map(r => recipeInfo(b, r)) };
      }
      if (name === 'walk_to') {
        const p = position(args.position);
        loaded(b, p);
        // GetToBlock alone also accepts vertically adjacent nodes in the
        // target's column. That can put the bot inside an AIR planting cell.
        // Adjacent means beside the target, never occupying its column.
        const goal = args.adjacent ? new goals.GoalCompositeAll([
          new goals.GoalGetToBlock(p.x, p.y, p.z),
          new goals.GoalInvert(new goals.GoalXZ(p.x, p.z))
        ]) : new goals.GoalBlock(p.x, p.y, p.z);
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
        const planting = plantingRule(b, args.item);
        if (context && hasConstructionTarget(context, args.position) && !planting &&
            dataFor(b).blocksByName[args.item] && dataFor(b).itemsByName[args.item]) {
          const batch = await placeBatch(b, { item: args.item, positions: [args.position] }, context);
          if (!batch.complete || !batch.verified) fail('BATCH_NOT_COMPLETE',
            'Single structural placement was not verified; inspect partial evidence.', { partial: batch });
          return batch;
        }
        if (planting) {
          const soil = b.blockAt(target.position.offset(0, -1, 0));
          if (!soil || !planting.soils.includes(soil.name)) fail('INVALID_PLANTING_TARGET',
            'Plant into the empty cell immediately above supported observed soil, not into the soil or above another plant.',
            { planting, target: targetFacts(b, target) });
        }
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
        if (context) guardConstructionAction(b, tool, context);
        await b.placeBlock(reference, face);
        if (b.waitForTicks) await b.waitForTicks(2);
        const after = loaded(b, p);
        if (after.name === target.name || count(b, args.item) >= before || planting && after.name !== planting.result_block) {
          fail('PLACE_NOT_VERIFIED', 'Placement was not confirmed by expected block and inventory changes.');
        }
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
          // Supplied by the controller from immutable goal baselines, not a
          // task-specific plan. Recheck against this freshly opened window.
          const constraint = tool.container_constraint;
          if (constraint) {
            const cp = position(constraint.position);
            if (!cp.equals(block.position) || constraint.item !== args.item ||
                !['minimum', 'maximum'].some(key => Number.isInteger(constraint[key]) && constraint[key] >= 0)) {
              fail('INVALID_CONSTRAINT', 'Transfer constraint does not match the observed container/item.');
            }
            const projected = (before[args.item] || 0) + (name === 'deposit' ? quantity : -quantity);
            if (constraint.minimum != null && projected < constraint.minimum ||
                constraint.maximum != null && projected > constraint.maximum) {
              fail('GOAL_QUANTITY_LIMIT', 'Transfer would overshoot the fixed goal; choose the remaining quantity.',
                { before, projected, constraint });
            }
          }
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
    }, { ...toolTiming(tool), ...(name === 'place_batch' ? { progressValue: b => -count(b, args.item) } : {}),
      ...options });
    return { success: true, verified: !['use_on_block'].includes(name), data: result };
  } catch (error) {
    return { success: false, verified: false, status: /NOT_VERIFIED$/.test(error.code || '') || ['CANCELLED', 'TASK_TIMEOUT'].includes(error.code) ? 'unknown' : 'failed',
      message: error.message, data: { ...(error.data || {}), error_code: error.code || 'TOOL_FAILED' } };
  }
}
module.exports = { executeTool, READ_TOOLS };
