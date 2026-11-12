"""macOS desktop entry points: Finder hand-off for headless environments.

The Flow Review macOS app (macapp/FlowReviewApp) is the normal Finder entry
point now -- it performs project analysis and any setup through one
consolidated window (see inference.py). This module remains as the
CLI-only fallback used when the app could not be built/installed (for
example, a machine without Xcode or xcodegen): it deterministically starts
a ready project's review in Terminal, or reports what is missing without
opening any dialogs. It never blocks on interactive input.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config
from .flows import FlowError, load_flows


class DesktopError(RuntimeError):
    """The Finder/Desktop integration could not complete its hand-off."""


@dataclass(frozen=True)
class ProjectReadiness:
    root: Path
    ready: bool
    reason: str
    flow_count: int = 0


def selected_project_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise DesktopError(f"Select a project folder, not a file: {root}")
    try:
        return config.find_project_root(root)
    except config.ConfigError:
        return root


def assess_project(value: str | Path) -> ProjectReadiness:
    """Return a non-mutating readiness result suitable for Finder launches."""
    root = selected_project_root(value)
    try:
        cfg = config.load_config(root)
    except config.ConfigError as exc:
        return ProjectReadiness(root, False, str(exc))

    missing = [
        field for field, value in (
            ("xcode.project", cfg.xcode.project),
            ("xcode.scheme", cfg.xcode.scheme),
            ("xcode.ui_test_target", cfg.xcode.ui_test_target),
        ) if not value
    ]
    if missing:
        return ProjectReadiness(root, False, "Configuration is missing " + ", ".join(missing) + ".")

    project = Path(cfg.xcode.project or "")
    if not project.is_absolute():
        project = root / project
    if project.suffix != ".xcodeproj" or not project.exists():
        return ProjectReadiness(root, False, "The configured Xcode project is missing or is not an .xcodeproj.")

    try:
        flows = load_flows(cfg.flows_path)
    except FlowError as exc:
        return ProjectReadiness(root, False, f"A flow YAML file needs attention: {exc}")
    if not flows:
        return ProjectReadiness(
            root,
            False,
            "Configuration exists, but there are no valid flows yet. Add one or use Local AI drafts.",
        )
    return ProjectReadiness(root, True, f"{len(flows)} valid flow(s) ready to review.", len(flows))


def review_command(root: Path) -> list[str]:
    """The exact command sent to Terminal by a configured Finder launch."""
    return [sys.executable, "-m", "flow_review.cli", "run", str(root), "--review"]


def launch_review_in_terminal(root: Path) -> None:
    """Open a visible Terminal run, with a subprocess fallback for non-macOS.

    A terminal is intentional: Xcode build progress, errors, and artifact
    paths stay visible after Finder has handed off the Quick Action.
    """
    command = review_command(root)
    shell_command = "cd " + shlex.quote(str(root)) + " && " + shlex.join(command)
    if sys.platform == "darwin":
        script = 'tell application "Terminal"\nactivate\ndo script ' + json.dumps(shell_command) + "\nend tell"
        try:
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return
        except (OSError, subprocess.CalledProcessError):
            # A managed Mac can deny Apple Events. The command still runs and
            # writes ordinary artifacts in that case, even without a terminal.
            pass
    try:
        subprocess.Popen(command, cwd=str(root), start_new_session=True)
    except OSError as exc:
        raise DesktopError(f"Could not start Flow Review: {exc}") from exc
