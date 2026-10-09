/**
 * One physical operation per bot, with revocable method capabilities.
 * Promise.race is NOT cancellation: expired work must not issue later actions.
 * This is lifecycle protection, not a security sandbox for untrusted JS.
 */
const running = new WeakMap();

function operationError (code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function assertDigSafety (bot, block) {
  const feet = bot.entity && bot.entity.position;
  if (!feet || !block || !block.position) throw operationError('UNSAFE_DIG', 'Cannot verify the mining position.');
  if (Math.floor(feet.x) === block.position.x && Math.floor(feet.z) === block.position.z &&
      block.position.y < Math.floor(feet.y)) {
    throw operationError('UNSAFE_DIG', 'Refusing to mine underneath the bot.');
  }
}

function stopMotion (bot) {
  try { bot.pathfinder?.setGoal(null); } catch (_) {}
  try { bot.stopDigging?.(); } catch (_) {}
  try { bot.clearControlStates?.(); } catch (_) {}
}

async function pacedCraft (bot, craft, args, lease) {
  // Modern clicks carry a server stateId. The stock crafting implementation
  // can finish optimistic output/placement clicks before the server's next
  // inventory update, leading to resyncs rather than an actual crafted item.
  // Pace only this already-serialized primitive; never retry a craft here.
  const original = bot.clickWindow;
  const paced = async (...clickArgs) => {
    lease.check();
    const result = await original.apply(bot, clickArgs);
    await bot.waitForTicks(1);
    lease.check();
    return result;
  };
  bot.clickWindow = paced;
  try { return await craft.apply(bot, args); }
  finally { if (bot.clickWindow === paced) bot.clickWindow = original; }
}

async function runOperation (bot, work, { timeoutMs = 180000, idleTimeoutMs = null,
  progressValue = null, progressIntervalMs = 250 } = {}) {
  if (running.has(bot)) throw operationError('BUSY', 'A previous operation is still settling; please wait.');
  const started = Date.now();
  const lease = { active: true, windows: new Set(), reason: null, started,
    id: require('crypto').randomUUID(), timeoutMs, idleTimeoutMs, lastProgress: started, route: null };
  let bestValue = progressValue ? progressValue(bot) : null;
  lease.observeProgress = () => {
    if (!lease.active) return;
    if (progressValue) {
      const value = progressValue(bot);
      if (Number.isFinite(value) && value > bestValue) {
        bestValue = value;
        lease.lastProgress = Date.now();
      }
    }
    if (lease.route) {
      const score = lease.route.goal.heuristic(bot.entity.position);
      if (Number.isFinite(score) && score < lease.route.best - 0.1) {
        lease.route.best = score;
        lease.lastProgress = Date.now();
      }
    }
  };
  lease.closeWindow = window => {
    if (!lease.windows.delete(window)) return;
    // Closing a stale preflight window can copy its old inventory over the
    // current inventory or close a newer window. Close each tracked window once.
    if ('currentWindow' in bot && bot.currentWindow !== window) return;
    try { window.close(); } catch (_) {}
  };
  const deadline = Date.now() + timeoutMs;
  let rejectStop;
  const stopped = new Promise((_, reject) => { rejectStop = reject; });
  lease.check = () => {
    lease.observeProgress();
    if (lease.active && Date.now() >= deadline) {
      lease.stop(operationError('TASK_TIMEOUT', 'Operation deadline reached; partial progress is not completion.'));
    }
    if (lease.active && idleTimeoutMs != null && Date.now() - lease.lastProgress >= idleTimeoutMs) {
      lease.stop(operationError('TASK_TIMEOUT', 'No observed inventory gain or advance toward the walking target within the inactivity limit.'));
    }
    if (!lease.active) throw lease.reason || operationError('CANCELLED', 'Operation is no longer active.');
  };
  lease.stop = (reason) => {
    if (!lease.active) return;
    lease.reason = reason;
    reason.data = { ...(reason.data || {}), operation_id: lease.id, timing: {
      elapsed_ms: Date.now() - started, hard_limit_ms: timeoutMs, idle_limit_ms: idleTimeoutMs,
      idle_ms: Date.now() - lease.lastProgress } };
    lease.active = false; // Revoke before stopping outstanding movement/digging.
    stopMotion(bot);
    for (const window of [...lease.windows]) lease.closeWindow(window);
    rejectStop(reason);
  };
  running.set(bot, lease);
  const facade = (object, kind = 'bot') => new Proxy(object, {
    get (target, key) {
      if (kind === 'bot' && key === 'pathfinder') return facade(target.pathfinder, 'pathfinder');
      if (typeof key === 'string' && (key.startsWith('_') || ['quit', 'end', 'on', 'once'].includes(key))) {
        throw operationError('UNSUPPORTED_API', 'Private/session APIs are not available to operation code.');
      }
      const value = Reflect.get(target, key, target);
      if (typeof value !== 'function') return value;
      return (...args) => {
        if (kind === 'window' && key === 'close') return lease.closeWindow(target);
        lease.check();
        if (kind === 'bot' && key === 'dig') assertDigSafety(bot, args[0]);
        if (kind === 'pathfinder' && key === 'setMovements') {
          args[0].canDig = false;
          args[0].allow1by1towers = false;
          args[0].scafoldingBlocks = [];
        }
        let route;
        if (kind === 'pathfinder' && key === 'goto' && typeof args[0]?.heuristic === 'function') {
          route = { goal: args[0], best: args[0].heuristic(bot.entity.position) };
          lease.route = route;
        }
        const result = kind === 'bot' && key === 'craft' && bot.clickWindow && bot.waitForTicks &&
          bot.supportFeature?.('stateIdUsed')
          ? pacedCraft(bot, value, args, lease) : value.apply(target, args);
        if (!result || typeof result.then !== 'function') return result;
        return result.then(returned => {
          if (kind === 'bot' && ['openChest', 'openContainer', 'openBlock'].includes(key) && returned) {
            if (!lease.active) { try { returned.close(); } catch (_) {} }
            else lease.windows.add(returned);
          }
          lease.check();
          return kind === 'bot' && ['openChest', 'openContainer', 'openBlock'].includes(key)
            ? facade(returned, 'window') : returned;
        }).finally(() => { if (route && lease.route === route) lease.route = null; });
      };
    },
    set () { throw operationError('UNSUPPORTED_API', 'Operation code may not replace bot properties.'); }
  });
  // Keep the slot reserved until old work settles, even after a timeout reply.
  // New jobs must never overlap an in-flight dig/window operation.
  const execution = Promise.resolve().then(() => work(facade(bot))).finally(() => {
    lease.active = false;
    for (const window of [...lease.windows]) lease.closeWindow(window);
    if (running.get(bot) === lease) {
      stopMotion(bot);
      running.delete(bot);
    }
  });
  const timer = setTimeout(() => lease.stop(operationError(
    'TASK_TIMEOUT', 'Operation timed out; movement stopped. Any partial outcome needs verification.'
  )), timeoutMs);
  const idleTimer = idleTimeoutMs == null ? null : setInterval(() => {
    try { lease.check(); } catch (error) { lease.stop(error); }
  }, Math.min(progressIntervalMs, idleTimeoutMs));
  try {
    return await Promise.race([execution, stopped]);
  } catch (error) {
    lease.stop(error);
    throw error;
  } finally {
    clearTimeout(timer);
    if (idleTimer) clearInterval(idleTimer);
  }
}

function cancelOperation (bot) {
  const lease = running.get(bot);
  if (!lease) return false;
  lease.stop(operationError('CANCELLED', 'Task stopped by the player; partial progress is not completion.'));
  return true;
}

function operationStatus (bot) {
  const lease = running.get(bot);
  return lease ? { busy: true, active: lease.active, operation_id: lease.id,
    elapsed_ms: Date.now() - lease.started, idle_ms: Date.now() - lease.lastProgress,
    hard_limit_ms: lease.timeoutMs, idle_limit_ms: lease.idleTimeoutMs } : { busy: false, active: false };
}
module.exports = { runOperation, cancelOperation, assertDigSafety, operationStatus };
