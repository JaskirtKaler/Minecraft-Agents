/**
 * Deterministic Mineflayer task skills.
 *
 * This module deliberately accepts typed task objects rather than generated
 * JavaScript. It is safe to call from a bridge handler once the caller has
 * validated / planned a task:
 *
 *   { name: 'mine_logs', args: { item: 'oak_log', count: 3, max_distance: 32 } }
 *   { name: 'give_item', args: { item: 'oak_log', count: 3, recipient: 'Pilot6117' } }
 *   { name: 'mine_and_give', args: { item: 'oak_log', count: 3, recipient: 'Pilot6117' } }
 */

const { goals } = require('mineflayer-pathfinder');
const { runOperation } = require('./operations');
const { mineResource, depositItem, resolveChest } = require('./resources');
const { walkTo, moveToGoal, executeStaircase } = require('./navigation');
const { recoverPickup } = require('./pickup');

const DEFAULT_LOG = 'oak_log';
const DEFAULT_MAX_DISTANCE = 32;
const MAX_MAX_DISTANCE = 128;
const MAX_COUNT = 2304;
const MAX_TASK_DURATION_MS = 180000;
const RESOURCE_NAMES = new Set([
  'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log',
  'mangrove_log', 'cherry_log', 'crimson_stem', 'warped_stem', 'cobblestone', 'dirt'
]);

class TaskError extends Error {
  constructor (code, message, data = {}) {
    super(`[${code}] ${message}`);
    this.name = 'TaskError';
    this.code = code;
    this.data = data;
  }
}

function countEvidence (item, requested, before, after) {
  return {
    item,
    requested,
    requested_count: requested,
    before,
    after,
    observed_before: before,
    observed_after: after
  };
}

function normalizeItemName (value, fallback = DEFAULT_LOG) {
  const item = String(value || fallback).trim().toLowerCase();
  if (!item) {
    throw new TaskError('INVALID_ITEM', 'An item name is required.');
  }
  return item;
}

function isLogName (item) {
  return item.endsWith('_log') || item === 'crimson_stem' || item === 'warped_stem';
}

function positiveInteger (value, field) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new TaskError('INVALID_ARGUMENT', `${field} must be a positive integer.`, { field, value });
  }
  if (value > MAX_COUNT) {
    throw new TaskError('INVALID_ARGUMENT', `${field} may not exceed ${MAX_COUNT}.`, { field, value });
  }
  return value;
}

function maxDistance (value) {
  if (value == null) return DEFAULT_MAX_DISTANCE;
  if (!Number.isSafeInteger(value) || value <= 0 || value > MAX_MAX_DISTANCE) {
    throw new TaskError(
      'INVALID_ARGUMENT',
      `max_distance must be a positive integer no larger than ${MAX_MAX_DISTANCE}.`,
      { field: 'max_distance', value }
    );
  }
  return value;
}

function requireRecipient (value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new TaskError('RECIPIENT_REQUIRED', 'A non-empty recipient username is required for this task.');
  }
  return value.trim();
}

function parseTask (task) {
  if (!task || typeof task !== 'object' || Array.isArray(task)) {
    throw new TaskError('INVALID_TASK', 'Task must be an object with name and args fields.');
  }

  const { name } = task;
  const args = task.args;
  if (name === 'execute_plan') {
    if (!args || !Array.isArray(args.steps) || args.steps.length < 1 || args.steps.length > 12) {
      throw new TaskError('INVALID_PLAN', 'A plan must have 1–12 typed steps.');
    }
    const steps = args.steps.map(step => {
      if (!['mine_logs', 'mine_resource', 'deposit_item', 'give_item'].includes(step?.name)) {
        throw new TaskError('INVALID_PLAN', 'Plans may only compose collection and delivery skills.');
      }
      const parsed = parseTask(step);
      if (parsed.count > 64 || !RESOURCE_NAMES.has(parsed.item)) {
        throw new TaskError('INVALID_PLAN', 'Plan resources must be supported and each count must be 1–64.');
      }
      return parsed;
    });
    return { name, steps };
  }
  if (!['mine_logs', 'mine_resource', 'give_item', 'mine_and_give', 'deposit_item', 'mine_and_deposit', 'escape_staircase'].includes(name)) {
    throw new TaskError('UNKNOWN_TASK', `Unsupported task '${name}'.`, { name });
  }
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    throw new TaskError('INVALID_TASK', 'Task args must be an object.', { name });
  }
  if (name === 'escape_staircase') {
    const rise = args.rise ?? 4;
    if (!Number.isSafeInteger(rise) || rise < 1 || rise > 8) throw new TaskError('INVALID_ARGUMENT', 'Staircase rise must be 1–8 blocks.');
    return { name, rise };
  }

  const count = positiveInteger(args.count, 'count');
  const item = normalizeItemName(args.item);
  const parsed = {
    name,
    item,
    count,
    maxDistance: maxDistance(args.max_distance),
    collectionMode: args.collection_mode ?? 'additional'
  };
  if (!['additional', 'ensure_inventory'].includes(parsed.collectionMode)) {
    throw new TaskError('INVALID_ARGUMENT', 'collection_mode must be additional or ensure_inventory.');
  }

  if (name === 'mine_logs') {
    if (!isLogName(item)) {
      throw new TaskError(
        'INVALID_LOG_ITEM',
        `Task '${name}' requires a log or stem item name, received '${item}'.`,
        { item }
      );
    }
  }
  if (['mine_resource', 'mine_and_give', 'mine_and_deposit'].includes(name) &&
      !isLogName(item) && !['cobblestone', 'dirt'].includes(item)) {
    throw new TaskError('UNSUPPORTED_RESOURCE', 'Verified collection supports logs, dirt, and cobblestone; other resources need a tested skill.', { item });
  }

  if (name === 'give_item' || name === 'mine_and_give') {
    parsed.recipient = requireRecipient(args.recipient);
  }

  return parsed;
}

