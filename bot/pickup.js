/** Bounded recovery for a known mined drop, using walking only. */
const { goals } = require('mineflayer-pathfinder');
const { pathCost, walkTo } = require('./navigation');

const count = (bot, name) => bot.inventory.items().filter(item => item.name === name).reduce((n, item) => n + item.count, 0);

async function recoverPickup (bot, name, previous, minedPosition) {
  if (count(bot, name) > previous) return count(bot, name);
  const drop = Object.values(bot.entities || {}).filter(entity => {
    try {
      return entity.getDroppedItem?.()?.name === name && entity.position.distanceTo(bot.entity.position) <= 6;
    } catch (_) { return false; }
  }).sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))[0];
  let goal;
  if (drop) {
    // Stand at the drop's cell, not merely adjacent (adjacency can be outside
    // the server's pickup radius). Walking rules still prohibit extra digging.
    goal = new goals.GoalBlock(Math.floor(drop.position.x), Math.floor(drop.position.y), Math.floor(drop.position.z));
  } else {
    const cleared = bot.blockAt(minedPosition);
    const floor = bot.blockAt(minedPosition.offset(0, -1, 0));
    if (cleared && ['air', 'cave_air'].includes(cleared.name) && floor?.boundingBox === 'block') {
      goal = new goals.GoalBlock(minedPosition.x, minedPosition.y, minedPosition.z);
    }
  }
  try {
    if (goal && await pathCost(bot, goal) != null) await walkTo(bot, goal);
  } catch (error) {
    if (['TASK_TIMEOUT', 'CANCELLED'].includes(error.code)) throw error;
    // A recovery-route failure must not become permission to mine another
    // block. Return the observed count; the caller stops as unverified.
    console.warn('[Pickup Route Rejected]', error.message);
  }
  for (let tick = 0; tick < 40; tick++) {
    if (count(bot, name) > previous) break;
    if (bot.waitForTicks) await bot.waitForTicks(1);
    else await new Promise(resolve => setTimeout(resolve, 50));
  }
  return count(bot, name);
}

module.exports = { recoverPickup };
