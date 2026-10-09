/** Experimental walking options. Never enabled on the ordinary agent/world. */
const { goals } = require('mineflayer-pathfinder');
const { getObservation } = require('./observations');
const { runOperation } = require('./operations');
const { pathCost, walkTo } = require('./navigation');

// World-axis actions, not camera-relative key presses. No digging or placement.
const DIRECTIONS = [[1, 0], [-1, 0], [0, 1], [0, -1], [0, 0]];
const HAZARD = /^(?:water|flowing_water|lava|flowing_lava|bubble_column|fire|soul_fire|cactus|magma_block|(?:soul_)?campfire|sweet_berry_bush|wither_rose|powder_snow|pointed_dripstone)$/;
const fail = (code, message) => { throw Object.assign(new Error(message), { code }); };

function requirePractice (bot) {
  if (process.env.ENABLE_RL_NAVIGATION !== 'true' ||
      !String(process.env.MC_WORLD_ID || '').startsWith('practice:rl:')) {
    fail('RL_DISABLED', 'RL controls require an explicitly enabled disposable RL world.');
  }
  if (!bot.entity?.position) fail('RL_NOT_READY', 'The RL bot is not spawned.');
}

function navigationFrame (bot) {
  requirePractice(bot);
  const observation = getObservation(bot, { horizontalRadius: 2, verticalRadius: 2 });
  if (!observation.state.ready) fail('RL_NOT_READY', 'The RL bot is not ready.');
  const grid = observation.terrain;
  const data = bot.registry?.blocks || require('minecraft-data')(bot.version).blocks;
  const cell = (x, y, z) => {
    const index = ((x + 2) * 5 + y + 2) * 5 + z + 2;
    const name = data[grid.blockIds[index]]?.name;
    return { known: grid.loaded[index] && grid.collision[index] !== -1 && !!name,
      collision: grid.collision[index], fluid: grid.fluid[index], hazard: !!name && HAZARD.test(name) };
  };
  const walkability = [];
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) {
    const feet = cell(x, 0, z), head = cell(x, 1, z), support = cell(x, -1, z);
    const cells = [feet, head, support];
    walkability.push(cells.some(c => !c.known || c.fluid === -1) ? -1
      : feet.collision === 0 && head.collision === 0 && support.collision === 1 &&
        cells.every(c => c.fluid === 0 && !c.hazard) ? 1 : 0);
  }
  const actionMask = DIRECTIONS.map(([dx, dz], index) => index === 4 ||
    (observation.motion.onGround === true && walkability[12] === 1 && walkability[(dx + 2) * 5 + dz + 2] === 1));
  return { schemaVersion: 1, backend: 'mineflayer', observation, walkability, actionMask };
}

async function executeNavigationStep (bot, request) {
  try {
    requirePractice(bot);
    if (!request || !Number.isInteger(request.action) || request.action < 0 || request.action > 4) {
      fail('INVALID_RL_ACTION', 'RL action must be an integer from 0 to 4.');
    }
    const data = await runOperation(bot, async b => {
      // Session identity is keyed by the actual bot, not its revocable facade.
      // Physical calls below still use the facade and its cancellation lease.
      const frame = navigationFrame(bot);
      const world = frame.observation.state.world;
      if (!['id', 'dimension', 'sessionId'].every(k => request.expectedWorld?.[k] === world[k])) {
        fail('RL_SESSION_CHANGED', 'World/session changed; discard this episode.');
      }
      const from = frame.observation.terrain.center;
      if (!['x', 'y', 'z'].every(k => Number.isInteger(request.expectedPosition?.[k]) && request.expectedPosition[k] === from[k])) {
        fail('RL_POSITION_CHANGED', 'The bot moved after observation; discard this action.');
      }
      if (!frame.actionMask[request.action]) fail('RL_MASKED_ACTION', 'That step is blocked, unknown, airborne, or hazardous.');
      const [dx, dz] = DIRECTIONS[request.action];
      const to = { x: from.x + dx, y: from.y, z: from.z + dz };
      if (request.action !== 4) {
        const goal = new goals.GoalBlock(to.x, to.y, to.z);
        const cost = await pathCost(b, goal);
        if (cost == null || cost > 2) fail('RL_NO_SHORT_ROUTE', 'No short non-destructive route exists to this adjacent cell.');
        await walkTo(b, goal, { timeoutMs: 5000, stalledMs: 2000, intervalMs: 100 });
      }
      await b.waitForTicks(2);
      const p = b.entity.position;
      if (!['x', 'y', 'z'].every(k => Math.floor(p[k]) === to[k]) || b.entity.onGround !== true) {
        fail('RL_MOVE_UNVERIFIED', 'The adjacent walking option did not settle at its target.');
      }
      return { action: request.action, before: from, after: to };
    }, { timeoutMs: 7000 });
    return { success: true, verified: true, data };
  } catch (error) {
    return { success: false, verified: false, data: { error_code: error.code || 'RL_STEP_FAILED' }, message: error.message };
  }
}

module.exports = { navigationFrame, executeNavigationStep, DIRECTIONS };
