"""Pure proof-contract tests; no Minecraft, services or model calls."""
from copy import deepcopy
import unittest

from agent.action_effects import observed_tool_effect


POSITION = {"x": 11, "y": 79, "z": -13}
OTHER = {"x": 11, "y": 79, "z": 13}


def descriptor(name, properties=None, position=None):
    return {"name": name, "position": dict(position or POSITION), "properties": dict(properties or {})}


class ObservedToolEffectTests(unittest.TestCase):
    def test_verified_node_dig_changed_block_at_exact_target(self):
        action = {"name": "dig", "args": {"position": POSITION}}
        result = {"success": True, "verified": True, "data": {"broken": "oak_leaves", "replacement": "air", "position": POSITION}}
        original = deepcopy((action, result))
        self.assertTrue(observed_tool_effect(action, result))
        self.assertEqual((action, result), original)
        for changed in ({"success": False}, {"verified": False}, {"verified": 1}):
            self.assertFalse(observed_tool_effect(action, {**result, **changed}))
        for changed in ({"replacement": "oak_leaves"}, {"replacement": "unknown"}, {"broken": "unknown"}, {"position": OTHER}):
            self.assertFalse(observed_tool_effect(action, {**result, "data": {**result["data"], **changed}}))

    def test_explicit_simulator_dig_receipt_not_blanket_changed_flag(self):
        action = {"name": "dig", "args": {"position": POSITION}}
        result = {"success": True, "verified": True, "data": {
            "broken": "dirt", "name": "air", "position": POSITION, "changed": True, "synthetic_drop": "dirt"}}
        self.assertTrue(observed_tool_effect(action, result))
        for data in ({"changed": True}, {**result["data"], "changed": False},
                     {**result["data"], "name": "dirt"}, {**result["data"], "position": OTHER},
                     {**result["data"], "replacement": "unknown"}):
            self.assertFalse(observed_tool_effect(action, {**result, "data": data}))

    def test_interaction_descriptors_can_prove_mutation_without_overall_verification(self):
        action = {"name": "use_on_block", "args": {"item": "stone_hoe", "position": POSITION}}
        result = {"success": True, "verified": False, "data": {
            "before": descriptor("dirt"), "after": descriptor("farmland", {"moisture": 0})}}
        self.assertTrue(observed_tool_effect(action, result))
        self.assertFalse(observed_tool_effect(action, {**result, "success": False}))
        bad_after = [descriptor("farmland", position=OTHER), {"name": "farmland"}, descriptor("unknown"),
                     {**descriptor("farmland"), "loaded": False}, descriptor("dirt")]
        for after in bad_after:
            self.assertFalse(observed_tool_effect(action, {**result, "data": {**result["data"], "after": after}}))

    def test_interaction_properties_must_be_actual_known_unequal_observations(self):
        action = {"name": "use_on_block", "args": {"position": POSITION}}
        result = {"success": True, "verified": False, "data": {
            "before": descriptor("oak_door", {"open": False}), "after": descriptor("oak_door", {"open": True})}}
        original = deepcopy(result)
        self.assertTrue(observed_tool_effect(action, result))
        self.assertEqual(result, original)
        for properties in ({"open": False}, {"open": None}, {"open": "unknown"}, {"open": []}, "open", {"open": float("nan")}):
            after = {**descriptor("oak_door"), "properties": properties}
            self.assertFalse(observed_tool_effect(action, {**result, "data": {**result["data"], "after": after}}))
        before = {"name": "oak_door", "position": POSITION}
        self.assertFalse(observed_tool_effect(action, {**result, "data": {**result["data"], "before": before}}))

    def test_verified_batch_mutation_counts_when_overall_batch_fails(self):
        for name, proof_name, before, after in (("place_batch", "placements", "air", "cobblestone"),
                                               ("dig_batch", "cleared", "oak_leaves", "air"),
                                               ("repair_batch", "cleared", "cobblestone", "air")):
            with self.subTest(name=name):
                action = {"name": name, "args": {"item": "cobblestone", "positions": [POSITION]}}
                row = {"position": POSITION, "before": before, "after": after, "verified": True}
                result = {"success": False, "verified": False, "data": {
                    "partial": {proof_name: [row], "failed": {"code": "NO_SAFE_STANCE"}}}}
                original = deepcopy((action, result))
                self.assertTrue(observed_tool_effect(action, result))
                self.assertEqual((action, result), original)
                for changed in ({"before": after}, {"after": "unknown"}, {"verified": False},
                                {"verified": 1}, {"position": OTHER}, {"position": None}):
                    partial = {proof_name: [{**row, **changed}]}
                    self.assertFalse(observed_tool_effect(action, {**result, "data": {"partial": partial}}))
                self.assertTrue(observed_tool_effect(action, {**result, "success": True, "data": {proof_name: [row]}}))

    def test_one_real_partial_cell_counts_but_skipped_and_failed_claims_do_not(self):
        action = {"name": "dig_batch", "args": {"positions": [POSITION, OTHER]}}
        actual = {"position": POSITION, "before": "oak_leaves", "after": "air", "verified": True}
        result = {"success": False, "data": {"partial": {"cleared": [actual],
                  "skipped": [{"position": OTHER, "reason": "already_air", "verified": True}]}}}
        self.assertTrue(observed_tool_effect(action, result))
        result["data"]["partial"]["cleared"] = []
        self.assertFalse(observed_tool_effect(action, result))
        result["data"]["partial"]["failed"] = {**actual, "changed": True}
        self.assertFalse(observed_tool_effect(action, result))

    def test_batch_request_and_proof_must_match_valid_coordinates(self):
        action = {"name": "place_batch", "args": {"positions": [POSITION]}}
        row = {"position": POSITION, "before": "air", "after": "cobblestone", "verified": True}
        result = {"success": True, "data": {"placements": [row]}}
        for raw in ([], [POSITION, POSITION], [POSITION, None], [{**POSITION, "x": True}], "positions", [POSITION] * 65):
            self.assertFalse(observed_tool_effect({**action, "args": {"positions": raw}}, result))
        for data in ({"placements": "not rows"}, {"placements": [row] * 65}, {"partial": None},
                     {"placements": [{"before": "air", "after": "cobblestone", "verified": True}]}):
            self.assertFalse(observed_tool_effect(action, {**result, "data": data}))

    def test_success_or_metadata_alone_never_proves_effect(self):
        actions = [{"name": name, "args": {"position": POSITION, "positions": [POSITION]}}
                   for name in ("dig", "use_on_block", "place_batch", "dig_batch", "repair_batch", "walk_to", "equip", "invented")]
        for action in actions:
            for data in ({}, {"changed": True}, {"verified": True}, {"position": POSITION},
                         {"before": "unknown", "after": "air", "position": POSITION}):
                self.assertFalse(observed_tool_effect(action, {"success": True, "verified": True, "data": data}))
        for action, result in ((None, {}), ({}, None), ({"name": "dig", "args": []}, {"data": {}}),
                               ({"name": "dig", "args": {}}, {"data": []}),
                               ({"name": [], "args": {}}, {"data": {}}),
                               ({"name": {}, "args": {}}, {"data": {}})):
            self.assertFalse(observed_tool_effect(action, result))


if __name__ == "__main__":
    unittest.main()
