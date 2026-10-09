"""Node/Python perception contracts and bridge correlation, without game/model calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from pydantic import ValidationError

from agent.bridge import MineflayerBridge
from agent.observations import validate_observation


ROOT = Path(__file__).resolve().parents[1]


def node_snapshot():
    result = subprocess.run(['node', '-e',
        "const {fixture}=require('./tests/test_observations');"
        "const {getObservation}=require('./bot/observations');"
        "process.stdout.write(JSON.stringify(getObservation(fixture().bot)));"],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the cross-runtime fixture')
class ObservationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = node_snapshot()

    def test_node_snapshot_validates_and_returns_detached_data(self):
        parsed = validate_observation(self.snapshot)
        self.assertEqual(parsed, self.snapshot)
        parsed['terrain']['loaded'][0] = False
        self.assertTrue(self.snapshot['terrain']['loaded'][0])
        self.assertEqual(parsed['terrain']['center']['x'], -1)

    def test_wrong_version_and_unknown_fields_are_rejected(self):
        for changes in ({'schemaVersion': 2}, {'source': 'global-server-oracle'},
                        {'lineOfSightFiltered': True}, {'landmarkListComplete': True}, {'reward': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_observation({**self.snapshot, **changes})

    def test_terrain_shape_channel_lengths_and_bounds_are_validated(self):
        for shape in ([2, 5, 2], [13, 5, 13], [7, 9, 7], [7, 5, 9], [0, 5, 0]):
            data = deepcopy(self.snapshot)
            data['terrain']['shape'] = shape
            with self.subTest(shape=shape), self.assertRaises(ValidationError):
                validate_observation(data)
        for channel in ('blockIds', 'stateIds', 'loaded', 'collision', 'fluid'):
            data = deepcopy(self.snapshot)
            data['terrain'][channel].pop()
            with self.subTest(channel=channel), self.assertRaises(ValidationError):
                validate_observation(data)

    def test_unknown_cells_cannot_be_fabricated_as_air(self):
        index = self.snapshot['terrain']['loaded'].index(False)
        for channel, value in (('blockIds', 0), ('stateIds', 0), ('collision', 0), ('fluid', 0)):
            data = deepcopy(self.snapshot)
            data['terrain'][channel][index] = value
            with self.subTest(channel=channel), self.assertRaises(ValidationError):
                validate_observation(data)
        data = deepcopy(self.snapshot)
        data['terrain']['fullyLoaded'] = True
        with self.assertRaises(ValidationError):
            validate_observation(data)

    def test_invalid_coordinates_counts_and_ids_are_rejected(self):
        for channel in ('blockIds', 'stateIds'):
            data = deepcopy(self.snapshot)
            data['terrain'][channel][0] = -1
            with self.subTest(channel=channel), self.assertRaises(ValidationError):
                validate_observation(data)
        for value in (float('nan'), float('inf')):
            data = deepcopy(self.snapshot)
            data['motion']['yaw'] = value
            with self.assertRaises(ValidationError):
                validate_observation(data)
        data = deepcopy(self.snapshot)
        data['inventoryDetails'][0]['count'] = 65
        with self.assertRaises(ValidationError):
            validate_observation(data)

    def test_not_ready_cannot_contain_actionable_perception(self):
        data = deepcopy(self.snapshot)
        data['state']['ready'] = False
        with self.assertRaises(ValidationError):
            validate_observation(data)
        data.update(motion=None, terrain=None, inventoryDetails=[], landmarks=[])
        self.assertFalse(validate_observation(data)['state']['ready'])
        for changes in ({'motion': None}, {'terrain': None}, {'gameVersion': None}):
            with self.assertRaises(ValidationError):
                validate_observation({**self.snapshot, **changes})

    def test_identity_and_relative_geometry_cannot_be_omitted_or_mismatched(self):
        for key in ('id', 'dimension', 'sessionId'):
            data = deepcopy(self.snapshot)
            del data['state']['world'][key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                validate_observation(data)
        data = deepcopy(self.snapshot)
        data['terrain']['center']['x'] = 0
        with self.assertRaises(ValidationError):
            validate_observation(data)
        for key, value in (('relative', {'x': 999, 'y': 0, 'z': 0}), ('distance', 999)):
            data = deepcopy(self.snapshot)
            data['landmarks'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValidationError):
                validate_observation(data)


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the cross-runtime fixture')
class ObservationBridgeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = node_snapshot()

    def bridge(self, *, payload=None, error=None):
        bridge = MineflayerBridge()
        requests = []

        class Client:
            async def send(self, message):
                request = json.loads(message)
                requests.append(request)
                response = {'type': 'observation_response', 'id': request['id']}
                if error:
                    response['error'] = {'message': error}
                else:
                    response['data'] = deepcopy(payload if payload is not None else self_snapshot)
                await bridge._route_message(response)

        self_snapshot = self.snapshot
        bridge.active_client = Client()
        return bridge, requests

    async def test_correlated_snapshot_updates_only_compact_state(self):
        bridge, requests = self.bridge()
        callbacks = []
        bridge.state_callbacks.append(callbacks.append)
        result = await bridge.get_observation(horizontal_radius=1, vertical_radius=1)
        self.assertEqual(requests[0]['type'], 'get_observation')
        self.assertEqual(requests[0]['options'], {'horizontalRadius': 1, 'verticalRadius': 1})
        self.assertEqual(result, self.snapshot)
        self.assertEqual(bridge.latest_state, self.snapshot['state'])
        self.assertEqual(callbacks, [self.snapshot['state']])
        self.assertFalse(bridge.pending_requests)

    async def test_error_and_invalid_schema_do_not_update_checkpoint(self):
        for options, expected in (({'error': 'bad radius'}, ValueError),
                                 ({'payload': {}}, ValidationError)):
            bridge, requests = self.bridge(**options)
            with self.assertRaises(expected):
                await bridge.get_observation()
            self.assertEqual(len(requests), 1)
            self.assertEqual(bridge.latest_state, {})
            self.assertFalse(bridge.pending_requests)

    async def test_invalid_local_options_never_send_a_request(self):
        bridge, requests = self.bridge()
        for options in ({'horizontal_radius': -1}, {'horizontal_radius': 6},
                        {'vertical_radius': 4}, {'horizontal_radius': True}):
            with self.assertRaises(ValueError):
                await bridge.get_observation(**options)
        self.assertFalse(requests)

    async def test_timeout_clears_request_without_cancelling_physical_work(self):
        bridge = MineflayerBridge()
        requests = []

        class Client:
            async def send(self, message):
                requests.append(json.loads(message))

        bridge.active_client = Client()
        with self.assertRaises(asyncio.TimeoutError):
            await bridge.get_observation(timeout=0.01)
        self.assertEqual([r['type'] for r in requests], ['get_observation'])
        self.assertFalse(bridge.pending_requests)


if __name__ == '__main__':
    unittest.main()
