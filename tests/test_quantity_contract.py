"""Requested exact quantities cannot be weakened to inventory lower bounds."""
import unittest
from unittest.mock import AsyncMock

from agent.goal_contract import INTENT_SYSTEM, check_quantities
from agent.tool_agent import ModelToolAgent, quantity_contract


class RetainedQuantityContractTests(unittest.TestCase):
    def check(self, goals, count=2, comparison='exact', initial=0):
        state = {'inventory': [{'name': 'oak_planks', 'count': initial}]} if initial else {'inventory': []}
        requirements = [{'outcome': 'retain_new', 'item': 'oak_planks',
                         'count': count, 'comparison': comparison}]
        check_quantities(requirements, goals, {'oak_planks': initial}, quantity_contract(goals, state))

    def gain(self, count=2, comparison=None, item='oak_planks'):
        goal = {'kind': 'inventory_gain', 'item': item, 'count': count}
        if comparison is not None:
            goal['comparison'] = comparison
        return goal

    def test_exact_request_rejects_minimum_gain_of_same_count(self):
        with self.assertRaisesRegex(ValueError, 'no exact inventory-gain bound'):
            self.check([self.gain(comparison='at_least')])

    def test_exact_request_rejects_absolute_minimum_even_with_starting_stock(self):
        goal = {'kind': 'inventory_at_least', 'item': 'oak_planks', 'count': 7}
        with self.assertRaisesRegex(ValueError, 'no exact inventory-gain bound'):
            self.check([goal], initial=5)

    def test_exact_requirement_cannot_borrow_other_item_equality(self):
        goals = [self.gain(comparison='at_least'), self.gain(item='stick')]
        with self.assertRaisesRegex(ValueError, 'no exact inventory-gain bound'):
            self.check(goals)

    def test_exact_bound_for_wrong_count_does_not_authorize_matching_minimum(self):
        goals = [self.gain(1), self.gain(comparison='at_least')]
        with self.assertRaisesRegex(ValueError, 'no exact inventory-gain bound'):
            self.check(goals)

    def test_default_comparison_is_executable_exact_gain(self):
        self.check([self.gain()], initial=5)

    def test_explicit_exact_comparison_is_accepted(self):
        self.check([self.gain(comparison='exact')], initial=5)

    def test_exact_bound_can_coexist_with_compatible_lower_bounds_and_duplicates(self):
        exact = self.gain()
        goals = [self.gain(comparison='at_least'), exact, exact,
                 {'kind': 'inventory_at_least', 'item': 'oak_planks', 'count': 7}]
        self.check(goals, initial=5)

    def test_exact_requirement_still_rejects_larger_final_minimum(self):
        goals = [self.gain(), {'kind': 'inventory_at_least', 'item': 'oak_planks', 'count': 8}]
        with self.assertRaisesRegex(ValueError, 'proposed final predicates imply 3'):
            self.check(goals, initial=5)

    def test_explicit_minimum_allows_lower_bound_predicate(self):
        self.check([self.gain(comparison='at_least')], comparison='at_least', initial=5)

    def test_explicit_minimum_allows_recipe_batch_surplus(self):
        for comparison in (None, 'exact', 'at_least'):
            with self.subTest(comparison=comparison):
                self.check([self.gain(4, comparison)], comparison='at_least', initial=5)

    def test_explicit_minimum_allows_total_inventory_lower_bound(self):
        goal = {'kind': 'inventory_at_least', 'item': 'oak_planks', 'count': 9}
        self.check([goal], comparison='at_least', initial=5)


class QuantityIntentPromptTests(unittest.TestCase):
    def test_geometry_and_repairs_do_not_invent_new_block_quantities(self):
        self.assertIn('Do NOT multiply width by depth or height here', INTENT_SYSTEM)
        self.assertIn('FINISH/REPAIR an existing structure permits keeping matching blocks', INTENT_SYSTEM)
        self.assertIn('"Finish a 5 by 3 cobblestone floor; keep the existing row" -> requirements: []', INTENT_SYSTEM)
        self.assertIn('"Place exactly 20 NEW cobblestone blocks" -> requirements:', INTENT_SYSTEM)

    def test_crafting_new_output_is_retained_not_false_world_collection(self):
        self.assertIn('CRAFT/MAKE Q NEW items that the player keeps, record retain_new(Q)', INTENT_SYSTEM)
        self.assertIn('does NOT turn a crafted or recipe-produced item into collect_new', INTENT_SYSTEM)
        self.assertIn('only\nfor an explicit physical gather/mine/collect request', INTENT_SYSTEM)
        self.assertIn('not by a crafting output or any other recipe result', INTENT_SYSTEM)


class RetainedQuantityVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_exact_goal_does_not_verify_recipe_surplus(self):
        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.measure = AsyncMock(return_value=9)
        goal = {'kind': 'inventory_gain', 'item': 'oak_planks', 'count': 2}
        state = {'inventory': [{'name': 'oak_planks', 'count': 5}]}
        requirement = [{'outcome': 'retain_new', 'item': 'oak_planks', 'count': 2, 'comparison': 'exact'}]
        check_quantities(requirement, [goal], {'oak_planks': 5}, quantity_contract([goal], state))
        met, evidence = await agent.check([goal], [5], {})
        self.assertFalse(met, 'A recipe batch producing four cannot prove exactly two new retained items.')
        self.assertFalse(evidence[0]['met'])

    async def test_explicit_minimum_verifies_recipe_surplus(self):
        agent = ModelToolAgent.__new__(ModelToolAgent)
        agent.measure = AsyncMock(return_value=9)
        goal = {'kind': 'inventory_gain', 'item': 'oak_planks', 'count': 2, 'comparison': 'at_least'}
        state = {'inventory': [{'name': 'oak_planks', 'count': 5}]}
        requirement = [{'outcome': 'retain_new', 'item': 'oak_planks', 'count': 2, 'comparison': 'at_least'}]
        check_quantities(requirement, [goal], {'oak_planks': 5}, quantity_contract([goal], state))
        met, evidence = await agent.check([goal], [5], {})
        self.assertTrue(met)
        self.assertTrue(evidence[0]['met'])


if __name__ == '__main__':
    unittest.main()
