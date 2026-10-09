/** Shared execution budgets, never model-selected task sequences. */
const policy = require('../shared/tool_timing.json');
const GATHER = new Set(['mine_logs', 'mine_resource']);
function toolTiming (tool) {
  if (!GATHER.has(tool?.name)) return { timeoutMs: policy.shortMs };
  const count = Number.isInteger(tool.args?.count) ? Math.min(64, Math.max(1, tool.args.count)) : 1;
  return { timeoutMs: Math.min(policy.gatherMaxMs, policy.gatherBaseMs + count * policy.gatherPerItemMs),
    idleTimeoutMs: policy.gatherIdleMs };
}
module.exports = { toolTiming, policy, GATHER };
