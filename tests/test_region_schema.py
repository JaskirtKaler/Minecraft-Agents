"""Schema/controller contracts for the read-only inspect_region tool."""

import json
import unittest

from agent.planner_schema import POSITION, tool_schema
from agent.tool_agent import READ_TOOLS, SYSTEM, TOOLS


class RegionInspectionSchemaTests(unittest.TestCase):
    def test_inspect_region_requires_exact_inclusive_bounds(self):
        variants = tool_schema({"inspect_region"})["oneOf"]
        self.assertEqual(len(variants), 1)
        tool = variants[0]
        self.assertEqual(tool["properties"]["name"], {"const": "inspect_region"})
        args = tool["properties"]["args"]
        self.assertEqual(args["required"], ["min", "max"])
        self.assertFalse(args["additionalProperties"])
        self.assertEqual(args["properties"], {"min": POSITION, "max": POSITION})
        json.dumps(tool_schema(TOOLS))

    def test_region_survey_is_advertised_as_read_only_with_partial_scan_warning(self):
        self.assertIn("inspect_region", READ_TOOLS)
        self.assertIn("inspect_region", TOOLS)
        self.assertIn("inspect_region: {min:{x,y,z},max:{x,y,z}}", SYSTEM)
        self.assertIn("name:'unknown', loaded:false", SYSTEM)
        self.assertIn("never treat an unloaded/partial survey", SYSTEM)

    def test_repair_schema_never_accepts_model_ownership_or_controller_contracts(self):
        variant = tool_schema({'repair_batch'})['oneOf'][0]
        args = variant['properties']['args']
        self.assertEqual(args['required'], ['positions'])
        self.assertEqual(set(args['properties']), {'positions', 'tool'})
        self.assertFalse(args['additionalProperties'])
        self.assertNotIn('repair_batch', READ_TOOLS)
        self.assertIn('repair_batch', TOOLS)


if __name__ == "__main__":
    unittest.main()
