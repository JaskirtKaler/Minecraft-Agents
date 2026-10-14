"""Literal quantity grounding, never an action plan or model inference."""
import copy
import json
import unittest

from agent.goal_contract import check_quantities, quantity_source_facts, validate_requirements
from practice.construction import make_task


def requirement(outcome, count, item='cobblestone', comparison='exact'):
    return {'outcome': outcome, 'item': item, 'count': count, 'comparison': comparison}


class QuantitySourceFactsTests(unittest.TestCase):
    def validate(self, text, *requirements):
        return validate_requirements({'requirements': list(requirements)}, objective=text)

    def test_staircase_and_walking_distances_are_geometry_not_collected_items(self):
        for text in ('Mine a staircase up 4 blocks.', 'Make a staircase up three blocks.',
                     'Walk 10 blocks east.', 'Climb up 3 blocks.'):
            with self.subTest(text=text):
                self.assertTrue(quantity_source_facts(text)['safe_without_quantity_inference'])
                self.assertEqual(self.validate(text), [])

    def test_geometry_distance_does_not_hide_a_following_explicit_item_amount(self):
        text = 'Mine a staircase up 4 blocks; then collect exactly 3 oak logs.'
        self.assertFalse(quantity_source_facts(text)['safe_without_quantity_inference'])
        self.validate(text, requirement('collect_new', 3, 'oak_log'))
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.validate(text)

    def test_shelter_and_repair_failures_have_no_explicit_new_item_amount(self):
        for name, seed, invented in (('shelter', 303, 20), ('repair_shelter', 204, 2)):
            with self.subTest(task=name):
                objective = make_task(name, seed).objective
                facts = quantity_source_facts(objective)
                self.assertTrue(facts['safe_without_quantity_inference'])
                self.assertEqual(facts['explicit_amounts'], [])
                self.assertEqual(self.validate(objective), [])
                with self.assertRaisesRegex(ValueError, 'Ungrounded request quantity'):
                    self.validate(objective, requirement('place_new', invented))
                json.dumps(facts)  # Every diagnostic is usable in model/report JSON.

    def test_geometry_coordinates_and_foundation_holes_are_not_item_counts(self):
        requests = [
            'Build a 10 by 10 house with three-high walls at X=-8..-4, Y=64, Z=-15..-12.',
            'Build a 10 block by 10 block house, width 10 and height 4.',
            'Fill these two foundation holes at (1,64,-3) and (2,64,-3).',
            'Finish a 5 by 3 floor; keep the existing row.',
            'Build a five by four shelter with a two-high doorway.',
        ]
        for text in requests:
            with self.subTest(text=text):
                self.assertTrue(quantity_source_facts(text)['safe_without_quantity_inference'])
                self.validate(text)

    def test_mixed_geometry_does_not_erase_explicit_placement_quantity(self):
        text = 'Build a 5 by 4 shelter at Y=64; also place exactly 3 NEW oak_saplings near it.'
        facts = quantity_source_facts(text)
        self.assertFalse(facts['safe_without_quantity_inference'])
        self.assertEqual([f['count'] for f in facts['explicit_amounts']], [3])
        self.validate(text, requirement('place_new', 3, 'oak_sapling'))
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.validate(text)
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('place_new', 20))

    def test_explicit_new_blocks_remain_strict_and_no_inputs_are_mutated(self):
        text = 'Place exactly 20 NEW cobblestone blocks.'
        value = {'requirements': [requirement('place_new', 20)]}
        original = copy.deepcopy(value)
        self.assertEqual(validate_requirements(value, objective=text), value['requirements'])
        self.assertEqual(value, original)
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('place_new', 20, comparison='at_least'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('place_new', 66))
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.validate(text)
        with self.assertRaises(ValueError):
            check_quantities(value['requirements'], [{'kind': 'inventory_at_least', 'item': 'cobblestone', 'count': 20}], {}, {})

    def test_explicit_minimum_is_not_silently_changed_to_exact(self):
        text = 'Plant at least 3 new wheat crops.'
        self.validate(text, requirement('place_new', 3, 'wheat', 'at_least'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('place_new', 3, 'wheat', 'exact'))

    def test_existing_state_and_world_removal_do_not_become_new_placement(self):
        for text in ('I have 7 logs.', 'I hold 4 logs.', 'You already hold 2 cobblestones.', 'You already carry 2 cobblestones.',
                     'There are 3 existing saplings.', 'Remove two misplaced cobblestones at Y=64.',
                     'The fixture injects two verified misplaced cobblestones.',
                     'Keep 4 existing blocks; repair the nearby shelter.'):
            with self.subTest(text=text):
                self.assertTrue(quantity_source_facts(text)['safe_without_quantity_inference'])
                with self.assertRaisesRegex(ValueError, 'Ungrounded'):
                    self.validate(text, requirement('place_new', 2))

    def test_source_removal_is_a_requested_quantity_even_for_existing_stock(self):
        for verb in ('Take', 'Withdraw', 'Remove'):
            text = f'{verb} exactly 3 existing oak logs from the nearby chest.'
            self.assertFalse(quantity_source_facts(text)['safe_without_quantity_inference'])
            self.validate(text, requirement('container_remove', 3, 'oak_log'))
            with self.assertRaisesRegex(ValueError, 'Ungrounded'):
                self.validate(text, requirement('place_new', 3, 'oak_log'))
            with self.assertRaisesRegex(ValueError, 'omitted'):
                self.validate(text)

    def test_existing_inventory_does_not_erase_later_requested_source_count(self):
        text = 'I already have 7 logs; take exactly 3 oak logs from the chest and plant 2 new oak saplings.'
        facts = quantity_source_facts(text)
        self.assertEqual([f['count'] for f in facts['explicit_amounts']], [3, 2])
        self.validate(text, requirement('container_remove', 3, 'oak_log'), requirement('place_new', 2, 'oak_sapling'))

    def test_collection_requires_fresh_evidence_while_get_allows_held_stock(self):
        self.validate('Collect 24 oak logs and put them in the chest.',
                      requirement('collect_new', 24, 'oak_log'), requirement('container_add', 24, 'oak_log'))
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.validate('Collect 24 oak logs and put them in the chest.', requirement('container_add', 24, 'oak_log'))
        self.validate('Get 24 oak logs and put them in the chest.', requirement('container_add', 24, 'oak_log'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate('Get 24 oak logs.', requirement('collect_new', 24, 'oak_log'))

    def test_craft_plant_drop_and_chest_delivery_amounts_remain_checked(self):
        cases = [
            ('Craft 2 wooden swords.', [requirement('retain_new', 2, 'wooden_sword')]),
            ('Craft 2 wooden swords and give them to me.', [requirement('drop_near', 2, 'wooden_sword')]),
            ('Plant exactly 5 new oak saplings.', [requirement('place_new', 5, 'oak_sapling')]),
            ('Drop exactly 3 cobblestone for Pilot.', [requirement('drop_near', 3)]),
            ('Deposit exactly 8 oak logs into the nearby chest.', [requirement('container_add', 8, 'oak_log')]),
            ('Take exactly 3 oak saplings from the nearby chest and plant 3 NEW oak sapling blocks.',
             [requirement('container_remove', 3, 'oak_sapling'), requirement('place_new', 3, 'oak_sapling')]),
        ]
        for text, amounts in cases:
            with self.subTest(text=text):
                self.validate(text, *amounts)
                with self.assertRaisesRegex(ValueError, 'omitted'):
                    self.validate(text)

    def test_final_crafted_output_is_not_fresh_world_collection(self):
        text = 'Craft enough planks and sticks to finish with at least 4 NEW oak_planks and 4 NEW stick in your inventory.'
        self.validate(text, requirement('retain_new', 4, 'oak_planks', 'at_least'), requirement('retain_new', 4, 'stick', 'at_least'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('collect_new', 4, 'oak_planks', 'at_least'))

    def test_number_words_are_literal_amounts_not_derived_geometry(self):
        for phrase, count in (('three', 3), ('twenty-four', 24), ('one hundred and twenty', 120)):
            self.validate(f'Give exactly {phrase} oak logs to Pilot.', requirement('drop_near', count, 'oak_log'))

    def test_unsupported_or_ambiguous_numeric_language_never_permits_safe_skip(self):
        for text in ('Get 2 stacks of cobblestone.', 'Get a dozen oak logs.', 'Give me a few logs.',
                     'Get 2-3 oak logs.', 'Plant 2.5 saplings.', 'get10logs', 'Craft2 wooden swords.',
                     'The number 27 is relevant.', 'Get one two logs.'):
            with self.subTest(text=text):
                facts = quantity_source_facts(text)
                self.assertFalse(facts['safe_without_quantity_inference'])
                self.assertTrue(facts['unresolved_mentions'])
                json.dumps(facts)
                with self.assertRaisesRegex(ValueError, 'Unresolved request quantity syntax'):
                    self.validate(text)

    def test_unresolved_amount_cannot_be_omitted_from_mixed_request(self):
        text = 'Get 2 stacks of oak logs; also plant exactly 3 new oak saplings.'
        facts = quantity_source_facts(text)
        self.assertEqual([row['count'] for row in facts['explicit_amounts']], [3])
        self.assertTrue(facts['unresolved_mentions'])
        with self.assertRaisesRegex(ValueError, 'Unresolved request quantity syntax'):
            self.validate(text, requirement('place_new', 3, 'oak_sapling'))

    def test_same_count_different_items_need_their_own_item_requirements(self):
        text = 'Craft 4 oak_planks and 4 sticks.'
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.validate(text, requirement('retain_new', 4, 'oak_planks'))
        self.validate(text, requirement('retain_new', 4, 'oak_planks'), requirement('retain_new', 4, 'stick'))

    def test_swapped_item_counts_cannot_borrow_another_numeric_source(self):
        text = 'Craft 2 wooden_swords and 3 sticks.'
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate(text, requirement('retain_new', 3, 'wooden_sword'), requirement('retain_new', 2, 'stick'))
        self.validate(text, requirement('retain_new', 2, 'wooden_sword'), requirement('retain_new', 3, 'stick'))

    def test_canonical_ids_spaces_units_and_ordinary_plurals_match_lexically(self):
        cases = [
            ('Craft 2 wooden swords.', 'retain_new', 'wooden_sword', 2),
            ('Mine 3 oak logs.', 'collect_new', 'oak_log', 3),
            ('Give 2 stone pickaxes to Pilot.', 'drop_near', 'stone_pickaxe', 2),
            ('Plant 3 NEW oak sapling blocks on soil nearby.', 'place_new', 'oak_sapling', 3),
            ('Place 5 cobble stone blocks.', 'place_new', 'cobblestone', 5),
            ('Drop 3 minecraft:cobblestone to Pilot6117.', 'drop_near', 'cobblestone', 3),
            ('Get 2 music_disc_13.', 'hold_minimum', 'music_disc_13', 2),
        ]
        for text, outcome, item, count in cases:
            with self.subTest(text=text):
                self.validate(text, requirement(outcome, count, item))

    def test_generic_item_kind_allows_specific_canonical_suffix_not_another_species(self):
        self.validate('Get 27 logs and put them in the chest.', requirement('container_add', 27, 'oak_log'))
        self.validate('Take 3 saplings from the chest.', requirement('container_remove', 3, 'oak_sapling'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate('Get 27 birch logs and put them in the chest.', requirement('container_add', 27, 'oak_log'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate('Take 3 spruce saplings from the chest.', requirement('container_remove', 3, 'oak_sapling'))
        with self.assertRaisesRegex(ValueError, 'Ungrounded'):
            self.validate('Get 3 stone.', requirement('hold_minimum', 3, 'cobblestone'))

    def test_repeated_same_count_delivery_resolves_unique_same_clause_literal_item(self):
        text = 'Get 24 additional oak logs and put 24 in the chest.'
        facts = quantity_source_facts(text)
        self.assertEqual(facts['unresolved_mentions'], [])
        self.assertEqual(facts['explicit_amounts'][1]['resolved_antecedent_span'], facts['explicit_amounts'][0]['span'])
        self.validate(text, requirement('collect_new', 24, 'oak_log', 'at_least'), requirement('container_add', 24, 'oak_log'))
        self.validate('Collect exactly two new oak logs and deliver two to the chest.',
                      requirement('collect_new', 2, 'oak_log'), requirement('container_add', 2, 'oak_log'))

    def test_missing_item_antecedents_fail_when_items_counts_or_clauses_are_ambiguous(self):
        for text in ('Get 4 oak logs and 4 sticks and put 4 in the chest.',
                     'Get 3 oak logs and put 4 in the chest.',
                     'Get 4 oak logs; put 4 in the chest.',
                     'Put 4 in the chest.'):
            with self.subTest(text=text):
                self.assertTrue(quantity_source_facts(text)['unresolved_mentions'])
                with self.assertRaisesRegex(ValueError, 'Unresolved request quantity syntax'):
                    self.validate(text, requirement('container_add', 4, 'oak_log'))

    def test_unproven_aliases_or_unspecified_item_identity_require_clarification(self):
        for text, item in (('Get 3 timber.', 'oak_log'), ('Craft 2 wood swords.', 'wooden_sword'),
                           ('Plant 3 wheat seeds.', 'wheat'), ('Get 4 blocks.', 'cobblestone')):
            with self.subTest(text=text):
                outcome = 'place_new' if text.startswith('Plant') else 'retain_new' if text.startswith('Craft') else 'hold_minimum'
                with self.assertRaisesRegex(ValueError, 'Ungrounded|Unresolved'):
                    self.validate(text, requirement(outcome, 3 if ' 3 ' in text else 2 if ' 2 ' in text else 4, item))

    def test_structural_validation_without_source_stays_backward_compatible(self):
        value = {'requirements': [requirement('place_new', 20)]}
        self.assertEqual(validate_requirements(value), value['requirements'])
        with self.assertRaises(ValueError):
            validate_requirements({'requirements': [requirement('place_new', True)]}, objective='Place 20 cobblestone.')


if __name__ == '__main__':
    unittest.main()
