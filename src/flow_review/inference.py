"""Deterministic inference over a discovered project.

This is the piece that replaces a chain of setup dialogs: given whatever
Flow Review can observe about a project (Xcode targets/schemes, an existing
``flow-review/config.yaml``, installed simulators, existing flows), decide
everything that can be decided safely, and surface only genuine ambiguity to
a caller (CLI flag, or the macOS UI).

Nothing here prompts or prints. ``analyze()`` is a pure function of project
state so it can be unit tested without a GUI and reused by both the CLI's
``analyze``/``doctor`` commands and the macOS app.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config, discovery, paths, procutil, simulator
from .flows import FlowError, load_flows

# Product-type platform strings the Ruby helper reports, mapped onto the
# review-destination platforms Flow Review understands. A given Xcode target
# can only tell us "ios" (which covers both iPhone and iPad) or "osx" -- the
# iOS/iPadOS distinction is a *review destination* choice, not something the
# target itself encodes, so it is never treated as ambiguous discovery.
_TARGET_PLATFORM_TO_REVIEW_PLATFORM = {"ios": "iOS", "osx": "macOS"}


@dataclass
class Choice:
    """One inferred setting.

    ``value`` is set when Flow Review is confident; ``options`` is the full
    candidate list (populated whenever there is more than one plausible
    value, even when a value was still confidently chosen, so a UI can offer
    it as an override without a second discovery round-trip).
    """

    value: Optional[str] = None
    options: list[str] = field(default_factory=list)
    source: str = "unavailable"  # existing_config | single_candidate | name_match | booted | default | ambiguous | unavailable
    detail: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.value is None and len(self.options) > 1

    @property
    def is_resolved(self) -> bool:
        return self.value is not None

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "options": self.options,
            "source": self.source,
            "ambiguous": self.is_ambiguous,
            "detail": self.detail,
        }


def infer_app_target(info: discovery.ProjectInfo, existing: Optional[str]) -> Choice:
    apps = info.app_targets
    names = [t.name for t in apps]
    if existing and existing in names:
        return Choice(existing, names, "existing_config")
    if len(apps) == 1:
        return Choice(names[0], names, "single_candidate")
    if not apps:
        return Choice(None, [], "unavailable", "No application target was found.")
    return Choice(None, names, "ambiguous", "Multiple application targets were found.")


def infer_scheme(info: discovery.ProjectInfo, app_target: Optional[str], existing: Optional[str]) -> Choice:
    schemes = info.schemes
    if existing and existing in schemes:
        return Choice(existing, schemes, "existing_config")
    if app_target and app_target in schemes:
        return Choice(app_target, schemes, "name_match", f"Matches app target '{app_target}'.")
    if len(schemes) == 1:
        return Choice(schemes[0], schemes, "single_candidate")
    if not schemes:
        return Choice(None, [], "unavailable", "No shared Xcode scheme was found.")
    return Choice(None, schemes, "ambiguous", "Multiple build schemes were found.")


def infer_platform(app_target_info: Optional[discovery.TargetInfo], existing: Optional[str]) -> Choice:
    target_platform = _TARGET_PLATFORM_TO_REVIEW_PLATFORM.get(
        (app_target_info.platform if app_target_info else "ios"), "iOS"
    )
    options = ["iOS", "iPadOS", "macOS"] if target_platform == "iOS" else ["macOS"]
    if existing and existing in options:
        return Choice(existing, options, "existing_config")
    return Choice(target_platform, options, "single_candidate", f"Inferred from the app target's platform ({target_platform}).")


def infer_ui_test_target(info: discovery.ProjectInfo, app_target: Optional[str], existing: Optional[str]) -> Choice:
    existing_targets = [t.name for t in info.ui_test_targets]
    if existing:
        source = "existing_config" if existing in existing_targets else "existing_config"
        detail = "" if existing in existing_targets else "Will be created."
        return Choice(existing, existing_targets, source, detail)
    related = [t.name for t in info.ui_test_targets if app_target and t.name.startswith(app_target)]
    if len(related) == 1:
        return Choice(related[0], existing_targets, "name_match")
    if len(existing_targets) == 1:
        return Choice(existing_targets[0], existing_targets, "single_candidate")
    default = f"{app_target}FlowReviewUITests" if app_target else "FlowReviewUITests"
    return Choice(default, existing_targets, "default", "A new UI-test target will be created with this name.")


def _ios_family_devices(devices: list[simulator.SimDevice], platform: str) -> list[simulator.SimDevice]:
    if platform.lower() == "ipados":
        family = [d for d in devices if "ipad" in d.name.lower()]
    else:
        family = [d for d in devices if "iphone" in d.name.lower()]
    return family or devices


def infer_simulator(
    platform: str, existing_name: Optional[str], existing_os: Optional[str]
) -> tuple[Choice, list[simulator.SimDevice]]:
    if platform.lower() in ("macos", "osx"):
        return Choice(None, [], "unavailable", "macOS review runs on this Mac; no simulator is needed."), []
    try:
        all_devices = simulator.list_devices(platform_filter="ios")
    except simulator.SimulatorError as exc:
        return Choice(None, [], "unavailable", str(exc)), []

    candidates = all_devices
    if existing_os:
        os_filtered = [d for d in candidates if existing_os.replace(".", "-") in d.runtime]
        candidates = os_filtered or candidates
    family = _ios_family_devices(candidates, platform)
    names = sorted({d.name for d in family})

    if existing_name and existing_name in names:
        return Choice(existing_name, names, "existing_config"), family
    if len(names) == 1:
        return Choice(names[0], names, "single_candidate"), family
    booted = [d for d in family if d.state == "Booted"]
    if booted:
        return Choice(booted[0].name, names, "booted", "Currently booted."), family
    if not names:
        return Choice(None, [], "unavailable", f"No installed {platform} simulator was found."), family
    return Choice(None, names, "ambiguous", "Multiple simulators are installed."), family


@dataclass
class ProjectAnalysis:
    root: Path
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    has_xcodeproj: bool = False
    xcodeproj_path: Optional[str] = None
    xcworkspace_path: Optional[str] = None

    app_targets: list[str] = field(default_factory=list)
    schemes: list[str] = field(default_factory=list)
    ui_test_targets: list[str] = field(default_factory=list)

    existing_config: bool = False
    project_name: str = ""
    flow_count: int = 0
    flow_ids: list[str] = field(default_factory=list)

    app_target: Choice = field(default_factory=Choice)
    scheme: Choice = field(default_factory=Choice)
    platform: Choice = field(default_factory=Choice)
    ui_test_target: Choice = field(default_factory=Choice)
    simulator: Choice = field(default_factory=Choice)

    environment_ok: bool = True
    environment_detail: list[dict] = field(default_factory=list)

    ready_to_run: bool = False
    needs_setup: bool = False
    blocking_reason: str = ""

    def is_fully_resolved(self) -> bool:
        """True when nothing here needs a picker -- Advanced overrides still work."""
        return not any(c.is_ambiguous for c in (self.app_target, self.scheme, self.ui_test_target, self.simulator))

    def to_json(self) -> dict:
        return {
            "root": str(self.root),
            "steps": self.steps,
            "errors": self.errors,
            "has_xcodeproj": self.has_xcodeproj,
            "xcodeproj_path": self.xcodeproj_path,
            "xcworkspace_path": self.xcworkspace_path,
            "app_targets": self.app_targets,
            "schemes": self.schemes,
            "ui_test_targets": self.ui_test_targets,
            "existing_config": self.existing_config,
            "project_name": self.project_name,
            "flow_count": self.flow_count,
            "flow_ids": self.flow_ids,
            "app_target": self.app_target.to_json(),
            "scheme": self.scheme.to_json(),
            "platform": self.platform.to_json(),
            "ui_test_target": self.ui_test_target.to_json(),
            "simulator": self.simulator.to_json(),
            "environment_ok": self.environment_ok,
            "environment": self.environment_detail,
            "ready_to_run": self.ready_to_run,
            "needs_setup": self.needs_setup,
            "blocking_reason": self.blocking_reason,
            "fully_resolved": self.is_fully_resolved(),
        }


def _check_environment() -> tuple[bool, list[dict]]:
    checks = []
    ok = True
    for name in ("xcodebuild", "xcrun", "ruby"):
        found = shutil.which(name)
        checks.append({"name": name, "ok": bool(found), "detail": found or f"'{name}' was not found on PATH"})
        ok = ok and bool(found)
    if shutil.which("ruby"):
        result = procutil.run(["ruby", "-e", "require 'xcodeproj'; puts Xcodeproj::VERSION"], timeout=20)
        checks.append({"name": "xcodeproj gem", "ok": result.ok, "detail": result.stdout.strip() or result.stderr.strip()})
        ok = ok and result.ok
    return ok, checks


def find_project_root(value: Path) -> Path:
    """Resolve a Finder/CLI selected folder to the project root Flow Review should analyze.

    Walks upward looking for an existing ``flow-review/config.yaml``; falls
    back to the selected folder itself so first-time analysis still works.
    """
    root = value.expanduser().resolve()
    try:
        return config.find_project_root(root)
    except config.ConfigError:
        return root


def analyze(value: Path) -> ProjectAnalysis:
    root = find_project_root(Path(value))
    analysis = ProjectAnalysis(root=root)
    analysis.steps.append(f"Analyzing {root.name}…")

    env_ok, env_detail = _check_environment()
    analysis.environment_ok = env_ok
    analysis.environment_detail = env_detail
    if not env_ok:
        missing = ", ".join(c["name"] for c in env_detail if not c["ok"])
        analysis.errors.append(f"Missing required tooling: {missing}.")

    try:
        existing_cfg = config.load_config(root)
        analysis.existing_config = True
        analysis.project_name = existing_cfg.name
        analysis.steps.append("Found existing Flow Review configuration")
    except config.ConfigError:
        existing_cfg = None
        analysis.project_name = root.name

    try:
        info = discovery.discover(root)
    except discovery.DiscoveryError as exc:
        analysis.has_xcodeproj = False
        analysis.errors.append(str(exc))
        analysis.steps.append("No Xcode project found")
        analysis.blocking_reason = str(exc)
        return analysis

    analysis.has_xcodeproj = True
    analysis.xcodeproj_path = str(info.xcodeproj) if info.xcodeproj else None
    analysis.xcworkspace_path = str(info.xcworkspace) if info.xcworkspace else None
    analysis.app_targets = [t.name for t in info.app_targets]
    analysis.schemes = info.schemes
    analysis.ui_test_targets = [t.name for t in info.ui_test_targets]
    analysis.steps.append(f"Found Xcode project: {info.xcodeproj.name if info.xcodeproj else info.xcworkspace.name}")

    existing_app = existing_cfg.xcode.app_target if existing_cfg else None
    existing_scheme = existing_cfg.xcode.scheme if existing_cfg else None
    existing_platform = existing_cfg.device.platform if existing_cfg else None
    existing_ui_target = existing_cfg.xcode.ui_test_target if existing_cfg else None
    existing_device = existing_cfg.device.name if existing_cfg else None
    existing_os = existing_cfg.device.os_version if existing_cfg else None

    analysis.app_target = infer_app_target(info, existing_app)
    if analysis.app_target.is_resolved:
        analysis.steps.append(f"Detected app target: {analysis.app_target.value}")
    elif analysis.app_target.is_ambiguous:
        analysis.steps.append("Multiple app targets found; choose one")

    app_target_info = next((t for t in info.app_targets if t.name == analysis.app_target.value), None)

    analysis.scheme = infer_scheme(info, analysis.app_target.value, existing_scheme)
    if analysis.scheme.is_resolved:
        analysis.steps.append(f"Found scheme: {analysis.scheme.value}")
    elif analysis.scheme.is_ambiguous:
        analysis.steps.append("Multiple schemes found; choose one")

    analysis.platform = infer_platform(app_target_info, existing_platform)
    analysis.steps.append(f"Review platform: {analysis.platform.value}")

    analysis.ui_test_target = infer_ui_test_target(info, analysis.app_target.value, existing_ui_target)
    if analysis.ui_test_target.value in analysis.ui_test_targets:
        analysis.steps.append(f"Found existing Flow Review UI-test target: {analysis.ui_test_target.value}")
    elif analysis.ui_test_target.value:
        analysis.steps.append(f"Will create UI-test target: {analysis.ui_test_target.value}")

    if analysis.platform.value and analysis.platform.value.lower() != "macos":
        analysis.steps.append("Checking simulators…")
        sim_choice, _devices = infer_simulator(analysis.platform.value, existing_device, existing_os)
        analysis.simulator = sim_choice
        if sim_choice.is_resolved:
            analysis.steps.append(f"Simulator available: {sim_choice.value}")
        elif sim_choice.is_ambiguous:
            analysis.steps.append("Multiple simulators available; choose one")
        else:
            analysis.steps.append("No simulator available")
            analysis.errors.append(sim_choice.detail or "No simulator available.")
    else:
        analysis.simulator = Choice(None, [], "unavailable", "macOS review runs on this Mac.")

    if existing_cfg:
        try:
            flows = load_flows(existing_cfg.flows_path)
            analysis.flow_count = len(flows)
            analysis.flow_ids = [f.id for f in flows]
        except FlowError as exc:
            analysis.errors.append(f"A flow YAML file needs attention: {exc}")

    ui_target_created = analysis.ui_test_target.value in analysis.ui_test_targets
    missing_setup: list[str] = []
    if not analysis.existing_config:
        missing_setup.append("configuration has not been created yet")
    elif not ui_target_created:
        missing_setup.append("the UI-test target has not been created yet")
    if analysis.flow_count == 0:
        missing_setup.append("no flows exist yet")

    if analysis.app_target.is_ambiguous or analysis.scheme.is_ambiguous or analysis.simulator.is_ambiguous:
        analysis.needs_setup = True
        analysis.blocking_reason = "Choose the highlighted settings, then continue."
    elif missing_setup:
        analysis.needs_setup = True
        analysis.blocking_reason = "Flow Review needs to add its project configuration: " + "; ".join(missing_setup) + "."
    elif not env_ok:
        analysis.needs_setup = True
        analysis.blocking_reason = "Install the missing tooling above, then try again."
    else:
        analysis.ready_to_run = True
        analysis.steps.append("Configuration ready")

    return analysis
