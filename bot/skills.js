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

const DEFAULT_LOG = 'oak_log';
const DEFAULT_MAX_DISTANCE = 32;
const MAX_MAX_DISTANCE = 128;
const MAX_COUNT = 2304;
const MAX_TASK_DURATION_MS = 180000;

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
  if (!['mine_logs', 'give_item', 'mine_and_give'].includes(name)) {
    throw new TaskError('UNKNOWN_TASK', `Unsupported task '${name}'.`, { name });
  }
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    throw new TaskError('INVALID_TASK', 'Task args must be an object.', { name });
  }

  const count = positiveInteger(args.count, 'count');
  const item = normalizeItemName(args.item);
  const parsed = {
    name,
    item,
    count,
    maxDistance: maxDistance(args.max_distance)
  };

  if (name === 'mine_logs' || name === 'mine_and_give') {
    if (!isLogName(item)) {
      throw new TaskError(
        'INVALID_LOG_ITEM',
        `Task '${name}' requires a log or stem item name, received '${item}'.`,
        { item }
      );
    }
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
  let timeoutId;
  const pathPromise = bot.pathfinder.goto(goal);
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      try {
        if (typeof bot.pathfinder.setGoal === 'function') bot.pathfinder.setGoal(null);
      } catch (_) {
        // The timeout error below is still the actionable result.
      }
      reject(new TaskError('NAVIGATION_TIMEOUT', `Could not reach the target within ${Math.ceil(timeoutMs / 1000)} seconds.`));
    }, timeoutMs);
  });

  try {
    await Promise.race([pathPromise, timeoutPromise]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function runWithTaskTimeout (bot, work) {
  let timeoutId;
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      try {
        if (bot.pathfinder && typeof bot.pathfinder.setGoal === 'function') bot.pathfinder.setGoal(null);
      } catch (_) {
        // The timeout error below is still the actionable result.
      }
      reject(new TaskError('TASK_TIMEOUT', `Task exceeded ${MAX_TASK_DURATION_MS / 1000} seconds and was stopped.`));
    }, MAX_TASK_DURATION_MS);
  });

  try {
    return await Promise.race([work, timeoutPromise]);
  } finally {
    clearTimeout(timeoutId);
  }
}

function positionKey (position) {
  return `${Math.floor(position.x)},${Math.floor(position.y)},${Math.floor(position.z)}`;
}

function formatAttemptErrors (attemptErrors) {
  if (attemptErrors.length === 0) return '';
  return ` Last attempts: ${attemptErrors.slice(-3).join(' | ')}.`;
}

async function mineLogs (bot, { item, count, maxDistance: searchDistance }) {
  assertPathfinder(bot);
  if (typeof bot.findBlocks !== 'function' || typeof bot.blockAt !== 'function') {
    throw new TaskError('WORLD_UNAVAILABLE', 'The bot cannot currently inspect nearby blocks.');
  }
  if (typeof bot.canDigBlock !== 'function' || typeof bot.dig !== 'function') {
    throw new TaskError('DIGGING_UNAVAILABLE', 'The bot does not expose Mineflayer digging APIs.');
  }

  const before = countItem(bot, item);
  const targetAfter = before + count;
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
        // GoalGetToBlock ends adjacent to the solid log; GoalBlock would ask
        // pathfinder to stand inside the log and is therefore not appropriate.
        await gotoWithTimeout(bot, new goals.GoalGetToBlock(
          block.position.x,
          block.position.y,
          block.position.z
        ), Math.max(30000, searchDistance * 750));

        if (!bot.canDigBlock(block)) {
          attemptErrors.push(`${positionKey(block.position)} is not reachable for digging`);
          continue;
        }

        const beforeDig = countItem(bot, item);
        await bot.dig(block);
        const afterDig = await waitForInventoryChange(bot, item, beforeDig, 'increase');

        if (afterDig <= beforeDig) {
          attemptErrors.push(`${positionKey(block.position)} was broken but no ${item} entered inventory`);
          continue;
        }

        blocksDug += 1;
        madeProgress = true;
        break;
      } catch (error) {
        attemptErrors.push(`${positionKey(block.position)}: ${error.message || error}`);
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
    message: `Mined ${observedMined} ${item}.`,
    data: {
      ...countEvidence(item, count, before, after),
      observed_mined: observedMined,
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

async function giveItem (bot, { item, count, recipient }) {
  assertPathfinder(bot);
  const before = countItem(bot, item);
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

  try {
    await gotoWithTimeout(bot, new goals.GoalNear(
      target.position.x,
      target.position.y,
      target.position.z,
      2
    ), 45000);
  } catch (error) {
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
      delivery: 'dropped_near_recipient'
    }
  };
}

async function mineAndGive (bot, args) {
  const mined = await mineLogs(bot, args);
  const given = await giveItem(bot, args);

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
async function executeTask (bot, task) {
  try {
    const parsed = parseTask(task);
    let result;

    if (parsed.name === 'mine_logs') {
      result = await runWithTaskTimeout(bot, mineLogs(bot, parsed));
    } else if (parsed.name === 'give_item') {
      result = await runWithTaskTimeout(bot, giveItem(bot, parsed));
    } else {
      result = await runWithTaskTimeout(bot, mineAndGive(bot, parsed));
    }

    return {
      success: true,
      verified: true,
      message: result.message,
      data: result.data
    };
  } catch (error) {
    const taskError = error instanceof TaskError
      ? error
      : new TaskError('TASK_FAILED', error && error.message ? error.message : String(error));

    return {
      success: false,
      verified: false,
      message: taskError.message,
      data: taskError.data || {}
    };
  }
}

module.exports = { executeTask };
