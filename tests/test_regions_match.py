"""Exact-region contracts for generic, model-directed construction goals."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.goal_contract import check_quantities
from agent.planner_schema import decision_schema
from agent.regions import RegionError, compile_goal, expected_block_counts, inspect_bounds, position_dict, position_key
from agent.tool_agent import ModelToolAgent, SYSTEM, TOOLS, compact_evidence, goal_meaning, goal_progress, validate_goals


def house_goal(must_change=True):
    """Geometry only: floor, walls, roof, interior and a two-cell doorway."""
    return {
        'kind': 'regions_match',
        'regions': [
            {'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 9, 'y': 64, 'z': 9},
             'block': 'cobblestone', 'mode': 'solid'},
            {'min': {'x': 0, 'y': 65, 'z': 0}, 'max': {'x': 9, 'y': 67, 'z': 9},
             'block': 'cobblestone', 'mode': 'perimeter_xz'},
            {'min': {'x': 0, 'y': 68, 'z': 0}, 'max': {'x': 9, 'y': 68, 'z': 9},
             'block': 'cobblestone', 'mode': 'solid'},
            {'min': {'x': 1, 'y': 65, 'z': 1}, 'max': {'x': 8, 'y': 67, 'z': 8},
             'block': 'air', 'mode': 'solid'},
        ],
        'exceptions': [
            {'position': {'x': 4, 'y': 65, 'z': 0}, 'block': 'air'},
            {'position': {'x': 4, 'y': 66, 'z': 0}, 'block': 'air'},
        ],
        'must_change': must_change,
    }


def survey_cells(bounds, names):
    minimum, maximum = bounds['min'], bounds['max']
    return [
        {'position': position_dict((x, y, z)), 'name': names.get(position_key((x, y, z)), 'unknown'),
         **({} if names.get(position_key((x, y, z)), 'unknown') != 'unknown' else {'loaded': False})}
        for x in range(minimum['x'], maximum['x'] + 1)
        for y in range(minimum['y'], maximum['y'] + 1)
        for z in range(minimum['z'], maximum['z'] + 1)
    ]


class RegionCompilerTests(unittest.TestCase):
    def test_complete_house_contract_counts_all_final_cells_not_samples(self):
        goal = house_goal()
        cells = compile_goal(goal)
        self.assertEqual(len(cells), 500)  # full 10 x 5 x 10 result, including required air
        self.assertEqual(expected_block_counts(cells), {'air': 194, 'cobblestone': 306})
        self.assertEqual(inspect_bounds(cells), [{
            'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 9, 'y': 68, 'z': 9},
        }])
        self.assertEqual(validate_goals([goal]), [goal])
        self.assertIn('all 500 declared region cells', goal_meaning(goal))

    def test_schema_has_bounded_geometry_and_exception_contract(self):
        branches = decision_schema(TOOLS)['oneOf'][0]['properties']['goals']['items']['oneOf']
        region = next(branch for branch in branches if branch['properties']['kind'] == {'const': 'regions_match'})
        self.assertEqual(region['required'], ['kind', 'regions', 'must_change'])
        self.assertEqual(region['properties']['regions']['maxItems'], 16)
        self.assertEqual(region['properties']['exceptions']['maxItems'], 64)
        self.assertIn('perimeter_xz', region['properties']['regions']['items']['properties']['mode']['enum'])
        self.assertIn('regions_match checks every compiled cell', SYSTEM)

    def test_perimeter_is_partitioned_into_exact_bounded_surveys(self):
        goal = {
            'kind': 'regions_match',
            'regions': [{'min': {'x': -5, 'y': 64, 'z': -5}, 'max': {'x': 4, 'y': 66, 'z': 4},
                         'block': 'cobblestone', 'mode': 'perimeter_xz'}],
        }
        expected = set(compile_goal(goal))
        covered = set()
        for bounds in inspect_bounds(compile_goal(goal)):
            minimum, maximum = bounds['min'], bounds['max']
            cells = {
                (x, y, z)
                for x in range(minimum['x'], maximum['x'] + 1)
                for y in range(minimum['y'], maximum['y'] + 1)
                for z in range(minimum['z'], maximum['z'] + 1)
            }
            self.assertLessEqual(len(cells), 4096)
            self.assertTrue(cells <= expected)
            self.assertFalse(covered & cells)
            covered.update(cells)
        self.assertEqual(covered, expected)

    def test_conflicting_or_outside_geometry_is_rejected_before_goal_freeze(self):
        conflict = {
            'kind': 'regions_match',
            'regions': [
                {'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 0, 'y': 64, 'z': 0},
                 'block': 'cobblestone', 'mode': 'solid'},
                {'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 0, 'y': 64, 'z': 0},
                 'block': 'dirt', 'mode': 'solid'},
            ],
        }
        with self.assertRaisesRegex(ValueError, 'conflicting non-exception'):
            validate_goals([conflict])

        interior_exception = {
            'kind': 'regions_match',
            'regions': [{'min': {'x': 0, 'y': 64, 'z': 0}, 'max': {'x': 2, 'y': 64, 'z': 2},
                         'block': 'cobblestone', 'mode': 'perimeter_xz'}],
            'exceptions': [{'position': {'x': 1, 'y': 64, 'z': 1}, 'block': 'air'}],
        }
        with self.assertRaisesRegex(ValueError, 'outside the covered region'):
            validate_goals([interior_exception])

        too_large = {
            'kind': 'regions_match',
            'regions': [{'min': {'x': 0, 'y': 0, 'z': 0}, 'max': {'x': 2304, 'y': 0, 'z': 0},
                         'block': 'cobblestone', 'mode': 'solid'}],
        }
        with self.assertRaisesRegex(ValueError, 'limited to 2304'):
            validate_goals([too_large])


class RegionGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_grounding_checks_registry_including_air_and_reads_every_cell(self):
        goal = house_goal()
        expected = compile_goal(goal)
        expected_names = {position_key(position): block for position, block in expected.items()}
        calls = []

        async def execute(tool):
            calls.append(tool)
            if tool['name'] == 'inspect':
                return {'success': True, 'data': {'isBlock': True}}
            self.assertEqual(tool['name'], 'inspect_region')
            return {'success': True, 'data': {'cells': survey_cells(tool['args'], expected_names)}}

        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.bridge = SimpleNamespace(execute_tool=AsyncMock(side_effect=execute))
        baselines = await agent.ground_goals([goal], {})
        self.assertEqual(baselines, [expected_names])
        registry = {call['args']['item'] for call in calls if call['name'] == 'inspect'}
        self.assertEqual(registry, {'air', 'cobblestone'})
        surveys = [call for call in calls if call['name'] == 'inspect_region']
        self.assertEqual(len(surveys), 1)
        self.assertEqual(len(survey_cells(surveys[0]['args'], expected_names)), 500)

    async def test_unknown_or_missing_survey_cells_fail_instead_of_becoming_air(self):
        goal = house_goal()
        expected = compile_goal(goal)
        names = {position_key(position): block for position, block in expected.items()}
        agent = ModelToolAgent.__new__(ModelToolAgent)

        async def unknown(tool):
            cells = survey_cells(tool['args'], names)
            cells[0] = {**cells[0], 'name': 'unknown', 'loaded': False}
            return {'success': True, 'data': {'cells': cells}}

        agent.bridge = SimpleNamespace(execute_tool=AsyncMock(side_effect=unknown))
        with self.assertRaisesRegex(ValueError, 'unloaded or unknown'):
            await agent.measure_regions(goal)


class RegionVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_must_change_requires_every_final_nonair_cell_to_be_new_but_not_air(self):
        goal = house_goal(must_change=True)
        expected = compile_goal(goal)
        after = {position_key(position): block for position, block in expected.items()}
        before = {key: 'air' for key in after}
        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.measure = AsyncMock(return_value=after)
        met, evidence = await agent.check([goal], [before], {})
        self.assertTrue(met)
        self.assertEqual(evidence[0]['summary']['new_nonair'], 306)
        self.assertEqual(evidence[0]['summary']['unchanged_nonair_count'], 0)

        existing = dict(before)
        cobble_key = next(position_key(position) for position, block in expected.items() if block == 'cobblestone')
        existing[cobble_key] = 'cobblestone'
        agent.measure = AsyncMock(return_value=after)
        met, evidence = await agent.check([goal], [existing], {})
        self.assertFalse(met)
        self.assertEqual(evidence[0]['summary']['unchanged_nonair_count'], 1)

        incomplete = dict(before)
        incomplete.pop(cobble_key)
        agent.measure = AsyncMock(return_value=after)
        met, evidence = await agent.check([goal], [incomplete], {})
        self.assertFalse(met)
        self.assertEqual(evidence[0]['summary']['baseline_missing_count'], 1)

    async def test_prompt_evidence_is_compact_while_saved_evidence_keeps_all_cells(self):
        goal = house_goal()
        expected = compile_goal(goal)
        before = {position_key(position): 'air' for position in expected}
        after = {position_key(position): block for position, block in expected.items()}
        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.measure = AsyncMock(return_value=after)
        _, evidence = await agent.check([goal], [before], {})
        self.assertEqual(len(evidence[0]['observed']), 500)
        compact = compact_evidence(evidence)
        self.assertNotIn('before', compact[0])
        self.assertNotIn('observed', compact[0])
        self.assertEqual(compact[0]['region']['cell_count'], 500)
        self.assertEqual(len(compact[0]['region']['mismatched_coordinates']), 0)
        progress = goal_progress(evidence)[0]
        self.assertEqual(progress['remaining'], 0)
        self.assertEqual(progress['region']['expected_blocks']['cobblestone'], 306)


class RegionQuantityContractTests(unittest.TestCase):
    def test_place_new_counts_final_nonair_region_cells_not_air_or_recipe_inputs(self):
        goal = house_goal(must_change=True)
        requirement = [{'outcome': 'place_new', 'item': 'cobblestone', 'count': 306, 'comparison': 'exact'}]
        check_quantities(requirement, [goal], {}, {})
        wrong = [{**requirement[0], 'count': 305}]
        with self.assertRaisesRegex(ValueError, 'proposed final predicates imply 306'):
            check_quantities(wrong, [goal], {}, {})
        no_change = house_goal(must_change=False)
        with self.assertRaisesRegex(ValueError, 'proposed final predicates imply 0'):
            check_quantities(requirement, [no_change], {}, {})


if __name__ == '__main__':
    unittest.main()
