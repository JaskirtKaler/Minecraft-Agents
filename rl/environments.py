"""Disposable Minecraft and cheap grid backends with the same policy interface."""
import asyncio
from collections import deque
from datetime import datetime, timezone
from math import floor
import time

import numpy as np

from rl.navigation import DIRECTIONS, cell_position, validate_frame


class GridNavigationEnv:
    """Synthetic walking physics only. Results are NOT Minecraft performance."""
    backend = 'grid-simulator'

    async def reset(self, seed):
        rng = np.random.default_rng(seed)
        cells = [(x, z) for x in range(-4, 5) for z in range(-4, 5)]
        for _ in range(100):
            self.walls = {p for p in cells if rng.random() < 0.18}
            available = [p for p in cells if p not in self.walls]
            a, b = rng.choice(len(available), size=2, replace=False)
            start, target = available[a], available[b]
            if abs(start[0] - target[0]) + abs(start[1] - target[1]) < 5:
                continue
            queue, visited = deque([start]), {start}
            while queue:
                x, z = queue.popleft()
                for dx, dz in DIRECTIONS[:4]:
                    p = (x + dx, z + dz)
                    if p in cells and p not in visited and p not in self.walls:
                        visited.add(p)
                        queue.append(p)
            if target in visited:
                break
        else:
            raise RuntimeError('Could not generate a connected navigation fixture.')
        self.position = (start[0], 64, start[1])
        self.goal = (target[0], 64, target[1])
        self.seed = seed
        self.frame = self._frame()
        return self.frame, self.evidence(), self.goal

    def _open(self, x, z):
        return -4 <= x <= 4 and -4 <= z <= 4 and (x, z) not in self.walls

    def evidence(self):
        x, y, z = self.position
        return {'position': {'x': x + 0.5, 'y': float(y), 'z': z + 0.5},
                'health': 20.0, 'on_ground': True, 'broken': [], 'unsafe_breaks': 0}

    def _frame(self):
        x, y, z = self.position
        walkability = [int(self._open(x + dx, z + dz)) for dx in range(-2, 3) for dz in range(-2, 3)]
        blocks, collision = [], []
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                for dz in range(-2, 3):
                    solid = dy < 0 or (not self._open(x + dx, z + dz) and dy <= 1)
                    blocks.append(1 if solid else 0)
                    collision.append(int(solid))
        p = self.evidence()['position']
        # A generated contract fixture, explicitly labelled as a simulator in
        # the envelope/world. It must never authorize a live bridge action.
        observation = {'schemaVersion': 1, 'source': 'mineflayer', 'gameVersion': 'synthetic-grid-v1',
            'state': {'ready': True, 'world': {'id': 'synthetic:rl', 'dimension': 'grid', 'sessionId': str(self.seed)},
                      'observedAt': datetime.now(timezone.utc).isoformat(), 'stats': {'health': 20.0}},
            'motion': {'position': p, 'yaw': 0.0, 'pitch': 0.0,
                       'velocity': {'x': 0.0, 'y': 0.0, 'z': 0.0}, 'onGround': True},
            'terrain': {'center': {'x': float(x), 'y': float(y), 'z': float(z)}, 'shape': [5, 5, 5],
                        'axisOrder': 'xyz', 'blockIds': blocks, 'stateIds': blocks.copy(),
                        'loaded': [True] * 125, 'collision': collision, 'fluid': [0] * 125, 'fullyLoaded': True},
            'inventoryDetails': [], 'landmarks': [], 'landmarkListComplete': False, 'lineOfSightFiltered': False}
        mask = [i == 4 or walkability[(dx + 2) * 5 + dz + 2] == 1
                for i, (dx, dz) in enumerate(DIRECTIONS)]
        return validate_frame({'schemaVersion': 1, 'backend': self.backend, 'observation': observation,
                               'walkability': walkability, 'actionMask': mask})

    async def step(self, action):
        if type(action) is not int or not 0 <= action <= 4 or not self.frame['actionMask'][action]:
            raise ValueError('Invalid or masked navigation action.')
        x, y, z = self.position
        dx, dz = DIRECTIONS[action]
        self.position = (x + dx, y, z + dz)
        self.frame = self._frame()
        return self.frame, self.evidence(), 0.25, {'success': True, 'verified': True, 'source': self.backend}


class MinecraftNavigationEnv:
    backend = 'mineflayer'

    def __init__(self, console, bridge):
        self.console, self.bridge = console, bridge

    async def reset(self, seed):
        # Console/plugin guard proves ownership of this disposable world.
        evidence = await self.console.request('setup', f'rl_navigation {seed}')
        self.goal = tuple(evidence['nav_goal'][a] for a in ('x', 'y', 'z'))
        expected = tuple(floor(evidence['position'][a]) for a in ('x', 'y', 'z'))
        await asyncio.sleep(0.3)
        for _ in range(30):
            frame = await self.bridge.get_navigation_frame()
            if (frame['backend'] == self.backend and cell_position(frame) == expected and
                    frame['observation']['motion']['onGround'] is True and frame['walkability'][12] == 1):
                self.frame = frame
                self.world = frame['observation']['state']['world']
                # Observe the settled server position, not its teleport command.
                return frame, await self.console.request('snapshot'), self.goal
            await asyncio.sleep(0.1)
        raise RuntimeError('Minecraft reset did not produce a settled navigation observation.')

    async def step(self, action):
        started = time.monotonic()
        result = await self.bridge.execute_rl_step(action, self.frame)
        code = result.get('data', {}).get('error_code')
        if code in {'BUSY', 'TASK_TIMEOUT', 'CANCELLED', 'RL_SESSION_CHANGED', 'RL_POSITION_CHANGED',
                    'RL_DISABLED', 'RL_NOT_READY', 'NAVIGATION_TIMEOUT', 'NO_NAVIGATION_PROGRESS'}:
            raise RuntimeError(f'Unsettled/stale RL action ({code}); aborting without another world reset.')
        frame = await self.bridge.get_navigation_frame()
        if frame['observation']['state']['world'] != self.world:
            raise RuntimeError('World/session changed during episode; do not train across it.')
        evidence = await self.console.request('snapshot')
        if cell_position(frame) != tuple(floor(evidence['position'][a]) for a in ('x', 'y', 'z')):
            raise RuntimeError('Client/server position evidence disagrees; discard this rollout.')
        self.frame = frame
        return frame, evidence, max(0.001, time.monotonic() - started), result

    async def pathfinder_control(self, seed):
        """Existing full-route helper, separately labelled: it has a wider map."""
        _, before, goal = await self.reset(seed)
        started = time.monotonic()
        result = await self.bridge.execute_tool({'name': 'walk_to', 'args': {
            'position': dict(zip(('x', 'y', 'z'), goal))}}, timeout=70)
        if result.get('data', {}).get('error_code') in {'BUSY', 'TASK_TIMEOUT', 'CANCELLED'}:
            raise RuntimeError('Pathfinder control unsettled; aborting before a reset.')
        frame = await self.bridge.get_navigation_frame()
        if frame['observation']['state']['world'] != self.world:
            raise RuntimeError('Pathfinder control crossed a world/session boundary.')
        after = await self.console.request('snapshot')
        from rl.navigation import transition_reward
        _, _, _, success = transition_reward(before, after, goal, max(0.001, time.monotonic() - started),
                                             failed=not (result.get('success') and result.get('verified')))
        return {'seed': seed, 'success': success, 'before': before, 'after': after,
                'duration_seconds': time.monotonic() - started,
                'comparison': 'Existing pathfinder uses a wider loaded map; control, not an equal-observation RL baseline.'}
