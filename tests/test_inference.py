from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow_review import config, discovery, inference, simulator

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PROJECT = REPO_ROOT / "sample_project" / "SampleFlowApp"


def _target(name: str, platform: str = "ios", is_app: bool = False, is_ui_test: bool = False) -> discovery.TargetInfo:
    return discovery.TargetInfo(
        name=name, product_type="x", platform=platform, bundle_id=None,
        is_app=is_app, is_ui_test=is_ui_test, is_unit_test=False,
    )


class AppTargetInferenceTests(unittest.TestCase):
    def test_existing_config_wins_even_when_another_candidate_exists(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            targets=[_target("App", is_app=True), _target("Watch", is_app=True)],
        )
        choice = inference.infer_app_target(info, "Watch")
        self.assertEqual(choice.value, "Watch")
        self.assertEqual(choice.source, "existing_config")
        self.assertFalse(choice.is_ambiguous)

    def test_single_app_target_is_inferred_without_asking(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            targets=[_target("App", is_app=True), _target("AppTests", is_ui_test=True)],
        )
        choice = inference.infer_app_target(info, None)
        self.assertEqual(choice.value, "App")
        self.assertEqual(choice.source, "single_candidate")

    def test_multiple_app_targets_without_existing_config_is_genuinely_ambiguous(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            targets=[_target("App", is_app=True), _target("AppWatch", is_app=True)],
        )
        choice = inference.infer_app_target(info, None)
        self.assertIsNone(choice.value)
        self.assertTrue(choice.is_ambiguous)
        self.assertEqual(choice.options, ["App", "AppWatch"])

    def test_no_app_target_is_unavailable_not_ambiguous(self) -> None:
        info = discovery.ProjectInfo(root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None, targets=[])
        choice = inference.infer_app_target(info, None)
        self.assertFalse(choice.is_ambiguous)
        self.assertEqual(choice.source, "unavailable")


class SchemeInferenceTests(unittest.TestCase):
    def test_scheme_matching_app_target_name_is_preferred(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            schemes=["App", "AppDomain", "AppWidgets"],
        )
        choice = inference.infer_scheme(info, "App", None)
        self.assertEqual(choice.value, "App")
        self.assertEqual(choice.source, "name_match")

    def test_schemes_with_no_name_match_and_no_single_candidate_are_ambiguous(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            schemes=["AppDomain", "AppWidgets"],
        )
        choice = inference.infer_scheme(info, "App", None)
        self.assertTrue(choice.is_ambiguous)
        self.assertEqual(choice.options, ["AppDomain", "AppWidgets"])


class PlatformInferenceTests(unittest.TestCase):
    def test_ios_target_defaults_to_ios_and_offers_ipados_as_an_override(self) -> None:
        choice = inference.infer_platform(_target("App", platform="ios"), None)
        self.assertEqual(choice.value, "iOS")
        self.assertIn("iPadOS", choice.options)
        self.assertFalse(choice.is_ambiguous)

    def test_macos_target_never_offers_a_simulator_platform(self) -> None:
        choice = inference.infer_platform(_target("App", platform="osx"), None)
        self.assertEqual(choice.value, "macOS")
        self.assertEqual(choice.options, ["macOS"])

    def test_existing_ipados_choice_is_respected_for_an_ios_target(self) -> None:
        choice = inference.infer_platform(_target("App", platform="ios"), "iPadOS")
        self.assertEqual(choice.value, "iPadOS")
        self.assertEqual(choice.source, "existing_config")


class UITestTargetInferenceTests(unittest.TestCase):
    def test_single_related_target_matching_app_prefix_is_inferred(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            targets=[_target("AppFlowReviewUITests", is_ui_test=True), _target("OtherUITests", is_ui_test=True)],
        )
        choice = inference.infer_ui_test_target(info, "App", None)
        self.assertEqual(choice.value, "AppFlowReviewUITests")
        self.assertEqual(choice.source, "name_match")

    def test_two_unrelated_existing_targets_fall_back_to_the_conventional_name(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None,
            targets=[_target("SmokeUITests", is_ui_test=True), _target("RegressionUITests", is_ui_test=True)],
        )
        choice = inference.infer_ui_test_target(info, "App", None)
        self.assertEqual(choice.value, "AppFlowReviewUITests")  # never blocks; a new target is safe to add
        self.assertEqual(choice.source, "default")

    def test_no_existing_target_gets_a_safe_default_name_not_a_blocking_question(self) -> None:
        info = discovery.ProjectInfo(root=Path("."), xcodeproj=Path("A.xcodeproj"), xcworkspace=None, targets=[])
        choice = inference.infer_ui_test_target(info, "App", None)
        self.assertEqual(choice.value, "AppFlowReviewUITests")
        self.assertFalse(choice.is_ambiguous)
        self.assertEqual(choice.source, "default")