function inventoryItems (bot) {
  if (!bot || !bot.inventory || typeof bot.inventory.items !== 'function') {
    throw new TaskError('INVENTORY_UNAVAILABLE', 'The bot inventory is not available yet.');
  }
  return bot.inventory.items();
}

function countItem (bot, itemName) {
  return inventoryItems(bot)
    .filter(item => item && item.name === itemName)
    .reduce((total, item) => total + (Number.isSafeInteger(item.count) ? item.count : 0), 0);
}

function firstInventoryItem (bot, itemName) {
  return inventoryItems(bot).find(item => item && item.name === itemName) || null;
}

function assertPathfinder (bot) {
  if (!bot || !bot.pathfinder || typeof bot.pathfinder.goto !== 'function') {
    throw new TaskError('PATHFINDER_UNAVAILABLE', 'The Mineflayer pathfinder is not loaded or ready.');
  }
}

function sleep (milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function waitOneTick (bot) {
  if (typeof bot.waitForTicks !== 'function') {
    await sleep(50);
    return;
  }

  // A lost server connection can leave waitForTicks pending forever. The
  // wall-clock race lets the surrounding skill time out cleanly instead.
  await Promise.race([bot.waitForTicks(1), sleep(1000)]);
}

async function waitForInventoryChange (bot, item, previousCount, direction, maxTicks = 40) {
  for (let tick = 0; tick < maxTicks; tick += 1) {
    const currentCount = countItem(bot, item);
    if ((direction === 'increase' && currentCount > previousCount) ||
        (direction === 'decrease' && currentCount < previousCount)) {
      return currentCount;
    }
    await waitOneTick(bot);
  }
  return countItem(bot, item);
}

async function gotoWithTimeout (bot, goal, timeoutMs) {
  return walkTo(bot, goal, { timeoutMs });
}

function positionKey (position) {
  return `${Math.floor(position.x)},${Math.floor(position.y)},${Math.floor(position.z)}`;
}

function formatAttemptErrors (attemptErrors) {
  if (attemptErrors.length === 0) return '';
  return ` Last attempts: ${attemptErrors.slice(-3).join(' | ')}.`;
}

async function mineLogs (bot, { item, count, maxDistance: searchDistance, collectionMode = 'additional' }, progress = () => {}) {
  assertPathfinder(bot);
  if (typeof bot.findBlocks !== 'function' || typeof bot.blockAt !== 'function') {
    throw new TaskError('WORLD_UNAVAILABLE', 'The bot cannot currently inspect nearby blocks.');
  }
  if (typeof bot.canDigBlock !== 'function' || typeof bot.dig !== 'function') {
    throw new TaskError('DIGGING_UNAVAILABLE', 'The bot does not expose Mineflayer digging APIs.');
  }

  const before = countItem(bot, item);
  const targetAfter = collectionMode === 'ensure_inventory' ? count : before + count;
  const attemptErrors = [];
  const attemptedPositions = new Set();
  let blocksDug = 0;

  while (countItem(bot, item) < targetAfter) {
    let positions;
    try {
      positions = bot.findBlocks({
        matching: block => block && block.name === item,
        maxDistance: searchDistance,
        count: Math.min(256, Math.max(16, (targetAfter - countItem(bot, item)) * 8))
      }) || [];
    } catch (error) {
      throw new TaskError(
        'BLOCK_SEARCH_FAILED',
        `Could not search for ${item}: ${error.message || error}.`,
        countEvidence(item, count, before, countItem(bot, item))
      );
    }

    let madeProgress = false;
    for (const position of positions) {
      if (!position || attemptedPositions.has(positionKey(position))) continue;
      attemptedPositions.add(positionKey(position));

      const block = bot.blockAt(position);
      if (!block || block.name !== item) continue;

      try {
        progress(`walking to ${item}; holding ${countItem(bot, item)}/${targetAfter}`);
        // GetToBlock alone also accepts standing ON the log. Approach beside
        // it so the underfoot-dig safeguard never has to reject that route.
        const p = block.position;
        await gotoWithTimeout(bot, new goals.GoalCompositeAll([
          new goals.GoalGetToBlock(p.x, p.y, p.z),
          new goals.GoalInvert(new goals.GoalXZ(p.x, p.z))
        ]), Math.max(30000, searchDistance * 750));

        if (!bot.canDigBlock(block)) {
          attemptErrors.push(`${positionKey(block.position)} is not reachable for digging`);
          continue;
        }

        const axes = inventoryItems(bot).filter(tool => tool.name.endsWith('_axe'));
        axes.sort((a, b) => typeof block.digTime === 'function' ? block.digTime(a.type) - block.digTime(b.type) : 0);
        if (axes[0] && typeof bot.equip === 'function') await bot.equip(axes[0], 'hand');

        const beforeDig = countItem(bot, item);
        progress('mining and collecting ' + item);
        await bot.dig(block);
        let afterDig = await waitForInventoryChange(bot, item, beforeDig, 'increase');

        if (afterDig <= beforeDig) {
          progress('recovering a dropped ' + item + ' on a safe route');
          afterDig = await recoverPickup(bot, item, beforeDig, block.position);
          if (afterDig <= beforeDig) {
            throw new TaskError('PICKUP_NOT_VERIFIED', `${item} broke but pickup was not confirmed. I stopped before mining more.`,
              countEvidence(item, count, before, countItem(bot, item)));
          }
        }

        blocksDug += 1;
        madeProgress = true;
        break;
      } catch (error) {
        if (['PICKUP_NOT_VERIFIED', 'TASK_TIMEOUT', 'CANCELLED'].includes(error.code)) throw error;
        attemptErrors.push(`${positionKey(block.position)}: ${error.message || error}`);
        console.warn('[Log Route Rejected]', JSON.stringify({ position: block.position,
          code: error.code || 'ROUTE_FAILED', reason: error.message || String(error) }));
      }
    }

    if (!madeProgress) {
      const after = countItem(bot, item);
      throw new TaskError(
        positions.length === 0 ? 'NO_LOGS_FOUND' : 'NO_REACHABLE_LOG',
        positions.length === 0
          ? `No loaded ${item} blocks were found within ${searchDistance} blocks.`
          : `No reachable ${item} block could be mined within ${searchDistance} blocks.${formatAttemptErrors(attemptErrors)}`,
        {
          ...countEvidence(item, count, before, after),
          blocks_dug: blocksDug,
          max_distance: searchDistance
        }
      );
    }
  }

  const after = countItem(bot, item);
  const observedMined = after - before;
  return {
    message: collectionMode === 'ensure_inventory' ? `Holding ${after} ${item}; collected ${observedMined} more.` : `Mined ${observedMined} ${item}.`,
    data: {
      ...countEvidence(item, count, before, after),
      observed_mined: observedMined,
      collection_mode: collectionMode,
      target_count: targetAfter,
      blocks_dug: blocksDug,
      max_distance: searchDistance
    }
  };
}

function findPlayerEntity (bot, username) {
  const direct = bot && bot.players && bot.players[username];
  if (direct && direct.entity) return direct.entity;

  const lowerUsername = username.toLowerCase();
  if (bot && bot.players) {
    const player = Object.entries(bot.players).find(([name, value]) => (
      name.toLowerCase() === lowerUsername && value && value.entity
    ));
    if (player) return player[1].entity;
  }

  if (bot && typeof bot.findPlayer === 'function') {
    const found = bot.findPlayer(username);
    if (Array.isArray(found)) {
      const exact = found.find(entity => entity && entity.username && entity.username.toLowerCase() === lowerUsername);
      if (exact) return exact;
    } else if (found) {
      return found;
    }
  }

  if (bot && bot.entities) {
    return Object.values(bot.entities).find(entity => (
      entity && typeof entity.username === 'string' && entity.username.toLowerCase() === lowerUsername
    )) || null;
  }

  return null;
}

async function giveItem (bot, { item, count, recipient }, progress = () => {}) {
  assertPathfinder(bot);
  let before = countItem(bot, item);
  const evidence = () => countEvidence(item, count, before, countItem(bot, item));

  if (before < count) {
    throw new TaskError(
      'INSUFFICIENT_ITEMS',
      `Cannot give ${count} ${item}; the bot only has ${before}.`,
      { ...evidence(), recipient }
    );
  }

  let target = findPlayerEntity(bot, recipient);
  if (!target || !target.position) {
    throw new TaskError(
      'RECIPIENT_NOT_VISIBLE',
      `Player '${recipient}' is not visible to the bot. Ask them to come within loaded range.`,
      { ...evidence(), recipient }
    );
  }

  let navigation;
  try {
    navigation = await moveToGoal(bot, new goals.GoalNear(
      target.position.x,
      target.position.y,
      target.position.z,
      2
    ), progress);
  } catch (error) {
    if (['TASK_TIMEOUT', 'CANCELLED'].includes(error.code)) throw error;
    throw new TaskError(
      'RECIPIENT_UNREACHABLE',
      `Could not reach '${recipient}' to hand over ${item}: ${error.message || error}.`,
      { ...evidence(), recipient }
    );
  }

  // Resolve once more after walking, since entities can despawn or move out of
  // the loaded area while pathfinding is in progress.
  target = findPlayerEntity(bot, recipient);
  if (!target || !target.position) {
    throw new TaskError(
      'RECIPIENT_NOT_VISIBLE',
      `Player '${recipient}' left visible range before the handoff.`,
      { ...evidence(), recipient }
    );
  }

  // Escaping may collect stone. Measure the toss itself, not the entire trip.
  before = countItem(bot, item);
  if (before < count) throw new TaskError('INSUFFICIENT_ITEMS', 'The items are no longer available for the handoff.', { ...evidence(), recipient });

  const itemStack = firstInventoryItem(bot, item);
  if (!itemStack || !Number.isSafeInteger(itemStack.type)) {
    throw new TaskError(
      'ITEM_METADATA_UNAVAILABLE',
      `Could not resolve the Mineflayer item type for ${item}.`,
      { ...evidence(), recipient }
    );
  }

  try {
    if (typeof bot.toss === 'function') {
      // Mineflayer toss accepts an exact count across matching inventory stacks.
      await bot.toss(itemStack.type, null, count);
    } else if (typeof bot.tossStack === 'function' && itemStack.count === count) {
      // Tossing an entire stack is safe only when it is exactly the requested
      // amount. Failing otherwise prevents accidental over-delivery.
      await bot.tossStack(itemStack);
    } else {
      throw new TaskError(
        'EXACT_TOSS_UNAVAILABLE',
        `The bot cannot safely toss exactly ${count} ${item}.`,
        { ...evidence(), recipient }
      );
    }
  } catch (error) {
    if (error instanceof TaskError) throw error;
    throw new TaskError(
      'TOSS_FAILED',
      `Could not drop ${count} ${item} for '${recipient}': ${error.message || error}.`,
      { ...evidence(), recipient }
    );
  }

  const after = await waitForInventoryChange(bot, item, before, 'decrease');
  if (before - after !== count) {
    throw new TaskError(
      'DELIVERY_NOT_VERIFIED',
      `Expected the inventory to decrease by ${count} ${item}, but it changed from ${before} to ${after}.`,
      { ...evidence(), recipient }
    );
  }

  return {
    message: `Dropped exactly ${count} ${item} near ${recipient}.`,
    data: {
      ...evidence(),
      recipient,
      delivery: 'dropped_near_recipient',
      navigation
    }
  };
}

async function mineAndGive (bot, args, progress = () => {}) {
  const mined = isLogName(args.item) ? await mineLogs(bot, args, progress) : await mineResource(bot, args, progress);
  const given = await giveItem(bot, args, progress);

  return {
    message: `${mined.message} ${given.message}`,
    data: {
      ...countEvidence(args.item, args.count, mined.data.before, given.data.after),
      recipient: args.recipient,
      mine: mined.data,
      give: given.data
    }
  };
}

/**
 * Execute one validated deterministic skill.
 *
 * All failures are returned as structured values so callers can safely relay a
 * useful status message instead of claiming completion based on code execution.
 */
async function executeTask (bot, task, options = {}) {
  let parsed = null;
  let inventoryBefore = null;
  try {
    parsed = parseTask(task);
    inventoryBefore = parsed.item ? countItem(bot, parsed.item) : null;
    const progress = options.onProgress || (() => {});
    const result = await runOperation(bot, async guarded => {
      progress('starting verified ' + parsed.name);
      if (parsed.name === 'execute_plan') {
        const completed = [];
        const deposits = parsed.steps.filter(step => step.name === 'deposit_item');
        const mineStone = parsed.steps.some(step => step.item === 'cobblestone' && step.name === 'mine_resource' &&
          (step.collectionMode !== 'ensure_inventory' || countItem(guarded, step.item) < step.count));
        if (mineStone && !guarded.inventory.items().some(item => item.name.endsWith('_pickaxe') &&
            !require('./knowledge').hasSilkTouch(item))) throw new TaskError('TOOL_REQUIRED', 'This plan needs a pickaxe before any collection starts.');
        let chest;
        if (deposits.length) chest = await resolveChest(guarded, {
          maxDistance: Math.min(...deposits.map(step => step.maxDistance)), items: deposits
        }, progress);
        for (let index = 0; index < parsed.steps.length; index++) {
          const step = parsed.steps[index];
          const stage = phase => progress(`step ${index + 1}/${parsed.steps.length}: ${step.item}: ${phase}`);
          stage(step.name);
          try {
            let result;
            if (step.name === 'mine_logs') result = await mineLogs(guarded, step, stage);
            else if (step.name === 'mine_resource') result = await mineResource(guarded, step, stage);
            else if (step.name === 'give_item') result = await giveItem(guarded, step, stage);
            else result = await depositItem(guarded, step, chest, stage);
            completed.push({ name: step.name, item: step.item, count: step.count, data: result.data });
          } catch (error) {
            error.data = { ...(error.data || {}), completed_steps: completed, failed_step: index + 1, total_steps: parsed.steps.length };
            throw error;
          }
        }
        return { message: `Verified all ${completed.length} resource-plan steps.`, data: { completed_steps: completed, total_steps: completed.length } };
      }
      if (parsed.name === 'escape_staircase') {
        const position = guarded.entity.position;
        const navigation = await executeStaircase(guarded, new goals.GoalY(Math.floor(position.y) + parsed.rise), progress);
        return { message: `Made a controlled staircase ${parsed.rise} blocks up.`, data: navigation };
      }
      if (parsed.name === 'mine_logs') return mineLogs(guarded, parsed, progress);
      if (parsed.name === 'mine_resource') return mineResource(guarded, parsed, progress);
      if (parsed.name === 'give_item') return giveItem(guarded, parsed, progress);
      if (parsed.name === 'mine_and_give') return mineAndGive(guarded, parsed, progress);
      if (parsed.name === 'deposit_item') return depositItem(guarded, parsed, null, progress);
      // Reach/inspect the destination first, using controlled escape if needed.
      const chest = await resolveChest(guarded, parsed, progress);
      const mined = isLogName(parsed.item) ? await mineLogs(guarded, parsed, progress) : await mineResource(guarded, parsed, progress, { baseline: inventoryBefore });
      const deposited = await depositItem(guarded, parsed, chest, progress);
      return { message: mined.message + ' ' + deposited.message, data: {
        item: parsed.item, requested_count: parsed.count, mine: mined.data, deposit: deposited.data
      } };
    }, { timeoutMs: options.timeoutMs ?? MAX_TASK_DURATION_MS, idleTimeoutMs: options.idleTimeoutMs ?? null,
      progressValue: ['mine_logs', 'mine_resource'].includes(parsed.name) ? b => countItem(b, parsed.item) : null });

    return {
      success: true,
      verified: true,
      message: result.message,
      data: result.data
    };
  } catch (error) {
    const taskError = error instanceof TaskError
      ? error
      : new TaskError(error.code || 'TASK_FAILED', error && error.message ? error.message : String(error), error.data || {});
    let evidence = {};
    if (parsed && inventoryBefore != null) {
      try { evidence = countEvidence(parsed.item, parsed.count, inventoryBefore, countItem(bot, parsed.item)); } catch (_) {}
    }

    return {
      success: false,
      verified: false,
      status: ['TASK_TIMEOUT', 'CANCELLED', 'DEPOSIT_NOT_VERIFIED', 'PICKUP_NOT_VERIFIED'].includes(taskError.code) ? 'unknown' : 'failed',
      message: taskError.message,
      data: { ...evidence, ...(taskError.data || {}), error_code: taskError.code }
    };
  }
}

module.exports = { executeTask };
