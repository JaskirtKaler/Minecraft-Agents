// Keep npm test and check.sh on the same complete set of offline contracts.
// test_skills.js includes the state, resources, tools, navigation, observations,
// and RL navigation suites. Run the remaining entry points once, in isolation.
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const suites = [
  'test_skills.js',
  'test_grounding.js',
  'test_region_inspection.js',
  'test_construction.js',
  'test_construction_controller.js',
  'test_operation_progress.js'
];

for (const suite of suites) {
  const result = spawnSync(process.execPath, [path.join(__dirname, suite)], { stdio: 'inherit' });
  if (result.error) console.error(result.error);
  if (result.status !== 0) process.exit(result.status || 1);
}
