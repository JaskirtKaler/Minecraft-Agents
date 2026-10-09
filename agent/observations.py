"""Versioned Mineflayer observations. No inference, actions, rewards, or training."""
from math import floor, isclose, sqrt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Vector(Contract):
    x: float
    y: float
    z: float


class Motion(Contract):
    position: Vector
    yaw: float | None
    pitch: float | None
    velocity: Vector | None
    onGround: bool | None


class Terrain(Contract):
    center: Vector
    shape: list[int] = Field(min_length=3, max_length=3)
    axisOrder: Literal['xyz']
    blockIds: list[int | None]
    stateIds: list[int | None]
    loaded: list[bool]
    collision: list[Literal[-1, 0, 1, 2]]
    fluid: list[Literal[-1, 0, 1, 2]]
    fullyLoaded: bool

    @model_validator(mode='after')
    def check_grid(self):
        x, y, z = self.shape
        if x != z or any(n < 1 or n % 2 != 1 for n in self.shape) or x > 11 or y > 7:
            raise ValueError('Terrain shape must be a bounded odd grid, at most 11 by 7 by 11.')
        if any(v != int(v) for v in (self.center.x, self.center.y, self.center.z)):
            raise ValueError('Terrain center must use floored block coordinates.')
        cells = x * y * z
        channels = (self.blockIds, self.stateIds, self.loaded, self.collision, self.fluid)
        if any(len(values) != cells for values in channels):
            raise ValueError('Every terrain channel must match the grid shape.')
        for index, loaded in enumerate(self.loaded):
            if any(values[index] is not None and values[index] < 0 for values in (self.blockIds, self.stateIds)):
                raise ValueError('Registry IDs must be nonnegative or null.')
            if not loaded and (self.blockIds[index] is not None or self.stateIds[index] is not None or
                               self.collision[index] != -1 or self.fluid[index] != -1):
                raise ValueError('Unloaded cells must remain explicitly unknown.')
        if self.fullyLoaded != all(self.loaded):
            raise ValueError('fullyLoaded must agree with the loaded mask.')
        return self


class InventoryItem(Contract):
    name: str = Field(min_length=1)
    count: int = Field(ge=1, le=64)
    slot: int = Field(ge=0, le=45)
    itemId: int | None = Field(ge=0)
    durabilityUsed: int | None = Field(ge=0)
    maxDurability: int | None = Field(ge=1)


class Landmark(Contract):
    name: str = Field(min_length=1)
    position: Vector
    relative: Vector
    distance: float = Field(ge=0)


class MinecraftObservation(Contract):
    schemaVersion: Literal[1]
    source: Literal['mineflayer']
    gameVersion: str | None
    state: dict[str, Any]
    motion: Motion | None
    terrain: Terrain | None
    inventoryDetails: list[InventoryItem] = Field(max_length=46)
    landmarks: list[Landmark] = Field(max_length=24)
    landmarkListComplete: Literal[False]
    lineOfSightFiltered: Literal[False]

    @model_validator(mode='after')
    def check_readiness(self):
        ready = self.state.get('ready')
        if type(ready) is not bool:
            raise ValueError('Observation state must declare readiness.')
        world = self.state.get('world', {})
        if not isinstance(world, dict) or any(not isinstance(world.get(key), str) or not world[key]
                                             for key in ('id', 'dimension', 'sessionId')):
            raise ValueError('Observation state requires world, dimension, and session identity.')
        if not isinstance(self.state.get('observedAt'), str) or not self.state['observedAt']:
            raise ValueError('Observation state requires its observation timestamp.')
        if ready and (self.motion is None or self.terrain is None or not self.gameVersion):
            raise ValueError('Spawned observations require motion, terrain, and game version.')
        if not ready and (self.motion is not None or self.terrain is not None or
                          self.inventoryDetails or self.landmarks):
            raise ValueError('Unspawned observations must not contain actionable perception.')
        if ready:
            position = self.motion.position
            if any(getattr(self.terrain.center, axis) != floor(getattr(position, axis))
                   for axis in ('x', 'y', 'z')):
                raise ValueError('Terrain center must match the current floored position.')
            for landmark in self.landmarks:
                if any(not isclose(getattr(landmark.relative, axis),
                                   getattr(landmark.position, axis) - getattr(position, axis), abs_tol=1e-7)
                       for axis in ('x', 'y', 'z')):
                    raise ValueError('Landmark coordinates must be relative to the current position.')
                distance = sqrt(sum(getattr(landmark.relative, axis) ** 2 for axis in ('x', 'y', 'z')))
                if not isclose(landmark.distance, distance, abs_tol=1e-7):
                    raise ValueError('Landmark distance must agree with its relative coordinates.')
        return self


def validate_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate JSON from the bot and return a detached, JSON-compatible snapshot."""
    return MinecraftObservation.model_validate(payload).model_dump(mode='json')
