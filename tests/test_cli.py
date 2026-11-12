from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flow_review import cli, config
from flow_review.flows import Flow


class CommandConstructionTests(unittest.TestCase):
    def test_build_command_limits_xctest_to_one_generated_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "Example.xcodeproj"
            project.mkdir()
            cfg = config.ProjectConfig(
                id="example",
                name="Example",
                device=config.DeviceConfig(),
                xcode=config.XcodeConfig(
                    project="Example.xcodeproj",
                    scheme="Example",
                    ui_test_target="ExampleFlowReviewUITests",
                ),
                run=config.RunConfig(),
                root=root,
            )
            flow = Flow(id="create-item", name="Create item", source_path=root / "create.yaml")
            command = cli.build_xcodebuild_command(
                root,
                cfg,
                "ExampleFlowReviewUITests",
                flow,
                "platform=iOS Simulator,id=ABC",
                root / "result.xcresult",
            )

        self.assertIn("-project", command)
        self.assertTrue(any(part.endswith("/Example.xcodeproj") for part in command))
        self.assertIn(
            "-only-testing:ExampleFlowReviewUITests/FlowReviewGeneratedTests/test_create_item",
            command,
        )

    def test_parser_accepts_multiple_flow_filters(self) -> None:
        args = cli.build_parser().parse_args([
            "run", "Example", "--flow", "login", "--flow", "logout", "--no-video",
        ])
        self.assertEqual(args.flow, ["login", "logout"])
        self.assertTrue(args.no_video)


if __name__ == "__main__":
    unittest.main()
