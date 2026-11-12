from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flow_review.flows import FlowError, load_flows, parse_flow_file, select_flows


class FlowParsingTests(unittest.TestCase):
    def write_flow(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(body)
        return path

    def test_parses_a_supported_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_flow(Path(tmp), "create.yaml", """
id: create-item
name: Create an item
platform: ios
tags: [critical, create]
launch_arguments: [-UITest]
steps:
  - launch
  - tap:
      accessibility_id: addButton
  - type:
      accessibility_id: titleField
      value: Test Item
      clear_first: true
  - assert_text:
      accessibility_id: titleField
      contains: Test
""")
            flow = parse_flow_file(path)

        self.assertEqual(flow.id, "create-item")
        self.assertEqual(flow.swift_test_name(), "test_create_item")
        self.assertEqual(flow.steps[2].params["clear_first"], True)

    def test_rejects_tap_without_locator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_flow(Path(tmp), "invalid.yaml", """
id: invalid-flow
steps:
  - launch
  - tap: {}
""")
            with self.assertRaisesRegex(FlowError, "needs a locator"):
                parse_flow_file(path)

    def test_rejects_invalid_element_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            missing_type_locator = self.write_flow(directory, "type.yaml", """
id: invalid-type
steps:
  - type:
      value: Hello
""")
            invalid_index = self.write_flow(directory, "index.yaml", """
id: invalid-index
steps:
  - tap:
      accessibility_id: row
      index: -1
""")
            missing_text_expectation = self.write_flow(directory, "text.yaml", """
id: invalid-text
steps:
  - assert_text:
      accessibility_id: title
""")

            with self.assertRaisesRegex(FlowError, "needs a locator"):
                parse_flow_file(missing_type_locator)
            with self.assertRaisesRegex(FlowError, "non-negative integer"):
                parse_flow_file(invalid_index)
            with self.assertRaisesRegex(FlowError, "needs at least one expected value"):
                parse_flow_file(missing_text_expectation)

    def test_load_and_select_by_tag_and_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_flow(directory, "ios.yaml", """
id: ios-flow
platform: ios
tags: [smoke]
steps: [launch]
""")
            self.write_flow(directory, "mac.yaml", """
id: mac-flow
platform: macos
tags: [smoke]
steps: [launch]
""")
            flows = load_flows(directory)

        selected = select_flows(flows, tags=["smoke"], platform="ios")
        self.assertEqual([flow.id for flow in selected], ["ios-flow"])


if __name__ == "__main__":
    unittest.main()
