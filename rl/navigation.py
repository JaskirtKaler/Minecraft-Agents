"""Shared perception, option actions and rewards for the navigation experiment."""
from math import floor, hypot, isfinite
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from agent.observations import Contract, MinecraftObservation

ACTIONS = ('east', 'west', 'south', 'north', 'wait')
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0))
FEATURE_VERSION = 2
FEATURE_COUNT = 103  # 25 categorical cells * 3 + goal x/z + 25 visits + health
GAMMA = 0.99
TIME_UNIT = 0.25


class NavigationFrame(Contract):
    schemaVersion: Literal[1]
    backend: Literal['mineflayer', 'grid-simulator']
    observation: MinecraftObservation
    walkability: list[Literal[-1, 0, 1]] = Field(min_length=25, max_length=25)
    actionMask: list[bool] = Field(min_length=5, max_length=5)

    @model_validator(mode='after')
    def check_frame(self):
        if not self.observation.state['ready'] or self.observation.terrain.shape != [5, 5, 5]:
            raise ValueError('Navigation requires a spawned 5 by 5 by 5 observation.')
        expected = [i == 4 or (self.observation.motion.onGround is True and self.walkability[12] == 1 and
                              self.walkability[(dx + 2) * 5 + dz + 2] == 1)
                    for i, (dx, dz) in enumerate(DIRECTIONS)]
        if self.actionMask != expected:
            raise ValueError('Action mask must agree with grounded adjacent walkability.')
        grid = self.observation.terrain
        for index, value in enumerate(self.walkability):
            if value != 1:
                continue
            x, z = divmod(index, 5)
            indices = [(x * 5 + y) * 5 + z for y in (2, 3, 1)]
            if (any(not grid.loaded[i] or grid.fluid[i] != 0 for i in indices) or
                    [grid.collision[i] for i in indices] != [0, 0, 1]):
                raise ValueError('Walkable cells require known clearance and full dry support.')
        return self


def validate_frame(payload):
    return NavigationFrame.model_validate(payload).model_dump(mode='json')


def cell_position(frame):
    return tuple(int(frame['observation']['terrain']['center'][axis]) for axis in ('x', 'y', 'z'))


def encode(frame, goal, visits):
    """Categorical terrain and relative goal; never raw IDs, oracle maps or rewards."""
    frame = validate_frame(frame)
    if len(goal) != 3 or any(type(n) is not int for n in goal):
        raise ValueError('Goal must be three integer block coordinates.')
    x, y, z = cell_position(frame)
    if goal[1] != y or abs(goal[0] - x) > 16 or abs(goal[2] - z) > 16:
        raise ValueError('This first policy supports same-level local goals within 16 blocks per axis.')
    categories = np.array(frame['walkability']) + 1
    cells = np.eye(3, dtype=np.float64)[categories].ravel()
    history = [min(4, visits.get((x + dx, y, z + dz), 0)) / 4
               for dx in range(-2, 3) for dz in range(-2, 3)]
    health = frame['observation']['state'].get('stats', {}).get('health')
    if type(health) not in (int, float) or not isfinite(health) or not 0 <= health <= 20:
        raise ValueError('Navigation requires finite health in the range 0–20.')
    # External run/step caps are truncations, not a task's terminal state.
    # Do not feed a countdown to the value network then bootstrap at zero.
    features = np.concatenate((cells, [(goal[0] - x) / 16, (goal[2] - z) / 16], history, [health / 20]))
    return features, np.array(frame['actionMask'], dtype=bool)


def transition_reward(before, after, goal, elapsed_seconds, failed=False):
    """Server evidence in Minecraft; simulator evidence only in the grid backend."""
    if not isfinite(elapsed_seconds) or elapsed_seconds <= 0:
        raise ValueError('An option needs positive finite elapsed time.')
    for state in (before, after):
        if not all(isfinite(state['position'][a]) for a in ('x', 'y', 'z')) or not isfinite(state['health']):
            raise ValueError('Nonfinite grading evidence.')
    duration = max(1.0, elapsed_seconds / TIME_UNIT)
    discount = GAMMA ** duration
    phi = lambda s: -hypot(s['position']['x'] - goal[0] - 0.5, s['position']['z'] - goal[2] - 0.5) / 16
    damaged = after['health'] < before['health'] or after.get('unsafe_breaks', 0) > before.get('unsafe_breaks', 0)
    excavation = len(after.get('broken', [])) > len(before.get('broken', []))
    success = (not damaged and not excavation and not failed and after.get('on_ground') is True and
               tuple(floor(after['position'][a]) for a in ('x', 'y', 'z')) == tuple(goal))
    terminated = success or damaged or excavation
    # Terminal potential is zero. Duration-aware potential shaping cannot
    # reward cycling through the same cells once discounted returns are used.
    reward = discount * (0 if terminated else phi(after)) - phi(before) - 0.02 * duration
    reward += 3.0 if success else -3.0 if damaged or excavation else -0.2 if failed else 0.0
    return float(reward), float(discount), terminated, success
