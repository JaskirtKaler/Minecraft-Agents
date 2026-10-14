"""Generic declarative box boundaries, never a runtime build sequence."""
import unittest

from agent.regions import RegionError, compile_goal, _cell_count
from agent.tool_agent import compiled_spatial_contracts, repeated_without_progress


def region(low, high, block='cobblestone', mode='shell_3d'):
    return {'min': dict(zip('xyz', low)), 'max': dict(zip('xyz', high)), 'block': block, 'mode': mode}


class ShellGeometryTests(unittest.TestCase):
    def test_six_faces_exclude_interior_and_keep_negative_coordinates(self):
        cells = compile_goal({'kind': 'regions_match', 'regions': [region((-5, 60, -9), (-1, 63, -6))]})
        self.assertEqual(len(cells), 68)
        self.assertEqual(len(cells), _cell_count((-5, 60, -9), (-1, 63, -6), 'shell_3d'))
        self.assertNotIn((-3, 61, -8), cells, 'An omitted interior is not implicitly air.')
        self.assertEqual(cells[(-3, 60, -8)], 'cobblestone')
        self.assertEqual(cells[(-3, 63, -8)], 'cobblestone')
        self.assertEqual(cells[(-5, 61, -8)], 'cobblestone')

    def test_model_can_explicitly_constrain_disjoint_interior_and_openings(self):
        p = {'x': -3, 'y': 61, 'z': -6}
        goal = {'kind': 'regions_match', 'regions': [region((-5, 60, -9), (-1, 63, -6)),
            region((-4, 61, -8), (-2, 62, -7), 'air', 'solid')],
            'exceptions': [{'position': p, 'block': 'air'}], 'must_change': False}
        cells = compile_goal(goal)
        self.assertEqual(len(cells), 80)
        self.assertEqual(cells[(-3, 61, -8)], 'air')
        self.assertEqual(cells[tuple(p.values())], 'air')
        contract = compiled_spatial_contracts([goal])[0]
        self.assertEqual(contract['required_blocks']['air']['required_cell_count'], 13)
        expanded = {}
        for block, facts in contract['required_blocks'].items():
            for box in facts['boxes']:
                expanded.update(compile_goal({'kind': 'regions_match', 'regions': [{**box, 'block': block, 'mode': 'solid'}]}))
        self.assertEqual(expanded, cells)

    def test_thin_boxes_do_not_drop_any_cells_or_miscount(self):
        for high in ((0, 10, 10), (10, 1, 10), (10, 10, 0), (1, 1, 1)):
            with self.subTest(high=high):
                cells = compile_goal({'kind': 'regions_match', 'regions': [region((0, 0, 0), high)]})
                self.assertEqual(len(cells), _cell_count((0, 0, 0), high, 'shell_3d'))
                self.assertEqual(len(cells), (high[0] + 1) * (high[1] + 1) * (high[2] + 1))

    def test_shell_does_not_relax_overlap_exception_or_size_limits(self):
        shell = region((-5, 60, -9), (-1, 63, -6))
        with self.assertRaisesRegex(RegionError, 'overlap'):
            compile_goal({'kind': 'regions_match', 'regions': [shell,
                region((-5, 60, -9), (-1, 63, -6), 'air', 'solid')]})
        with self.assertRaisesRegex(RegionError, 'outside'):
            compile_goal({'kind': 'regions_match', 'regions': [shell], 'exceptions': [
                {'position': {'x': -3, 'y': 61, 'z': -8}, 'block': 'air'}]})
        with self.assertRaisesRegex(RegionError, 'limited'):
            compile_goal({'kind': 'regions_match', 'regions': [region((0, 0, 0), (100, 100, 100))]})

    def test_refresh_queries_never_erase_failed_physical_attempts(self):
        read = {'name': 'inspect_region', 'args': {'min': {'x': 0, 'y': 0, 'z': 0}, 'max': {'x': 1, 'y': 0, 'z': 0}}}
        dig = {'name': 'dig', 'args': {'position': {'x': 0, 'y': 0, 'z': 0}}}
        trace = [{'action': a, 'progress_made': False} for a in (dig, read, dig, read)]
        self.assertTrue(repeated_without_progress(read, trace))
        self.assertTrue(repeated_without_progress(dig, trace))
        trace[1]['result'], trace[3]['result'] = {'data': {'name': 'air'}}, {'data': {'name': 'stone'}}
        self.assertFalse(repeated_without_progress(read, trace))
        self.assertTrue(repeated_without_progress(dig, trace))
