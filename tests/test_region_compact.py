"""Planner-prompt encoding tests for complete inspect_region observations."""
import unittest

from agent.tool_agent import compact


def position(x, y=64, z=0):
    return {'x': x, 'y': y, 'z': z}


def survey_entry(minimum, maximum, cells, *, success=True):
    return {
        'step': 1,
        'action': {'name': 'inspect_region', 'args': {'min': minimum, 'max': maximum}},
        'result': {
            'success': success,
            'verified': success,
            'data': {'min': minimum, 'max': maximum, 'cell_count': len(cells), 'cells': cells},
        },
    }


def expand_rows(rows):
    expanded = {}
    for row in rows:
        for run in row['runs']:
            for x in range(run['x'][0], run['x'][1] + 1):
                expanded[(x, row['y'], row['z'])] = run['cell']
    return expanded


class RegionPromptCompactionTests(unittest.TestCase):
    def test_lossless_runs_keep_cells_beyond_eight_and_unknown_explicit(self):
        minimum, maximum = position(-2), position(7)
        cells = [{'position': position(x), 'name': 'air'} for x in range(-2, 8)]
        cells[-3] = {'position': position(5), 'name': 'oak_log', 'properties': {'axis': 'y'}}
        cells[-1] = {'position': position(7), 'name': 'unknown', 'loaded': False}
        entry = survey_entry(minimum, maximum, cells)

        compacted = compact([entry])[0]
        survey = compacted['result']['data']['region_survey']

        self.assertTrue(survey['coverage']['complete'])
        self.assertTrue(survey['encoding']['lossless'])
        self.assertFalse(survey['encoding']['truncated'])
        self.assertEqual(survey['bounds'], {'min': minimum, 'max': maximum})
        self.assertEqual(survey['counts']['by_name'], {'air': 8, 'oak_log': 1, 'unknown': 1})
        self.assertEqual(survey['counts']['unknown_or_unloaded'], 1)
        self.assertTrue(survey['requires_narrower_query'])
        self.assertIn('unknown or unloaded', survey['planner_notice'])
        self.assertEqual(expand_rows(survey['rows'])[(5, 64, 0)], {'name': 'oak_log', 'properties': {'axis': 'y'}})
        self.assertEqual(expand_rows(survey['rows'])[(7, 64, 0)], {'name': 'unknown', 'loaded': False})
        # Prompt compaction must not alter the exact trace used by verification.
        self.assertEqual(len(entry['result']['data']['cells']), 10)
        self.assertEqual(entry['result']['data']['cells'][-1]['name'], 'unknown')

    def test_run_merging_preserves_full_cell_descriptor_and_negative_multiaxis_coordinates(self):
        minimum, maximum = position(-1, 63, -2), position(1, 64, -1)
        cells = []
        for y in range(63, 65):
            for z in range(-2, 0):
                for x in range(-1, 2):
                    properties = {'facing': 'north'} if x < 0 else {'facing': 'south'}
                    cells.append({'position': position(x, y, z), 'name': 'oak_stairs', 'properties': properties})
        survey = compact(survey_entry(minimum, maximum, cells))['result']['data']['region_survey']

        self.assertTrue(survey['encoding']['lossless'])
        expanded = expand_rows(survey['rows'])
        self.assertEqual(len(expanded), 12)
        self.assertEqual(expanded[(-1, 63, -2)], {'name': 'oak_stairs', 'properties': {'facing': 'north'}})
        self.assertEqual(expanded[(0, 64, -1)], {'name': 'oak_stairs', 'properties': {'facing': 'south'}})
        first_row = next(row for row in survey['rows'] if row['y'] == 63 and row['z'] == -2)
        self.assertEqual(first_row['runs'], [
            {'x': [-1, -1], 'cell': {'name': 'oak_stairs', 'properties': {'facing': 'north'}}},
            {'x': [0, 1], 'cell': {'name': 'oak_stairs', 'properties': {'facing': 'south'}}},
        ])

    def test_prompt_budget_is_explicit_when_alternating_cells_need_too_many_runs(self):
        minimum, maximum = position(0), position(255)
        cells = [{'position': position(x), 'name': 'air' if x % 2 == 0 else 'stone'} for x in range(256)]
        entry = survey_entry(minimum, maximum, cells)
        survey = compact(entry)['result']['data']['region_survey']

        self.assertTrue(survey['coverage']['complete'])
        self.assertTrue(survey['encoding']['truncated'])
        self.assertFalse(survey['encoding']['lossless'])
        self.assertEqual(survey['encoding']['total_runs'], 256)
        self.assertEqual(survey['encoding']['emitted_runs'], 128)
        self.assertEqual(survey['counts']['by_name'], {'air': 128, 'stone': 128})
        self.assertTrue(survey['requires_narrower_query'])
        self.assertIn('known_rows', survey)
        self.assertNotIn('rows', survey)
        self.assertIn('Do not assume omitted cells are air', survey['planner_notice'])

    def test_malformed_coverage_is_never_presented_as_a_complete_survey(self):
        minimum, maximum = position(0), position(2)
        cells = [
            {'position': position(0), 'name': 'air'},
            {'position': position(0), 'name': 'stone'},
            {'position': position(4), 'name': 'dirt'},
        ]
        survey = compact(survey_entry(minimum, maximum, cells))['result']['data']['region_survey']

        self.assertFalse(survey['coverage']['complete'])
        self.assertEqual(survey['coverage']['missing'], 2)
        self.assertEqual(survey['coverage']['duplicate'], 1)
        self.assertEqual(survey['coverage']['outside'], 1)
        self.assertFalse(survey['encoding']['lossless'])
        self.assertTrue(survey['requires_narrower_query'])
        self.assertIn('known_rows', survey)

    def test_non_region_and_failed_region_results_keep_generic_compaction(self):
        ordinary = {'action': {'name': 'find_blocks'}, 'result': {'success': True, 'data': {'items': list(range(10))}}}
        self.assertEqual(compact(ordinary)['result']['data']['items'], list(range(8)))

        cells = [{'position': position(x), 'name': 'air'} for x in range(10)]
        failed = compact(survey_entry(position(0), position(9), cells, success=False))
        self.assertNotIn('region_survey', failed['result']['data'])
        self.assertEqual(len(failed['result']['data']['cells']), 8)


if __name__ == '__main__':
    unittest.main()
