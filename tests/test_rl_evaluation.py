"""Frozen policy, paired fixtures and held-out provenance; no services or APIs."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if importlib.util.find_spec('numpy') is None:
    raise unittest.SkipTest('Optional RL tests require requirements-rl.txt')
import numpy as np

from rl.environments import GridNavigationEnv
from rl.evaluation import (evaluation_experiment, failure_category, known_seed_ranges,
                           policy_hash, prepare_evaluation, simulator_diagnostics)
from rl.navigation import FEATURE_COUNT, FEATURE_VERSION, cell_position, encode
from rl.ppo import ActorCritic
from rl.runner import episode, parse_args, run


class EvaluationArgumentsTests(unittest.TestCase):
    def test_training_defaults_are_unchanged(self):
        args = parse_args([])
        self.assertEqual((args.updates, args.episodes, args.max_steps, args.eval_episodes), (8, 4, 24, 4))
        self.assertFalse(args.evaluate_only)

    def test_evaluation_accepts_100_layouts_and_no_training(self):
        args = parse_args(['--evaluate-only', '--checkpoint', 'policy.npz', '--eval-episodes', '100',
                           '--compare-max-steps', '64'])
        self.assertEqual((args.updates, args.episodes, args.demonstrations), (0, 0, 0))
        self.assertEqual(args.eval_episodes, 100)

    def test_evaluation_rejects_training_and_unbounded_settings(self):
        base = ['--evaluate-only', '--checkpoint', 'policy.npz']
        invalid = [['--evaluate-only'], base + ['--updates', '1'], base + ['--episodes', '1'],
                   base + ['--demonstrations', '1'], base + ['--eval-episodes', '257'],
                   base + ['--backend', 'minecraft', '--eval-episodes', '100'],
                   base + ['--compare-max-steps', '24'], base + ['--compare-max-steps', '65'],
                   base + ['--evaluation-start-seed', '1000000000', '--eval-episodes', '2'],
                   ['--compare-max-steps', '64']]
        for argv in invalid:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(argv)


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'history'
        self.history.mkdir()
        self.checkpoint = self.root / 'policy.npz'
        self.model = ActorCritic(seed=5, hidden=4)
        # A deliberately simple frozen actor ensures failure traces exist.
        for value in self.model.parameters.values():
            value[...] = 0
        self.model.parameters['ba'][...] = [5, 4, 3, 2, 1]
        self.model.save(self.checkpoint)
        self.args = parse_args(['--evaluate-only', '--checkpoint', str(self.checkpoint),
                                '--eval-episodes', '6', '--max-steps', '8', '--compare-max-steps', '16'])

    def save_history(self, name, report):
        directory = self.history / name
        directory.mkdir()
        (directory / 'report.json').write_text(json.dumps(report))

    def test_seed_audit_covers_failed_demos_training_and_evaluations(self):
        self.save_history('train', {'backend': 'simulator', 'settings': {
            'seed': 17, 'updates': 8, 'episodes': 4, 'demonstrations': 64},
            'held_out_seeds': [100017, 100018, 100019, 100020]})
        self.save_history('evaluate', {'backend': 'simulator', 'mode': 'evaluation-only',
                                      'evaluation_seeds': [2000000, 2000001]})
        self.save_history('other', {'backend': 'minecraft', 'held_out_seeds': [3000000]})
        ranges, count, unreadable = known_seed_ranges(self.history, 'simulator')
        self.assertEqual(count, 2)
        self.assertEqual(unreadable, [])
        self.assertIn((17, 48), ranges)
        self.assertIn((50017, 50080), ranges)
        self.assertIn((100020, 100020), ranges)
        self.assertIn((2000001, 2000001), ranges)
        self.assertNotIn((3000000, 3000000), ranges)
        for seed in (17, 48, 50017, 50080, 100020, 2000001):
            self.args.evaluation_start_seed = seed
            with self.subTest(seed=seed), self.assertRaisesRegex(ValueError, 'overlap'):
                prepare_evaluation(self.args, self.history)
        self.args.evaluation_start_seed = 4000000
        prepared = prepare_evaluation(self.args, self.history)
        self.assertTrue(prepared['provenance']['disjoint_from_known_local_runs'])

    def test_unreadable_history_refuses_claim_of_unseen_layouts(self):
        directory = self.history / 'broken'
        directory.mkdir()
        (directory / 'report.json').write_text('{not JSON')
        with self.assertRaisesRegex(ValueError, 'unreadable'):
            prepare_evaluation(self.args, self.history)

    def test_hash_detects_changes_to_weights_or_version(self):
        original = policy_hash(self.model)
        self.model.parameters['ba'][0] += 1
        self.assertNotEqual(policy_hash(self.model), original)
        self.model.parameters['ba'][0] -= 1
        self.model.version += 1
        self.assertNotEqual(policy_hash(self.model), original)

    async def evaluate(self, name, env=None):
        directory = self.root / name
        directory.mkdir()
        report = {'backend': 'simulator', 'completed': False}
        prepared = prepare_evaluation(self.args, self.history)
        with redirect_stdout(io.StringIO()):
            await evaluation_experiment(env or GridNavigationEnv(), self.args, directory,
                                        report, prepared, episode)
        return directory, report, prepared

    async def test_evaluation_never_trains_or_saves_and_checkpoint_is_unchanged(self):
        before = self.checkpoint.read_bytes()
        with patch.object(ActorCritic, 'update', side_effect=AssertionError('PPO called')), \
             patch.object(ActorCritic, 'clone', side_effect=AssertionError('Cloning called')), \
             patch.object(ActorCritic, 'save', side_effect=AssertionError('Checkpoint save called')):
            directory, report, prepared = await self.evaluate('frozen')
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertTrue(report['completed'] and report['weights_unchanged'])
        self.assertEqual(report['weight_updates'], 0)
        self.assertEqual(report['training_episodes'], 0)
        self.assertEqual(prepared['model'].adam_steps, 0)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ['failures.jsonl', 'report.json', 'summary.md'])
        for key, value in self.model.parameters.items():
            np.testing.assert_equal(prepared['model'].parameters[key], value)
        saved = json.loads((directory / 'report.json').read_text())
        self.assertTrue(saved['completed'] and saved['weights_unchanged'])

    async def test_paired_layouts_and_failure_traces_are_reconstructable(self):
        directory, report, prepared = await self.evaluate('traces')
        self.assertEqual(report['unique_layouts'], 6)
        self.assertEqual(report['step_budget_comparison']['previous_successes_lost'], 0)
        short, longer = report['evaluations']
        for a, b in zip(short['policy']['cases'], longer['policy']['cases']):
            self.assertEqual(a['layout_hash'], b['layout_hash'])
            self.assertIsInstance(a['shortest_path_steps'], int)
            self.assertLessEqual(a['shortest_path_steps'], 80)
        for result in report['evaluations']:
            for p, h in zip(result['policy']['cases'], result['heuristic']['cases']):
                self.assertEqual((p['start_cell'], p['goal']), (h['start_cell'], h['goal']))
        traces = [json.loads(line) for line in (directory / 'failures.jsonl').read_text().splitlines()]
        expected = sum(r['policy']['episodes'] - r['policy']['successes'] for r in report['evaluations'])
        self.assertGreater(expected, 0)
        self.assertEqual(len(traces), expected)
        for trace in traces:
            self.assertFalse(trace['training'] or trace['case']['success'])
            self.assertEqual(trace['feature_version'], FEATURE_VERSION)
            self.assertEqual(len(trace['decisions']), trace['case']['steps'])
            for decision in trace['decisions']:
                self.assertEqual(len(decision['features']), FEATURE_COUNT)
                self.assertEqual(len(decision['mask']), 5)
                self.assertTrue(decision['mask'][decision['action']])
                self.assertEqual(decision['action'], int(np.argmax(decision['model_probabilities'])))
                self.assertAlmostEqual(sum(decision['model_probabilities']), 1)
                self.assertTrue(all(p == 0 for p, allowed in zip(decision['model_probabilities'], decision['mask']) if not allowed))
                reconstructed = prepared['model'].forward(np.array([decision['features']]),
                                                          np.array([decision['mask']]))[0][0]
                np.testing.assert_equal(reconstructed, decision['model_probabilities'])

    async def test_deterministic_policy_reproduces_cases_and_longer_prefix(self):
        first_dir, first, _ = await self.evaluate('first')
        _, second, _ = await self.evaluate('second')
        for a, b in zip(first['evaluations'], second['evaluations']):
            for controller in ('policy', 'heuristic'):
                for x, y in zip(a[controller]['cases'], b[controller]['cases']):
                    x, y = deepcopy(x), deepcopy(y)
                    x.pop('duration_seconds')
                    y.pop('duration_seconds')
                    self.assertEqual(x, y)
        traces = [json.loads(s) for s in (first_dir / 'failures.jsonl').read_text().splitlines()]
        by_seed = {(t['case']['seed'], t['max_steps']): t for t in traces}
        paired = 0
        for (seed, cap), trace in by_seed.items():
            if cap == 8 and (seed, 16) in by_seed:
                paired += 1
                self.assertEqual(trace['decisions'], by_seed[seed, 16]['decisions'][:len(trace['decisions'])])
        self.assertGreater(paired, 0)

    async def test_oracle_does_not_enter_observation_features(self):
        env = GridNavigationEnv()
        frame, _, goal = await env.reset(2000000)
        position = cell_position(frame)
        before = encode(frame, goal, {position: 1})
        oracle, layout = simulator_diagnostics(env, {'start_cell': list(position), 'goal': list(goal)})
        self.assertGreater(oracle['shortest_path_steps'], 0)
        self.assertTrue(layout['walls'])
        after = encode(frame, goal, {position: 1})
        np.testing.assert_equal(before[0], after[0])
        np.testing.assert_equal(before[1], after[1])
        self.assertNotIn('layout_oracle', frame)
        self.assertNotIn('shortest_path_steps', frame)

    async def test_weight_mutation_is_rejected(self):
        prepared = prepare_evaluation(self.args, self.history)
        directory = self.root / 'mutated'
        directory.mkdir()
        async def bad_episode(env, model, *args, **kwargs):
            result = await episode(env, model, *args, **kwargs)
            model.parameters['bv'][...] += 1
            return result
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'mutated'):
            await evaluation_experiment(GridNavigationEnv(), self.args, directory,
                                        {'backend': 'simulator'}, prepared, bad_episode)
        self.assertFalse((directory / 'summary.md').exists())

    async def test_seed_overlap_preflight_never_starts_minecraft(self):
        data = self.root / 'data/rl/old'
        data.mkdir(parents=True)
        (data / 'report.json').write_text(json.dumps({'backend': 'minecraft', 'mode': 'evaluation-only',
                                                     'evaluation_seeds': [2000000]}))
        args = parse_args(['--evaluate-only', '--backend', 'minecraft', '--checkpoint', str(self.checkpoint)])
        with patch('rl.runner.ROOT', self.root), \
             patch('rl.runner.minecraft_backend', side_effect=AssertionError('Minecraft started')), \
             redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'overlap'):
            await run(args)

    async def test_heuristic_cannot_be_marked_on_policy(self):
        with self.assertRaisesRegex(ValueError, 'on-policy'):
            await episode(GridNavigationEnv(), self.model, 1, 8, baseline=True, training=True)

    def test_failure_labels_are_behavioral_not_reasoning_claims(self):
        base = {'success': False, 'termination_reason': 'step_limit', 'moving_revisit_fraction': 0}
        alternating = [{'next_position': [x, 64, 0]} for x in (0, 1, 0, 1, 0, 1)]
        self.assertEqual(failure_category(base, alternating), 'cycle_at_step_limit')
        self.assertEqual(failure_category({**base, 'termination_reason': 'stationary'}, alternating), 'stationary')
        self.assertEqual(failure_category({**base, 'moving_revisit_fraction': 0.5}, []), 'revisits_at_step_limit')
        self.assertEqual(failure_category(base, []), 'step_limit')
        self.assertEqual(failure_category({**base, 'success': True}, alternating), 'success')


if __name__ == '__main__':
    unittest.main()
