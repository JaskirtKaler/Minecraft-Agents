"""Convenience transfers cannot bypass the controller's fixed destination/count."""
import unittest

from agent.tool_agent import constrained_transfer


class ConvenienceTransferTests(unittest.TestCase):
    def setUp(self):
        self.position = {'x': 2, 'y': 64, 'z': 3}
        self.goal = {'kind': 'container_gain', 'item': 'oak_log', 'count': 24, 'position': self.position}
        self.action = {'name': 'deposit_item', 'args': {'item': 'oak_log', 'count': 24}}

    def test_convenience_transfer_is_bound_to_the_frozen_goal_chest(self):
        issued = constrained_transfer(self.action, [self.goal], [5], [])
        self.assertEqual(issued['name'], 'deposit_item')
        self.assertEqual(issued['args'], self.action['args'])
        self.assertEqual(issued['container_constraint'], {'position': self.position, 'item': 'oak_log', 'maximum': 29})
        self.assertNotIn('container_constraint', self.action)

    def test_partial_delivery_rejects_total_and_keeps_model_selected_remainder(self):
        evidence = [{'goal': self.goal, 'before': 5, 'observed': 13}]
        with self.assertRaisesRegex(ValueError, 'overshoot'):
            constrained_transfer(self.action, [self.goal], [5], evidence)
        self.action['args']['count'] = 16
        issued = constrained_transfer(self.action, [self.goal], [5], evidence)
        self.assertEqual(issued['args']['count'], 16)
        self.assertEqual(issued['container_constraint']['maximum'], 29)

    def test_multiple_goal_chests_require_a_model_selected_explicit_destination(self):
        other = {**self.goal, 'position': {'x': 5, 'y': 64, 'z': 3}}
        with self.assertRaisesRegex(ValueError, 'explicit deposit'):
            constrained_transfer(self.action, [self.goal, other], [5, 0], [])
        explicit = {'name': 'deposit', 'args': {**self.action['args'], 'position': other['position']}}
        issued = constrained_transfer(explicit, [self.goal, other], [5, 0], [])
        self.assertEqual(issued['container_constraint']['position'], other['position'])

    def test_duplicate_chest_predicates_do_not_create_ambiguity(self):
        reordered = {**self.goal, 'position': {'z': 3, 'y': 64, 'x': 2}}
        issued = constrained_transfer(self.action, [self.goal, reordered], [5, 5], [])
        self.assertEqual(issued['container_constraint']['maximum'], 29)

    def test_model_supplied_constraint_is_discarded(self):
        self.action['container_constraint'] = {'maximum': 9999}
        issued = constrained_transfer(self.action, [self.goal], [5], [])
        self.assertEqual(issued['container_constraint']['maximum'], 29)

    def test_unrelated_non_transfer_and_legacy_transfer_are_unchanged(self):
        issued = constrained_transfer(self.action, [], [], [])
        self.assertNotIn('container_constraint', issued)
        action = {'name': 'mine_logs', 'args': {'item': 'oak_log', 'count': 24}}
        self.assertEqual(constrained_transfer(action, [self.goal], [5], []), action)


if __name__ == '__main__':
    unittest.main()
