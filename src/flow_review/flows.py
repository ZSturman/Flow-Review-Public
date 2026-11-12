"""Flow specification: parsing project flow YAML into a canonical, validated form.

Design intent (see docs): flows describe *metadata* (id, tags, platform,
launch config) plus a small, fixed vocabulary of steps that map 1:1 onto
XCUITest calls. This is deliberately NOT a general automation language -- if
a flow needs real logic, write an XCUITest method by hand instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Step types that take no parameters and may appear as a bare string.
BARE_STEPS = {"launch", "terminate", "relaunch"}

# Step type -> required keys, optional keys (for validation + doctor).
STEP_SCHEMA: dict[str, dict[str, list[str]]] = {
    "launch": {"required": [], "optional": []},
    "terminate": {"required": [], "optional": []},
    "relaunch": {"required": [], "optional": []},
    "tap": {"required": [], "optional": ["accessibility_id", "text", "label", "index"]},
    "type": {"required": ["value"], "optional": ["accessibility_id", "label", "clear_first"]},
    "swipe": {"required": ["direction"], "optional": ["accessibility_id"]},
    "scroll": {"required": ["direction"], "optional": ["accessibility_id"]},
    "wait_for": {"required": [], "optional": ["accessibility_id", "text", "label", "timeout"]},
    "assert_exists": {"required": [], "optional": ["accessibility_id", "text", "label"]},
    "assert_not_exists": {"required": [], "optional": ["accessibility_id", "text", "label"]},
    "assert_text": {"required": ["accessibility_id"], "optional": ["equals", "contains"]},
    "pause": {"required": [], "optional": ["duration_ms"]},
    "screenshot": {"required": ["name"], "optional": []},
    "checkpoint": {"required": ["name"], "optional": []},
    "reset_app_state": {"required": [], "optional": []},
}

VALID_PLATFORMS = {"ios", "ipados", "macos"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class FlowError(RuntimeError):
    pass


@dataclass
class Step:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    raw_index: int = 0

    def to_json(self) -> dict:
        d = {"type": self.type}
        d.update(self.params)
        return d


@dataclass
class Flow:
    id: str
    name: str
    source_path: Path
    platform: Optional[str] = None  # None = all platforms
    tags: list[str] = field(default_factory=list)
    launch_arguments: list[str] = field(default_factory=list)
    launch_environment: dict[str, str] = field(default_factory=dict)
    review_delay_ms: Optional[int] = None
    reset_app_state: bool = False
    steps: list[Step] = field(default_factory=list)

    def swift_test_name(self) -> str:
        return "test_" + self.id.replace("-", "_").replace(" ", "_")

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "tags": self.tags,
            "launch_arguments": self.launch_arguments,
            "launch_environment": self.launch_environment,
            "review_delay_ms": self.review_delay_ms,
            "reset_app_state": self.reset_app_state,
            "steps": [s.to_json() for s in self.steps],
        }


def _normalize_step(raw: Any, idx: int, flow_id: str) -> Step:
    if isinstance(raw, str):
        step_type = raw
        params: dict[str, Any] = {}
    elif isinstance(raw, dict):
        if len(raw) != 1:
            raise FlowError(
                f"{flow_id}: step {idx} must have exactly one key "
                f"(e.g. `tap:`), got {list(raw.keys())}"
            )
        step_type, body = next(iter(raw.items()))
        params = dict(body) if body else {}
    else:
        raise FlowError(f"{flow_id}: step {idx} has an unsupported shape: {raw!r}")

    if step_type not in STEP_SCHEMA:
        known = ", ".join(sorted(STEP_SCHEMA))
        raise FlowError(f"{flow_id}: step {idx} has unknown type '{step_type}'. Known types: {known}")

    schema = STEP_SCHEMA[step_type]
    for key in schema["required"]:
        if key not in params:
            raise FlowError(f"{flow_id}: step {idx} ('{step_type}') is missing required field '{key}'")
    allowed = set(schema["required"]) | set(schema["optional"])
    for key in params:
        if key not in allowed:
            raise FlowError(
                f"{flow_id}: step {idx} ('{step_type}') has unexpected field '{key}'. "
                f"Allowed: {sorted(allowed)}"
            )

    # Steps that act on a concrete element need a locator. Scrolling and
    # swiping deliberately do not: they may target the application itself.
    locator_steps = {"tap", "type", "wait_for", "assert_exists", "assert_not_exists"}
    if step_type in locator_steps and not any(
        key in params for key in ("accessibility_id", "text", "label")
    ):
        raise FlowError(
            f"{flow_id}: step {idx} ('{step_type}') needs a locator "
            f"(accessibility_id, text, or label)"
        )

    if step_type == "tap" and "index" in params:
        index = params["index"]
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise FlowError(f"{flow_id}: step {idx} ('tap') index must be a non-negative integer")

    if step_type == "assert_text" and not any(key in params for key in ("equals", "contains")):
        raise FlowError(
            f"{flow_id}: step {idx} ('assert_text') needs at least one expected value "
            f"(equals or contains)"
        )

    return Step(type=step_type, params=params, raw_index=idx)


def parse_flow_file(path: Path) -> Flow:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    flow_id = raw.get("id")
    if not flow_id:
        raise FlowError(f"{path}: missing required 'id'")
    if not ID_RE.match(flow_id):
        raise FlowError(f"{path}: id '{flow_id}' must be lowercase kebab-case (e.g. 'create-item')")

    name = raw.get("name") or flow_id
    platform = raw.get("platform")
    if platform is not None:
        platform = str(platform).lower()
        if platform not in VALID_PLATFORMS:
            raise FlowError(f"{flow_id}: platform '{platform}' must be one of {sorted(VALID_PLATFORMS)}")

    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise FlowError(f"{flow_id}: tags must be a list")

    launch_arguments = raw.get("launch_arguments") or []
    launch_environment = raw.get("launch_environment") or {}

    raw_steps = raw.get("steps")
    if not raw_steps or not isinstance(raw_steps, list):
        raise FlowError(f"{flow_id}: 'steps' must be a non-empty list")

    steps = [_normalize_step(s, i, flow_id) for i, s in enumerate(raw_steps)]

    return Flow(
        id=flow_id,
        name=name,
        source_path=path,
        platform=platform,
        tags=[str(t) for t in tags],
        launch_arguments=[str(a) for a in launch_arguments],
        launch_environment={str(k): str(v) for k, v in launch_environment.items()},
        review_delay_ms=raw.get("review_delay_ms"),
        reset_app_state=bool(raw.get("reset_app_state", False)),
        steps=steps,
    )


def load_flows(flows_dir: Path) -> list[Flow]:
    if not flows_dir.exists():
        return []
    flows = []
    for path in sorted(flows_dir.glob("*.yaml")) + sorted(flows_dir.glob("*.yml")):
        flows.append(parse_flow_file(path))
    ids = [f.id for f in flows]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise FlowError(f"Duplicate flow ids found: {sorted(dupes)}")
    return flows


def select_flows(
    flows: list[Flow],
    flow_ids: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    platform: Optional[str] = None,
) -> list[Flow]:
    selected = flows
    if flow_ids:
        wanted = set(flow_ids)
        selected = [f for f in selected if f.id in wanted]
    if tags:
        wanted_tags = set(tags)
        selected = [f for f in selected if wanted_tags & set(f.tags)]
    if platform:
        p = platform.lower()
        selected = [f for f in selected if f.platform is None or f.platform == p]
    return selected
