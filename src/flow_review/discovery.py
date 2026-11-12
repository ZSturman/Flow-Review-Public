"""Discovering the shape of an existing Xcode project: .xcodeproj/.xcworkspace,
schemes, targets, and platforms. Reuses `xcodebuild -list` and the xcodeproj
gem rather than re-implementing pbxproj parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import procutil


class DiscoveryError(RuntimeError):
    pass


@dataclass
class TargetInfo:
    name: str
    product_type: str
    platform: str
    bundle_id: Optional[str]
    is_app: bool
    is_ui_test: bool
    is_unit_test: bool


@dataclass
class ProjectInfo:
    root: Path
    xcodeproj: Optional[Path]
    xcworkspace: Optional[Path]
    schemes: list[str] = field(default_factory=list)
    targets: list[TargetInfo] = field(default_factory=list)

    @property
    def app_targets(self) -> list[TargetInfo]:
        return [t for t in self.targets if t.is_app]

    @property
    def ui_test_targets(self) -> list[TargetInfo]:
        return [t for t in self.targets if t.is_ui_test]

    @property
    def primary_build_file(self) -> Path:
        # Prefer a workspace if present (CocoaPods/SPM setups usually need it),
        # otherwise the .xcodeproj directly.
        return self.xcworkspace or self.xcodeproj


def find_xcode_containers(directory: Path) -> tuple[Optional[Path], Optional[Path]]:
    xcodeprojs = sorted(directory.glob("*.xcodeproj"))
    xcworkspaces = [w for w in sorted(directory.glob("*.xcworkspace")) if "project.xcworkspace" not in str(w)]
    xcodeproj = xcodeprojs[0] if xcodeprojs else None
    xcworkspace = xcworkspaces[0] if xcworkspaces else None
    return xcodeproj, xcworkspace


def list_schemes(build_file: Path) -> list[str]:
    flag = "-workspace" if build_file.suffix == ".xcworkspace" else "-project"
    result = procutil.run(["xcodebuild", "-list", "-json", flag, str(build_file)], timeout=60)
    if not result.ok:
        raise DiscoveryError(f"xcodebuild -list failed: {result.stderr.strip()}")
    import json
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise DiscoveryError(f"Could not parse xcodebuild -list output:\n{result.stdout}")
    key = "workspace" if "workspace" in data else "project"
    return data.get(key, {}).get("schemes", [])


def inspect_targets(xcodeproj: Path) -> tuple[list[TargetInfo], list[str]]:
    data = procutil.xcode_helper("inspect", {"project_path": str(xcodeproj)})
    targets = [
        TargetInfo(
            name=t["name"],
            product_type=t["product_type"],
            platform=t["platform"],
            bundle_id=t.get("bundle_id"),
            is_app=t["is_app"],
            is_ui_test=t["is_ui_test"],
            is_unit_test=t["is_unit_test"],
        )
        for t in data.get("targets", [])
    ]
    schemes = data.get("schemes", [])
    return targets, schemes


def discover(directory: Path) -> ProjectInfo:
    xcodeproj, xcworkspace = find_xcode_containers(directory)
    if not xcodeproj and not xcworkspace:
        raise DiscoveryError(
            f"No .xcodeproj or .xcworkspace found in {directory}.\n"
            f"Run `flow-review init` from inside your Xcode project's root directory."
        )

    info = ProjectInfo(root=directory, xcodeproj=xcodeproj, xcworkspace=xcworkspace)

    build_file = info.primary_build_file
    try:
        info.schemes = list_schemes(build_file)
    except DiscoveryError:
        info.schemes = []

    if xcodeproj:
        try:
            info.targets, scheme_files = inspect_targets(xcodeproj)
            if not info.schemes:
                info.schemes = scheme_files
        except procutil.RubyHelperError as e:
            if e.missing_gem:
                raise DiscoveryError(
                    "Flow Review needs the 'xcodeproj' Ruby gem to inspect/bootstrap Xcode "
                    "projects reliably. Install it with:\n\n    gem install xcodeproj\n"
                ) from e
            raise DiscoveryError(f"Failed to inspect {xcodeproj}: {e}") from e

    return info
