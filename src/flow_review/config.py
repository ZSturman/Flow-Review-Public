"""Loading and validating a project's flow-review/config.yaml."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import paths


class ConfigError(RuntimeError):
    pass


@dataclass
class DeviceConfig:
    platform: str = "iOS"          # iOS | iPadOS | macOS
    name: Optional[str] = "iPhone 17 Pro"
    os_version: Optional[str] = None  # None = latest installed runtime


@dataclass
class XcodeConfig:
    project: Optional[str] = None       # *.xcodeproj (relative path)
    workspace: Optional[str] = None     # *.xcworkspace (relative path)
    scheme: Optional[str] = None
    app_target: Optional[str] = None
    ui_test_target: Optional[str] = None
    bundle_id: Optional[str] = None
    build_configuration: str = "Debug"


@dataclass
class RunConfig:
    reset_simulator: bool = False
    review_delay_ms: int = 200
    review_speed_delay_ms: int = 700
    boot_timeout_s: int = 120
    build_timeout_s: int = 900
    test_timeout_s: int = 300


@dataclass
class LocalAIConfig:
    """An opt-in connection to a model running on this Mac.

    Local servers normally expose the OpenAI-compatible ``/v1`` API.  There
    are deliberately no API-key fields here: Flow Review only supports local
    endpoints for this feature and never needs to persist a secret.
    """

    provider: str = "lm_studio"
    base_url: str = "http://127.0.0.1:1234/v1"
    model: Optional[str] = None


@dataclass
class ProjectConfig:
    id: str
    name: str
    device: DeviceConfig
    xcode: XcodeConfig
    run: RunConfig
    ai: Optional[LocalAIConfig] = None
    flows_dir: str = "flows"
    root: Path = field(default=None, repr=False)  # type: ignore[assignment]

    @property
    def flows_path(self) -> Path:
        return paths.config_dir(self.root) / self.flows_dir

    def to_dict(self) -> dict:
        data = {
            "project": {"id": self.id, "name": self.name},
            "device": {
                "platform": self.device.platform,
                "name": self.device.name,
                "os": self.device.os_version,
            },
            "xcode": {
                "project": self.xcode.project,
                "workspace": self.xcode.workspace,
                "scheme": self.xcode.scheme,
                "app_target": self.xcode.app_target,
                "ui_test_target": self.xcode.ui_test_target,
                "bundle_id": self.xcode.bundle_id,
                "build_configuration": self.xcode.build_configuration,
            },
            "run": {
                "reset_simulator": self.run.reset_simulator,
                "review_delay_ms": self.run.review_delay_ms,
                "review_speed_delay_ms": self.run.review_speed_delay_ms,
                "boot_timeout_s": self.run.boot_timeout_s,
                "build_timeout_s": self.run.build_timeout_s,
                "test_timeout_s": self.run.test_timeout_s,
            },
            "flows_dir": self.flows_dir,
        }
        if self.ai:
            data["ai"] = {
                "provider": self.ai.provider,
                "base_url": self.ai.base_url,
                "model": self.ai.model,
            }
        return data

    def save(self) -> None:
        path = paths.config_file(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)


def new_project_id() -> str:
    return uuid.uuid4().hex[:12]


def load_config(project_root: Path) -> ProjectConfig:
    path = paths.config_file(project_root)
    if not path.exists():
        raise ConfigError(
            f"No flow-review config found at {path}.\n"
            f"Run `flow-review init` inside this project first."
        )
    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    project = raw.get("project") or {}
    device = raw.get("device") or {}
    xcode = raw.get("xcode") or {}
    run = raw.get("run") or {}
    ai = raw.get("ai") or None

    if "id" not in project or "name" not in project:
        raise ConfigError(f"{path} is missing project.id / project.name")

    return ProjectConfig(
        id=project["id"],
        name=project["name"],
        device=DeviceConfig(
            platform=device.get("platform", "iOS"),
            name=device.get("name", "iPhone 17 Pro"),
            os_version=device.get("os"),
        ),
        xcode=XcodeConfig(
            project=xcode.get("project"),
            workspace=xcode.get("workspace"),
            scheme=xcode.get("scheme"),
            app_target=xcode.get("app_target"),
            ui_test_target=xcode.get("ui_test_target"),
            bundle_id=xcode.get("bundle_id"),
            build_configuration=xcode.get("build_configuration", "Debug"),
        ),
        run=RunConfig(
            reset_simulator=run.get("reset_simulator", False),
            review_delay_ms=run.get("review_delay_ms", 200),
            review_speed_delay_ms=run.get("review_speed_delay_ms", 700),
            boot_timeout_s=run.get("boot_timeout_s", 120),
            build_timeout_s=run.get("build_timeout_s", 900),
            test_timeout_s=run.get("test_timeout_s", 300),
        ),
        ai=(LocalAIConfig(
            provider=ai.get("provider", "lm_studio"),
            base_url=ai.get("base_url", "http://127.0.0.1:1234/v1"),
            model=ai.get("model"),
        ) if isinstance(ai, dict) else None),
        flows_dir=raw.get("flows_dir", "flows"),
        root=project_root,
    )


def find_project_root(start: Path) -> Path:
    """Walk upward from `start` looking for a flow-review/config.yaml."""
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if paths.config_file(candidate).exists():
            return candidate
    raise ConfigError(
        "Could not find a flow-review/config.yaml in this directory or any parent.\n"
        "Run `flow-review init` inside your Xcode project first."
    )
