"""Small RL math, perception, isolation and on-policy contracts; no real server."""
import asyncio
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

if importlib.util.find_spec('numpy') is None:
    raise unittest.SkipTest('Optional RL tests require requirements-rl.txt')
import numpy as np
from pydantic import ValidationError

from agent.bridge import MineflayerBridge
from rl.environments import GridNavigationEnv
from rl.navigation import FEATURE_COUNT, cell_position, encode, transition_reward, validate_frame
from rl.ppo import ActorCritic, advantages
from rl.runner import experiment, parse_args

ROOT = Path(__file__).resolve().parents[1]


class PpoMathTests(unittest.TestCase):
    def setUp(self):
        self.model = ActorCritic(seed=5, hidden=4)
        rng = np.random.default_rng(7)
        self.features = rng.normal(size=(3, FEATURE_COUNT))
        self.masks = np.array([[True, True, False, False, True], [True, False, True, True, True], [True] * 5])
        self.actions = np.array([0, 2, 4])
        p = self.model.forward(self.features, self.masks)[0]
        self.old_logp = np.log(p[np.arange(3), self.actions])
        self.adv = np.array([0.7, -0.3, 1.2])
        self.returns = np.array([1.0, -0.5, 0.1])

    def loss(self, **kwargs):
        return self.model.loss_and_gradients(self.features, self.masks, self.actions,
                                            self.old_logp, self.adv, self.returns, **kwargs)

    def numerical_check(self):
        _, gradients = self.loss()
        for key, parameter in self.model.parameters.items():
            indices = [()] if parameter.ndim == 0 else list(np.ndindex(parameter.shape))
            # All output/bias entries and a representative sample of encoder weights.
            if key == 'w1':
                indices = indices[::17]
            for index in indices:
                with self.subTest(parameter=key, index=index):
                    original = parameter[index]
                    parameter[index] = original + 1e-6
                    plus = self.loss()[0]['loss']
                    parameter[index] = original - 1e-6
                    minus = self.loss()[0]['loss']
                    parameter[index] = original
                    self.assertAlmostEqual(gradients[key][index], (plus - minus) / 2e-6, places=6)

    def test_shared_actor_critic_and_entropy_gradients_match_finite_differences(self):
        self.numerical_check()

    def test_cloning_gradients_and_loss_decrease(self):
        _, gradients = self.model.cloning_loss_and_gradients(self.features, self.masks, self.actions)
        for key in ('w1', 'b1', 'wa', 'ba', 'wv', 'bv'):
            parameter = self.model.parameters[key]
            index = tuple(0 for _ in parameter.shape)
            original = parameter[index]
            parameter[index] = original + 1e-6
            plus = self.model.cloning_loss_and_gradients(self.features, self.masks, self.actions)[0]
            parameter[index] = original - 1e-6
            minus = self.model.cloning_loss_and_gradients(self.features, self.masks, self.actions)[0]
            parameter[index] = original
            self.assertAlmostEqual(gradients[key][index], (plus - minus) / 2e-6, places=6)
        samples = [{'features': x.tolist(), 'mask': m.tolist(), 'action': int(a)}
                   for x, m, a in zip(self.features, self.masks, self.actions)]
        metrics = self.model.clone(samples)
        self.assertLess(metrics['loss_after'], metrics['loss_before'])
        self.assertEqual(metrics['method'], 'behavior-cloning')
        self.assertEqual(self.model.version, 1)

    def test_clipped_positive_and_negative_advantage_gradients(self):
        # Ratios 1.5 with positive A and 0.6 with negative A: both clipped.
        self.old_logp -= np.log(np.array([1.5, 0.6, 1.0]))
        self.numerical_check()

    def test_masked_actions_have_zero_probability_and_cannot_be_sampled(self):
        p, _, _ = self.model.forward(self.features, self.masks)
        np.testing.assert_equal(p[~self.masks], 0)
        for _ in range(50):
            action, logp, _ = self.model.act(self.features[0], self.masks[0])
            self.assertTrue(self.masks[0, action])
            self.assertTrue(np.isfinite(logp))
        with self.assertRaises(ValueError):
            self.model.forward(self.features, np.zeros_like(self.masks))
        self.actions[0] = 2
        with self.assertRaises(ValueError):
            self.loss()

    def test_gae_bootstraps_truncation_but_does_not_cross_resets(self):
        base = {'reward': 1.0, 'discount': 0.9, 'value': 0.2, 'next_value': 0.7,
                'elapsed_seconds': 0.25, 'terminated': False, 'truncated': True}
        a, returns = advantages([base, {**base, 'reward': 1000, 'terminated': True}])
        self.assertAlmostEqual(a[0], 1 + 0.9 * 0.7 - 0.2)
        self.assertAlmostEqual(a[1], 1000 - 0.2)
        self.assertAlmostEqual(returns[0], 1.63)
        duration = {**base, 'truncated': False, 'elapsed_seconds': 0.5}
        a, _ = advantages([duration, base], lam=0.5)
        self.assertAlmostEqual(a[0], 1.43 + 0.9 * 0.5 ** 2 * 1.43)

    def transitions(self):
        values = self.model.forward(self.features, self.masks)[1]
        return [{'features': x.tolist(), 'mask': mask.tolist(), 'action': int(action),
                 'logp': float(logp), 'value': float(value), 'reward': float(reward), 'discount': 0.99,
                 'next_value': 0.0, 'elapsed_seconds': 0.25, 'terminated': True, 'truncated': False,
                 'policy_version': self.model.version}
                for x, mask, action, logp, value, reward in zip(self.features, self.masks, self.actions,
                                                               self.old_logp, values, [1, -1, 0.5])]

    def test_update_changes_weights_and_refuses_reused_or_mixed_version_rollouts(self):
        transitions = self.transitions()
        original = {k: p.copy() for k, p in self.model.parameters.items()}
        metrics = self.model.update(transitions)
        self.assertGreater(metrics['gradient_steps'], 0)
        self.assertTrue(any(not np.array_equal(original[k], p) for k, p in self.model.parameters.items()))
        with self.assertRaises(ValueError):
            self.model.update(transitions)
        transitions[-1]['policy_version'] = 99
        with self.assertRaises(ValueError):
            self.model.update(transitions)

    def test_checkpoint_roundtrip_and_no_overwrite(self):
        self.model.update(self.transitions())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.npz'
            self.model.save(path)
            restored = ActorCritic.load(path)
            self.assertEqual(restored.version, self.model.version)
            for key, value in self.model.parameters.items():
                np.testing.assert_equal(restored.parameters[key], value)
            with self.assertRaises(FileExistsError):
                self.model.save(path)

    def test_checkpoint_contract_and_compressed_size_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wrong.npz'
            np.savez_compressed(path, metadata=json.dumps({'schema_version': 999}))
            with self.assertRaises(ValueError):
                ActorCritic.load(path)
            path = Path(directory) / 'oversize.npz'
            np.savez_compressed(path, unrelated=np.zeros(300_000))
            with self.assertRaisesRegex(ValueError, 'expands'):
                ActorCritic.load(path)


class NavigationContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = GridNavigationEnv()
        self.frame, self.evidence, self.goal = await self.env.reset(17)

    async def test_encoder_categorical_unknown_and_no_oracle_map(self):
        features, mask = encode(self.frame, self.goal, {})
        self.assertEqual(len(features), FEATURE_COUNT)
        self.assertEqual(features[:75].sum(), 25)
        np.testing.assert_equal(features[:75].reshape(25, 3).sum(axis=1), 1)
        self.assertEqual(mask.dtype, bool)
        altered = deepcopy(self.frame)
        # IDs are categorical registry metadata, not input magnitudes.
        altered['observation']['terrain']['blockIds'] = [999] * 125
        np.testing.assert_equal(features, encode(altered, self.goal, {})[0])
        altered['walkability'][0] = -1
        self.assertEqual(encode(altered, self.goal, {})[0][0], 1)

    async def test_fake_masks_walkable_liquids_and_version_are_rejected(self):
        frame = deepcopy(self.frame)
        frame['actionMask'][4] = False
        with self.assertRaises(ValidationError):
            validate_frame(frame)
        frame = deepcopy(self.frame)
        frame['walkability'][12] = 1
        frame['observation']['terrain']['fluid'][((2 * 5 + 2) * 5 + 2)] = 1
        with self.assertRaises(ValidationError):
            validate_frame(frame)
        with self.assertRaises(ValidationError):
            validate_frame({**self.frame, 'schemaVersion': 2})

    async def test_simulator_reproducibility_and_world_is_labelled_synthetic(self):
        other = GridNavigationEnv()
        frame, _, goal = await other.reset(17)
        self.assertEqual(goal, self.goal)
        self.assertEqual(frame['walkability'], self.frame['walkability'])
        self.assertEqual(frame['backend'], 'grid-simulator')
        self.assertTrue(frame['observation']['state']['world']['id'].startswith('synthetic:'))

    async def test_server_reward_not_model_claim_and_duration_discount(self):
        goal = (3, 64, 3)
        after = {**self.evidence, 'position': {'x': 3.5, 'y': 64.0, 'z': 3.5}}
        r, discount, terminal, success = transition_reward(self.evidence, after, goal, 0.25)
        self.assertTrue(terminal and success)
        self.assertGreater(r, 0)
        self.assertAlmostEqual(discount, 0.99)
        _, slower, _, _ = transition_reward(self.evidence, after, goal, 0.5)
        self.assertAlmostEqual(slower, 0.99 ** 2)
        for damaged in ({**after, 'health': 19}, {**after, 'broken': [{'block': 'stone'}]},
                        {**after, 'on_ground': False}):
            self.assertFalse(transition_reward(self.evidence, damaged, goal, 0.25)[3])
        self.assertFalse(transition_reward(self.evidence, after, goal, 0.25, failed=True)[3])

    async def test_synthetic_observation_cannot_authorize_a_live_action(self):
        bridge = MineflayerBridge()
        with self.assertRaises(ValueError):
            await bridge.execute_rl_step(0, self.frame)
        with self.assertRaises(ValueError):
            await bridge.execute_rl_step(True, self.frame)

    async def test_interrupted_action_aborts_without_another_reset(self):
        from rl.runner import episode
        resets = []
        class CancelledEnv:
            backend = 'grid-simulator'
            async def reset(inner, seed):
                resets.append(seed)
                return self.frame, self.evidence, self.goal
            async def step(inner, action):
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await episode(CancelledEnv(), ActorCritic(), 10, 8, training=True)
        self.assertEqual(resets, [10])

    async def test_failed_teacher_episodes_never_become_demonstrations(self):
        from rl.runner import demonstrations
        class NoMovement(GridNavigationEnv):
            async def step(inner, action):
                return inner.frame, inner.evidence(), 0.25, {'success': True, 'verified': True}
        args = parse_args(['--demonstrations', '2', '--max-steps', '8'])
        stream = io.StringIO()
        samples, successful = await demonstrations(NoMovement(), args, stream)
        self.assertEqual(samples, [])
        self.assertEqual(successful, 0)
        self.assertEqual(stream.getvalue(), '')

    async def test_goal_boundaries_and_invalid_health_rejected(self):
        for goal in ((1, 65, 1), (999, 64, 1), (True, 64, 1)):
            with self.assertRaises(ValueError):
                encode(self.frame, goal, {})
        for health in (True, float('inf'), -1, 21):
            frame = deepcopy(self.frame)
            frame['observation']['state']['stats']['health'] = health
            with self.assertRaises(ValueError):
                encode(frame, self.goal, {})

    async def test_short_experiment_saves_real_updates_and_separate_evaluation(self):
        args = parse_args(['--updates', '2', '--episodes', '2', '--max-steps', '8', '--eval-episodes', '2'])
        with tempfile.TemporaryDirectory() as directory:
            report = {}
            await experiment(self.env, args, Path(directory), report)
            self.assertTrue(report['completed'])
            self.assertFalse(report['promoted_to_default_agent'])
            self.assertEqual(report['final_policy_version'], 2)
            transitions = [json.loads(s) for s in (Path(directory) / 'transitions.jsonl').read_text().splitlines()]
            seeds = {t['seed'] for t in transitions}
            self.assertFalse(seeds.intersection(report['held_out_seeds']))
            self.assertTrue(all(t['training'] and t['backend'] == 'grid-simulator' for t in transitions))
            self.assertTrue((Path(directory) / 'policy.npz').is_file())

    async def test_cross_runtime_navigation_frame(self):
        if not shutil.which('node'):
            self.skipTest('Node unavailable')
        result = subprocess.run(['node', '-e',
            "process.env.ENABLE_RL_NAVIGATION='true';process.env.MC_WORLD_ID='practice:rl:test';"
            "const {fixture}=require('./tests/test_rl_navigation');"
            "const {navigationFrame}=require('./bot/rl_navigation');"
            "process.stdout.write(JSON.stringify(navigationFrame(fixture().bot)));"],
            cwd=ROOT, text=True, capture_output=True, check=True, timeout=10)
        frame = validate_frame(json.loads(result.stdout))
        self.assertEqual(frame['backend'], 'mineflayer')
        # Exercise the live serializer's actual nested stats shape, not only
        # our simulator's envelope. Catch encoder/real-client drift offline.
        features, _ = encode(frame, (3, 64, 3), {})
        self.assertEqual(features[-1], 1.0)
        bridge = MineflayerBridge()
        calls = []
        class Client:
            async def send(inner, raw):
                request = json.loads(raw)
                calls.append(request)
                await bridge._route_message({'type': 'navigation_response', 'id': request['id'], 'data': frame})
        bridge.active_client = Client()
        self.assertEqual(await bridge.get_navigation_frame(), frame)
        self.assertEqual(calls[0]['type'], 'get_navigation_frame')
        self.assertEqual(bridge.pending_requests, {})

        # Physical request timeout cancels, unlike a read-only observation timeout.
        cancellations = []
        class Unresponsive:
            async def send(inner, raw):
                cancellations.append(json.loads(raw)['type'])
        bridge.active_client = Unresponsive()
        with self.assertRaises(asyncio.TimeoutError):
            await bridge.execute_rl_step(0, frame, timeout=0.001)
        self.assertEqual(cancellations, ['rl_step', 'cancel_task'])
        self.assertEqual(bridge.pending_requests, {})


if __name__ == '__main__':
    unittest.main()
