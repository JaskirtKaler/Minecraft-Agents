"""Small NumPy actor–critic with PPO-clip, GAE, masked actions and Adam.

This is an experimental implementation, not a general-purpose RL library.
Gradients are checked numerically in tests/test_rl.py. No model/API dependency.
"""
import json
from pathlib import Path
import zipfile

import numpy as np

from rl.navigation import ACTIONS, FEATURE_COUNT, FEATURE_VERSION, TIME_UNIT


def advantages(transitions, lam=0.95):
    if not transitions or not 0 <= lam <= 1:
        raise ValueError('GAE needs transitions and lambda in 0–1.')
    result = np.zeros(len(transitions))
    carry = 0.0
    for i in range(len(transitions) - 1, -1, -1):
        t = transitions[i]
        bootstrap = 0 if t['terminated'] else t['next_value']
        delta = t['reward'] + t['discount'] * bootstrap - t['value']
        boundary = t['terminated'] or t['truncated']
        duration = max(1, t['elapsed_seconds'] / TIME_UNIT)
        carry = delta + (0 if boundary else t['discount'] * lam ** duration * carry)
        result[i] = carry
    return result, result + np.array([t['value'] for t in transitions])


class ActorCritic:
    def __init__(self, seed=0, hidden=32):
        if type(hidden) is not int or not 4 <= hidden <= 128:
            raise ValueError('Hidden width must be 4–128.')
        self.rng = np.random.default_rng(seed)
        self.hidden = hidden
        self.version = 0
        self.parameters = {
            'w1': self.rng.normal(0, 1 / np.sqrt(FEATURE_COUNT), (FEATURE_COUNT, hidden)),
            'b1': np.zeros(hidden),
            'wa': self.rng.normal(0, 0.01, (hidden, len(ACTIONS))),
            'ba': np.zeros(len(ACTIONS)),
            'wv': self.rng.normal(0, 1 / np.sqrt(hidden), hidden),
            'bv': np.zeros(1),
        }
        self.m = {k: np.zeros_like(v) for k, v in self.parameters.items()}
        self.v = {k: np.zeros_like(v) for k, v in self.parameters.items()}
        self.adam_steps = 0

    def forward(self, features, masks):
        x = np.asarray(features, dtype=np.float64)
        mask = np.asarray(masks)
        if x.ndim != 2 or x.shape[1] != FEATURE_COUNT or not np.isfinite(x).all():
            raise ValueError('Invalid navigation feature matrix.')
        if mask.dtype != bool or mask.shape != (len(x), len(ACTIONS)) or not mask.any(axis=1).all():
            raise ValueError('Each observation needs a Boolean mask with a valid action.')
        p = self.parameters
        h = np.tanh(x @ p['w1'] + p['b1'])
        logits = np.where(mask, h @ p['wa'] + p['ba'], -np.inf)
        shifted = logits - logits.max(axis=1, keepdims=True)
        probabilities = np.exp(shifted)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        values = h @ p['wv'] + p['bv'][0]
        return probabilities, values, h

    def act(self, features, mask, deterministic=False):
        probabilities, values, _ = self.forward(np.array([features]), np.array([mask]))
        action = int(np.argmax(probabilities[0])) if deterministic else int(self.rng.choice(len(ACTIONS), p=probabilities[0]))
        return action, float(np.log(probabilities[0, action])), float(values[0])

    def value(self, features, mask):
        return float(self.forward(np.array([features]), np.array([mask]))[1][0])

    def loss_and_gradients(self, features, masks, actions, old_logp, adv, returns,
                           clip=0.2, value_coefficient=0.5, entropy_coefficient=0.01):
        x = np.asarray(features, dtype=np.float64)
        probabilities, values, h = self.forward(x, masks)
        n = len(x)
        actions, old_logp, adv, returns = map(np.asarray, (actions, old_logp, adv, returns))
        if n == 0 or any(a.shape != (n,) for a in (actions, old_logp, adv, returns)):
            raise ValueError('PPO batch arrays need matching nonempty lengths.')
        if not np.issubdtype(actions.dtype, np.integer) or np.any(actions < 0) or np.any(actions >= len(ACTIONS)):
            raise ValueError('Invalid action indices.')
        if not np.asarray(masks)[np.arange(n), actions].all():
            raise ValueError('Rollouts may not include masked actions.')
        if not all(np.isfinite(a).all() for a in (old_logp, adv, returns)) or np.any(old_logp > 0):
            raise ValueError('PPO targets and old log probabilities must be finite.')
        selected = probabilities[np.arange(n), actions]
        logp = np.log(np.maximum(selected, 1e-300))
        logratio = logp - old_logp
        if np.max(np.abs(logratio)) > 50:
            raise ValueError('Rollout probability ratios are numerically unstable.')
        ratio = np.exp(logratio)
        objective = np.minimum(ratio * adv, np.clip(ratio, 1 - clip, 1 + clip) * adv)
        logs = np.log(np.maximum(probabilities, 1e-300))
        entropy = -np.sum(probabilities * logs, axis=1)
        loss = -objective.mean() + 0.5 * value_coefficient * np.mean((values - returns) ** 2) - entropy_coefficient * entropy.mean()
        active = np.where(adv >= 0, ratio <= 1 + clip, ratio >= 1 - clip)
        onehot = np.eye(len(ACTIONS))[actions]
        dlogits = (-adv * ratio * active / n)[:, None] * (onehot - probabilities)
        dlogits += entropy_coefficient / n * probabilities * (logs + entropy[:, None])
        dv = value_coefficient * (values - returns) / n
        p = self.parameters
        dh = (dlogits @ p['wa'].T + dv[:, None] * p['wv']) * (1 - h ** 2)
        gradients = {'wa': h.T @ dlogits, 'ba': dlogits.sum(axis=0),
                     'wv': h.T @ dv, 'bv': np.array([dv.sum()]),
                     'w1': x.T @ dh, 'b1': dh.sum(axis=0)}
        metrics = {'loss': float(loss), 'entropy': float(entropy.mean()),
                   'approx_kl': float(np.mean(ratio - 1 - logratio)),
                   'clip_fraction': float(np.mean(np.abs(ratio - 1) > clip))}
        if not np.isfinite(loss) or not all(np.isfinite(g).all() for g in gradients.values()):
            raise ValueError('Nonfinite PPO update; policy not safe to use.')
        return metrics, gradients

    def _adam(self, gradients, learning_rate):
        norm = np.sqrt(sum(np.sum(g ** 2) for g in gradients.values()))
        scale = min(1, 0.5 / max(norm, 1e-12))
        self.adam_steps += 1
        for k, g in gradients.items():
            g = g * scale
            self.m[k] = 0.9 * self.m[k] + 0.1 * g
            self.v[k] = 0.999 * self.v[k] + 0.001 * g ** 2
            corrected_m = self.m[k] / (1 - 0.9 ** self.adam_steps)
            corrected_v = self.v[k] / (1 - 0.999 ** self.adam_steps)
            self.parameters[k] -= learning_rate * corrected_m / (np.sqrt(corrected_v) + 1e-8)

    def cloning_loss_and_gradients(self, features, masks, actions):
        """Supervised warm-start, explicitly NOT an off-policy PPO update."""
        x, actions = np.asarray(features, dtype=np.float64), np.asarray(actions)
        probabilities, _, h = self.forward(x, masks)
        n = len(x)
        if (not n or actions.shape != (n,) or not np.issubdtype(actions.dtype, np.integer) or
                np.any(actions < 0) or np.any(actions >= len(ACTIONS)) or
                not np.asarray(masks)[np.arange(n), actions].all()):
            raise ValueError('Demonstrations need valid unmasked action labels.')
        loss = float(-np.log(np.maximum(probabilities[np.arange(n), actions], 1e-300)).mean())
        dlogits = (probabilities - np.eye(len(ACTIONS))[actions]) / n
        dh = (dlogits @ self.parameters['wa'].T) * (1 - h ** 2)
        return loss, {'w1': x.T @ dh, 'b1': dh.sum(axis=0),
                      'wa': h.T @ dlogits, 'ba': dlogits.sum(axis=0),
                      'wv': np.zeros_like(self.parameters['wv']), 'bv': np.zeros_like(self.parameters['bv'])}

    def clone(self, demonstrations, epochs=30, batch_size=64):
        if not demonstrations or not 1 <= epochs <= 50 or not 1 <= batch_size <= 4096:
            raise ValueError('Cloning needs demonstrations and bounded update settings.')
        x = np.array([t['features'] for t in demonstrations])
        masks = np.array([t['mask'] for t in demonstrations], dtype=bool)
        actions = np.array([t['action'] for t in demonstrations])
        before, _ = self.cloning_loss_and_gradients(x, masks, actions)
        for _ in range(epochs):
            order = self.rng.permutation(len(demonstrations))
            for start in range(0, len(order), batch_size):
                idx = order[start:start + batch_size]
                _, gradients = self.cloning_loss_and_gradients(x[idx], masks[idx], actions[idx])
                self._adam(gradients, 0.001)
        after, _ = self.cloning_loss_and_gradients(x, masks, actions)
        self.version += 1
        return {'method': 'behavior-cloning', 'samples': len(demonstrations),
                'loss_before': before, 'loss_after': after, 'policy_version': self.version}

    def update(self, transitions, epochs=4, batch_size=32, learning_rate=0.0003):
        if not transitions or any(t['policy_version'] != self.version for t in transitions):
            raise ValueError('PPO requires a fresh rollout from the current policy version, not historical logs.')
        if not 1 <= epochs <= 20 or not 1 <= batch_size <= 4096 or not 0 < learning_rate <= 0.01:
            raise ValueError('Invalid bounded PPO update settings.')
        adv, returns = advantages(transitions)
        if len(adv) > 1:
            adv = (adv - adv.mean()) / max(adv.std(), 1e-8)
        x = np.array([t['features'] for t in transitions])
        masks = np.array([t['mask'] for t in transitions], dtype=bool)
        actions = np.array([t['action'] for t in transitions])
        old_logp = np.array([t['logp'] for t in transitions])
        # Validate the entire batch before the first mutation.
        self.loss_and_gradients(x, masks, actions, old_logp, adv, returns)
        updates = 0
        metrics = {}
        for _ in range(epochs):
            order = self.rng.permutation(len(transitions))
            for start in range(0, len(order), batch_size):
                idx = order[start:start + batch_size]
                metrics, gradients = self.loss_and_gradients(x[idx], masks[idx], actions[idx], old_logp[idx], adv[idx], returns[idx])
                if metrics['approx_kl'] > 0.03:
                    break
                self._adam(gradients, learning_rate)
                updates += 1
            else:
                continue
            break
        self.version += 1  # A consumed rollout cannot be used again, even after early stop.
        return {**metrics, 'gradient_steps': updates, 'policy_version': self.version,
                'transitions': len(transitions)}

    def save(self, path):
        """New artifact only. Never replace another experiment's checkpoint."""
        metadata = {'schema_version': 1, 'feature_version': FEATURE_VERSION, 'features': FEATURE_COUNT,
                    'actions': list(ACTIONS), 'hidden': self.hidden, 'policy_version': self.version,
                    'scope': 'same-level-adjacent-navigation', 'optimizer_saved': False}
        with Path(path).open('xb') as stream:
            np.savez_compressed(stream, metadata=json.dumps(metadata), **self.parameters)

    @classmethod
    def load(cls, path, seed=0):
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError('Navigation checkpoint unexpectedly large.')
        with zipfile.ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 2_000_000:
                raise ValueError('Navigation checkpoint expands beyond its size limit.')
        with np.load(path, allow_pickle=False) as data:
            metadata = json.loads(str(data['metadata']))
            if (metadata.get('schema_version') != 1 or metadata.get('feature_version') != FEATURE_VERSION or
                    metadata.get('features') != FEATURE_COUNT or metadata.get('actions') != list(ACTIONS) or
                    type(metadata.get('policy_version')) is not int or metadata['policy_version'] < 0):
                raise ValueError('Checkpoint contract does not match this policy.')
            model = cls(seed=seed, hidden=metadata['hidden'])
            if set(data.files) != {'metadata', *model.parameters.keys()}:
                raise ValueError('Checkpoint contains unexpected arrays.')
            for k, template in model.parameters.items():
                a = data[k]
                if a.shape != template.shape or not np.issubdtype(a.dtype, np.floating) or not np.isfinite(a).all():
                    raise ValueError('Invalid checkpoint weights.')
                model.parameters[k] = a.astype(np.float64, copy=True)
            model.version = metadata['policy_version']
        return model
