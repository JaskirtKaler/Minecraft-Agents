"""Offline grounding facts for varied targets; no Minecraft or model service."""

import copy
import json
import unittest

from agent.construction_knowledge import construction_knowledge
from agent.regions import RegionError, compile_goal, position_key


def region(minimum, maximum, block="cobblestone", must_change=True, mode="solid"):
    def position(value):
        return dict(zip(("x", "y", "z"), value))
    return {"kind": "regions_match", "regions": [{"min": position(minimum), "max": position(maximum),
             "block": block, "mode": mode}], "must_change": must_change}


def evidence(goal, observed, before=None):
    return {"goal": goal, "observed": observed, "before": before or {}, "met": False}


def sample_positions(packet, category):
    return [tuple(fact["position"][axis] for axis in ("x", "y", "z"))
            for fact in packet["categories"][category]["samples"]]


class ConstructionKnowledgeTests(unittest.TestCase):
    def test_negative_z_floor_is_not_the_support_cell_and_bounds_are_inclusive(self):
        goal = region((11, 78, -16), (20, 78, -7))
        names = {position_key(coordinate): "air" for coordinate in compile_goal(goal)}
        names["11,77,-16"] = "grass_block"
        packet = construction_knowledge([goal], [evidence(goal, names)], {}, max_samples=32)
        self.assertEqual(packet["target_cell_count"], 100)
        self.assertEqual(packet["target_bounds"], {"min": {"x": 11, "y": 78, "z": -16},
                                                   "max": {"x": 20, "y": 78, "z": -7}})
        samples = packet["categories"]["missing_nonair"]["samples"]
        self.assertEqual(samples[0]["position"], {"x": 11, "y": 78, "z": -16})
        self.assertEqual(samples[0]["below"], {"position": {"x": 11, "y": 77, "z": -16}, "observed": "grass_block"})
        self.assertTrue(all(fact["position"]["z"] < 0 for fact in samples))
        self.assertIn("max-min+1", packet["coordinate_knowledge"]["inclusive_bounds"])
        self.assertIn("not the same target", packet["coordinate_knowledge"]["target_vs_support"])

    def test_varied_origins_and_heights_preserve_exact_coordinates(self):
        for x, y, z in ((-34, -20, -7), (0, 0, 0), (112, 146, 81)):
            with self.subTest(origin=(x, y, z)):
                goal = region((x, y, z), (x + 2, y + 1, z + 2))
                names = {position_key(position): "air" for position in compile_goal(goal)}
                packet = construction_knowledge([goal], [evidence(goal, names)], {})
                self.assertEqual(packet["target_cell_count"], 18)
                self.assertIn((x, y, z), sample_positions(packet, "missing_nonair"))
                self.assertIn((x + 2, y + 1, z + 2), sample_positions(packet, "missing_nonair"))

    def test_partial_completion_materials_sum_stacks_and_do_not_recount_built_cells(self):
        goal = region((11, 78, -16), (20, 78, -7))
        keys = [position_key(position) for position in compile_goal(goal)]
        names = {key: ("cobblestone" if index < 54 else "air") for index, key in enumerate(keys)}
        state = {"inventory": [{"name": "cobblestone", "count": 16}, {"name": "cobblestone", "count": 12},
                               {"name": "dirt", "count": 64}]}
        packet = construction_knowledge([goal], [evidence(goal, names)], state)
        material = packet["materials"]["cobblestone"]
        self.assertEqual(material["held_same_name_items"], 28)
        self.assertEqual(material["already_matching_cells"], 54)
        self.assertEqual(material["known_remaining_cells"], 46)
        self.assertEqual(material["minimum_additional_same_name_items"], 18)
        self.assertEqual(material["remaining_cells_upper_bound"], 46)
        self.assertNotIn("dirt", packet["materials"])

    def test_air_does_not_hide_soil_leaves_preserved_or_unknown_cells(self):
        goal = region((-20, 51, -15), (-6, 51, -15))
        names = {position_key(position): "air" for position in compile_goal(goal)}
        names.update({"-10,51,-15": "grass_block", "-9,51,-15": "oak_leaves", "-8,51,-15": "chest",
                      "-7,51,-15": "cobblestone", "-6,51,-15": {"name": "air", "loaded": False}})
        preserve = {"kind": "block_is", "position": {"x": 2, "y": 52, "z": -13}, "block": "chest", "must_change": False}
        packet = construction_knowledge([goal, preserve], [evidence(goal, names), evidence(preserve, "chest")], {})
        categories = packet["categories"]
        self.assertEqual(categories["natural_obstructions"]["count"], 2)
        self.assertEqual({fact["observed"] for fact in categories["natural_obstructions"]["samples"]}, {"grass_block", "oak_leaves"})
        self.assertEqual(categories["matched_no_change_required"]["samples"][0]["observed"], "chest")
        self.assertEqual(categories["unknown"]["count"], 1)
        self.assertEqual(categories["other_occupied_mismatches"]["count"], 1)
        self.assertEqual(sum(len(category["samples"]) for category in categories.values()), 8)
        self.assertTrue(all(category["samples"] for category in categories.values() if category["count"]))

    def test_multiple_soil_names_do_not_take_every_natural_sample_before_leaves(self):
        goal = region((-10, 63, -20), (5, 63, -20))
        names = {position_key(position): "air" for position in compile_goal(goal)}
        names.update({"-2,63,-20": "dirt", "-1,63,-20": "grass_block", "0,63,-20": "oak_leaves",
                      "1,63,-20": "oak_log", "2,63,-20": "chest", "3,63,-20": "cobblestone",
                      "4,63,-20": "unknown", "5,63,-20": "unknown"})
        preserve = {"kind": "block_is", "position": {"x": 8, "y": 63, "z": -20}, "block": "chest"}
        packet = construction_knowledge([goal, preserve], [evidence(goal, names), evidence(preserve, "chest")], {})
        samples = packet["categories"]["natural_obstructions"]["samples"]
        self.assertEqual(len(samples), 2)
        self.assertTrue(any(fact["observed"] in {"dirt", "grass_block"} for fact in samples))
        self.assertTrue(any(fact["observed"] == "oak_leaves" for fact in samples))
        self.assertEqual(packet["categories"]["natural_obstructions"]["observed_blocks"],
                         {"dirt": 1, "grass_block": 1, "oak_leaves": 1, "oak_log": 1})

    def test_baseline_and_met_flags_never_become_current_world_observations(self):
        goal = region((0, 70, -2), (3, 70, -2))
        baseline = {position_key(position): "air" for position in compile_goal(goal)}
        entry = evidence(goal, {"0,70,-2": "dirt"}, baseline)
        entry["met"] = True
        packet = construction_knowledge([goal], [entry], {})
        self.assertEqual(packet["categories"]["unknown"]["count"], 3)
        self.assertEqual(packet["categories"]["missing_nonair"]["count"], 0)
        self.assertEqual(packet["categories"]["natural_obstructions"]["count"], 1)
        material = packet["materials"]["cobblestone"]
        self.assertEqual(material["known_remaining_cells"], 1)
        self.assertEqual(material["unknown_target_cells"], 3)
        self.assertEqual(material["remaining_cells_upper_bound"], 4)
        self.assertEqual(material["minimum_additional_same_name_items"], 1)
        self.assertEqual(material["maximum_additional_if_unknowns_need_placement"], 4)

    def test_required_air_is_not_a_material_or_dig_target_when_already_empty(self):
        goal = region((0, 80, 0), (3, 80, 0), block="air")
        names = {"0,80,0": "air", "1,80,0": "cave_air", "2,80,0": "oak_leaves", "3,80,0": "void_air"}
        packet = construction_knowledge([goal], [evidence(goal, names)], {})
        self.assertEqual(packet["materials"], {})
        self.assertEqual(packet["categories"]["empty_block_mismatches"]["count"], 2)
        self.assertEqual(packet["categories"]["natural_obstructions"]["count"], 1)
        self.assertEqual(packet["categories"]["matched_change_required"]["count"], 1)
        self.assertIn("Never dig an observed air", packet["mechanics"]["air"])

    def test_conflicting_observations_and_missing_block_observation_are_unknown(self):
        goal = region((4, 40, -4), (4, 40, -4))
        block = {"kind": "block_is", "position": {"x": 4, "y": 40, "z": -4}, "block": "cobblestone"}
        missing = {"kind": "block_is", "position": {"x": 5, "y": 40, "z": -4}, "block": "cobblestone"}
        packet = construction_knowledge([goal, block, missing], [evidence(goal, {"4,40,-4": "air"}),
                                                               evidence(block, "stone"), {"goal": missing, "before": "air"}], {})
        self.assertEqual(packet["target_cell_count"], 2)
        unknown = packet["categories"]["unknown"]["samples"]
        self.assertEqual({fact["unknown_reason"] for fact in unknown}, {"conflicting_observations", "missing_or_unloaded"})

    def test_same_block_goal_overlaps_deduplicate_materials_and_other_goals_are_ignored(self):
        first = region((0, 1, 0), (2, 1, 0))
        second = region((2, 1, 0), (4, 1, 0))
        names = {f"{x},1,0": "air" for x in range(5)}
        packet = construction_knowledge([first, second, {"kind": "inventory_at_least", "item": "oak_log", "count": 10}],
                                        [evidence(first, names), evidence(second, names)], {})
        self.assertEqual(packet["target_cell_count"], 5)
        self.assertEqual(packet["materials"]["cobblestone"]["known_remaining_cells"], 5)
        conflict = region((2, 1, 0), (2, 1, 0), block="dirt")
        with self.assertRaises(RegionError):
            construction_knowledge([first, conflict], [], {})

    def test_large_region_samples_span_coordinates_and_packet_is_bounded(self):
        goal = region((-12, 21, -16), (11, 24, 7))  # 2304 cells, no house template.
        names = {position_key(position): "air" for position in compile_goal(goal)}
        packet = construction_knowledge([goal], [evidence(goal, names)], {})
        positions = sample_positions(packet, "missing_nonair")
        self.assertEqual(len(positions), 8)
        self.assertEqual(positions[0], (-12, 21, -16))
        self.assertEqual(positions[-1], (11, 24, 7))
        self.assertEqual(packet["categories"]["missing_nonair"]["omitted_sample_count"], 2296)
        self.assertLess(len(json.dumps(packet)), 9000)

    def test_all_layers_report_desired_actual_matches_and_unknown_without_stages(self):
        goal = region((-3, 51, -6), (-2, 55, -5))
        names = {position_key(position): "air" for position in compile_goal(goal)}
        names["-3,51,-6"] = "cobblestone"
        names["-2,52,-5"] = "dirt"
        names["-3,54,-6"] = "unknown"
        packet = construction_knowledge([goal], [evidence(goal, names)], {})
        summary = packet["layers"]
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["distinct_layers"], 5)
        expanded = {y: row["per_layer"] for row in summary["ranges"] for y in range(row["min_y"], row["max_y"] + 1)}
        self.assertEqual(set(expanded), set(range(51, 56)))
        self.assertEqual(expanded[51]["matched"], 1)
        self.assertEqual(expanded[52]["observed_blocks"], {"air": 3, "dirt": 1})
        self.assertEqual(expanded[54]["unknown"], 1)
        self.assertEqual(expanded[55]["desired_blocks"], {"cobblestone": 4})
        self.assertEqual(sum(layer["cell_count"] for layer in expanded.values()), 20)
        self.assertNotIn("stage", json.dumps(summary))

    def test_layer_runs_compress_uniform_tall_shapes_and_bound_varied_strata(self):
        goal = region((0, -100, -1), (0, 899, -1))
        names = {position_key(position): "air" for position in compile_goal(goal)}
        packet = construction_knowledge([goal], [evidence(goal, names)], {})
        summary = packet["layers"]
        self.assertEqual(summary["total_ranges"], 1)
        self.assertEqual(summary["ranges"][0]["layer_count"], 1000)
        self.assertEqual(summary["ranges"][0]["per_layer"]["desired_blocks"], {"cobblestone": 1})
        varied = {key: "air" if index % 2 else "dirt" for index, key in enumerate(names)}
        packet = construction_knowledge([goal], [evidence(goal, varied)], {})
        summary = packet["layers"]
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["total_ranges"], 1000)
        self.assertEqual(len(summary["ranges"]), 24)
        self.assertEqual(summary["omitted_layer_count"], 976)
        self.assertEqual(summary["ranges"][0]["min_y"], -100)
        self.assertEqual(summary["ranges"][-1]["max_y"], 899)
        self.assertEqual(packet["categories"]["natural_obstructions"]["count"], 500)

    def test_missing_cell_samples_spread_across_height_strata_within_budget(self):
        goal = region((-5, 80, -8), (6, 84, 3))
        names = {position_key(position): "air" for position in compile_goal(goal)}
        packet = construction_knowledge([goal], [evidence(goal, names)], {}, max_samples=2)
        self.assertEqual({position[1] for position in sample_positions(packet, "missing_nonair")}, {80, 84})
        packet = construction_knowledge([goal], [evidence(goal, names)], {}, max_samples=8)
        self.assertEqual({position[1] for position in sample_positions(packet, "missing_nonair")}, set(range(80, 85)))
        self.assertEqual(len(sample_positions(packet, "missing_nonair")), 8)

    def test_no_actions_no_input_mutation_and_repeatable_json_safe_output(self):
        goal = region((8, 10, -8), (9, 10, -8))
        entries = [evidence(goal, {"8,10,-8": "air", "9,10,-8": "cobblestone"}, {"9,10,-8": "air"})]
        state = {"inventory": [{"name": "cobblestone", "count": 12}, {"name": "cobblestone", "count": True},
                               {"name": "cobblestone", "count": -5}]}
        original = copy.deepcopy(([goal], entries, state))
        packet = construction_knowledge([goal], entries, state)
        self.assertEqual(([goal], entries, state), original)
        self.assertEqual(packet, construction_knowledge([goal], entries, state))
        self.assertEqual(packet["materials"]["cobblestone"]["held_same_name_items"], 12)
        serialized = json.dumps(packet)
        self.assertNotIn('"action"', serialized)
        self.assertNotIn('"actions"', serialized)
        self.assertNotIn('"tool"', serialized)

    def test_zero_sample_budget_empty_targets_and_invalid_budgets(self):
        goal = region((0, 2, 0), (3, 2, 0))
        packet = construction_knowledge([goal], [], {}, max_samples=0)
        self.assertEqual(packet["categories"]["unknown"]["count"], 4)
        self.assertEqual(sum(len(category["samples"]) for category in packet["categories"].values()), 0)
        empty = construction_knowledge([], [], {})
        self.assertFalse(empty["applicable"])
        self.assertIsNone(empty["target_bounds"])
        for budget in (-1, 33, 1.5, True):
            with self.assertRaises(ValueError):
                construction_knowledge([goal], [], {}, max_samples=budget)


if __name__ == "__main__":
    unittest.main()
