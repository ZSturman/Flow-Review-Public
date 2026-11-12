from __future__ import annotations

import plistlib
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow_review import config, desktop, finder


class DesktopReadinessTests(unittest.TestCase):
    def _write_config(self, root: Path) -> config.ProjectConfig:
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
        cfg.save()
        return cfg

    def test_unconfigured_folder_needs_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readiness = desktop.assess_project(tmp)
        self.assertFalse(readiness.ready)
        self.assertIn("No flow-review config", readiness.reason)

    def test_configured_folder_with_a_valid_flow_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._write_config(root)
            cfg.flows_path.mkdir()
            (cfg.flows_path / "smoke.yaml").write_text("id: smoke\nsteps: [launch]\n")

            readiness = desktop.assess_project(root)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.flow_count, 1)

    def test_configured_folder_without_flows_opens_setup_instead_of_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_config(root)
            readiness = desktop.assess_project(root)
        self.assertFalse(readiness.ready)
        self.assertIn("no valid flows", readiness.reason)

    def test_review_command_uses_the_current_python_environment(self) -> None:
        command = desktop.review_command(Path("/tmp/Example Project"))
        self.assertEqual(command[1:4], ["-m", "flow_review.cli", "run"])
        self.assertEqual(command[-1], "--review")


class CLIShimTests(unittest.TestCase):
    def test_shim_is_executable_and_invokes_the_installing_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shim = finder.install_cli_shim("/usr/bin/fake-python3", home=home)
            self.assertTrue(shim.exists())
            self.assertTrue(shim.stat().st_mode & stat.S_IXUSR)
            contents = shim.read_text()
        self.assertIn("/usr/bin/fake-python3", contents)
        self.assertIn("-m flow_review.cli", contents)


class FinderQuickActionTests(unittest.TestCase):
    def test_with_the_app_installed_the_workflow_opens_it_per_selected_folder(self) -> None:
        workflow = finder._workflow_document("/tmp/flow review/bin/python", app_path="/Users/x/Applications/Flow Review.app")
        action = workflow["actions"][0]["action"]
        command = action["ActionParameters"]["COMMAND_STRING"]
        self.assertIn('"$@"', command)
        self.assertIn("open -n -a", command)
        self.assertIn("Flow Review.app", command)
        self.assertIn("--args", command)
        self.assertNotIn("flow_review.cli desktop", command)

    def test_without_the_app_the_workflow_falls_back_to_the_headless_command(self) -> None:
        workflow = finder._workflow_document("/tmp/flow review/bin/python", app_path=None)
        action = workflow["actions"][0]["action"]
        command = action["ActionParameters"]["COMMAND_STRING"]
        self.assertIn('"$@"', command)
        self.assertIn("flow_review.cli desktop", command)

    def test_workflow_is_folder_only_and_registered_as_a_finder_service(self) -> None:
        workflow = finder._workflow_document("/tmp/flow review/bin/python")
        action = workflow["actions"][0]["action"]
        self.assertEqual(action["AMAccepts"]["Types"], ["com.apple.cocoa.string"])
        self.assertEqual(action["ActionParameters"]["inputMethod"], 1)
        self.assertEqual(
            workflow["workflowMetaData"]["serviceInputTypeIdentifier"],
            "com.apple.Automator.fileSystemObject.folder",
        )
        service = finder._workflow_info()["NSServices"][0]
        self.assertEqual(service["NSMenuItem"]["default"], "Flow Review")
        self.assertEqual(service["NSSendFileTypes"], ["public.folder"])
        self.assertNotIn("NSIconName", service)

    def test_mac_install_writes_a_parseable_quick_action_without_touching_real_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            with patch("flow_review.finder.sys.platform", "darwin"):
                installed = finder.install_quick_action(home=fake_home)
            finder.validate_quick_action(installed)
            with (installed / "Contents" / "document.wflow").open("rb") as stream:
                self.assertEqual(plistlib.load(stream)["AMDocumentVersion"], "2")
            with (installed / "Contents" / "Info.plist").open("rb") as stream:
                self.assertEqual(plistlib.load(stream)["NSServices"][0]["NSMessage"], "runWorkflowAsService")
            self.assertTrue(finder.uninstall_quick_action(home=fake_home))


if __name__ == "__main__":
    unittest.main()