class SimulatorInferenceTests(unittest.TestCase):
    def _devices(self) -> list[simulator.SimDevice]:
        return [
            simulator.SimDevice("u1", "iPhone 17 Pro", "Shutdown", "com.apple.CoreSimulator.SimRuntime.iOS-26-5", True),
            simulator.SimDevice("u2", "iPhone 17", "Shutdown", "com.apple.CoreSimulator.SimRuntime.iOS-26-5", True),
        ]

    def test_single_matching_family_device_is_inferred(self) -> None:
        with patch("flow_review.inference.simulator.list_devices", return_value=self._devices()[:1]):
            choice, _ = inference.infer_simulator("iOS", None, None)
        self.assertEqual(choice.value, "iPhone 17 Pro")
        self.assertEqual(choice.source, "single_candidate")

    def test_multiple_devices_without_a_booted_one_is_ambiguous(self) -> None:
        with patch("flow_review.inference.simulator.list_devices", return_value=self._devices()):
            choice, _ = inference.infer_simulator("iOS", None, None)
        self.assertTrue(choice.is_ambiguous)

    def test_booted_device_breaks_the_tie_without_asking(self) -> None:
        devices = self._devices()
        devices[1].state = "Booted"
        with patch("flow_review.inference.simulator.list_devices", return_value=devices):
            choice, _ = inference.infer_simulator("iOS", None, None)
        self.assertEqual(choice.value, "iPhone 17")
        self.assertEqual(choice.source, "booted")

    def test_no_devices_installed_is_an_actionable_error_not_a_picker(self) -> None:
        with patch("flow_review.inference.simulator.list_devices", return_value=[]):
            choice, _ = inference.infer_simulator("iOS", None, None)
        self.assertFalse(choice.is_ambiguous)
        self.assertEqual(choice.source, "unavailable")

    def test_macos_platform_needs_no_simulator(self) -> None:
        choice, devices = inference.infer_simulator("macOS", None, None)
        self.assertEqual(choice.source, "unavailable")
        self.assertEqual(devices, [])


@unittest.skipUnless(SAMPLE_PROJECT.exists(), "bundled sample project is required")
class AnalyzeAgainstRealSampleProjectTests(unittest.TestCase):
    """Exercises analyze() against the repository's real .xcodeproj via xcodebuild -list,
    not a constructed fixture -- this is the actual discovery code path."""

    def test_fully_configured_sample_project_needs_no_questions(self) -> None:
        analysis = inference.analyze(SAMPLE_PROJECT)
        self.assertTrue(analysis.has_xcodeproj)
        self.assertEqual(analysis.app_target.value, "SampleFlowApp")
        self.assertEqual(analysis.scheme.value, "SampleFlowApp")
        self.assertTrue(analysis.is_fully_resolved())
        self.assertTrue(analysis.ready_to_run)
        self.assertEqual(analysis.needs_setup, False)

    def test_missing_project_directory_is_reported_as_no_xcode_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            analysis = inference.analyze(Path(tmp))
        self.assertFalse(analysis.has_xcodeproj)
        self.assertTrue(analysis.errors)


class AnalyzeAmbiguityAggregationTests(unittest.TestCase):
    """The ambiguity branches above are proven individually against real target
    lists; this proves analyze() aggregates them into needs_setup correctly.
    Two application targets is a real, reachable shape (a watchOS companion
    app target sits alongside its iOS host in the same project), so the
    fixture below is patched at the discovery boundary rather than invented
    data shaped implausibly.
    """

    def test_two_app_targets_surface_as_needing_a_choice(self) -> None:
        info = discovery.ProjectInfo(
            root=Path("/tmp/proj"), xcodeproj=Path("/tmp/proj/App.xcodeproj"), xcworkspace=None,
            schemes=["App"],
            targets=[_target("App", is_app=True), _target("AppWatch", is_app=True)],
        )
        with tempfile.TemporaryDirectory() as tmp, \
             patch("flow_review.inference.discovery.discover", return_value=info), \
             patch("flow_review.inference.config.load_config", side_effect=config.ConfigError("none")), \
             patch("flow_review.inference.simulator.list_devices", return_value=[]):
            analysis = inference.analyze(Path(tmp))
        self.assertTrue(analysis.app_target.is_ambiguous)
        self.assertTrue(analysis.needs_setup)
        self.assertFalse(analysis.ready_to_run)


if __name__ == "__main__":
    unittest.main()
