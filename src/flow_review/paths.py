"""Central, machine-wide locations used by Flow Review.

Flow Review keeps two kinds of state:

* Project-local artifacts (recordings, screenshots, generated Xcode files) live
  inside the target project's own ``.flow-review/`` directory, which is
  git-ignored.
* A small cross-project index (registered projects + review state) lives in
  the user's home directory so the review UI can show every project at once
  without having to rescan the filesystem.
"""
from __future__ import annotations

import os
from pathlib import Path

HOME_DIR = Path(os.environ.get("FLOW_REVIEW_HOME", str(Path.home() / ".flow-review")))
DB_PATH = HOME_DIR / "registry.db"
LOG_DIR = HOME_DIR / "logs"


def ensure_home() -> Path:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return HOME_DIR


# Project-local layout, relative to a project root.
CONFIG_DIR_NAME = "flow-review"
CONFIG_FILE_NAME = "config.yaml"
FLOWS_DIR_NAME = "flows"
ARTIFACT_DIR_NAME = ".flow-review"
RUNS_DIR_NAME = "runs"
GENERATED_DIR_NAME = "generated"  # inside .flow-review, staging for xcode-visible files


def config_dir(project_root: Path) -> Path:
    return project_root / CONFIG_DIR_NAME


def config_file(project_root: Path) -> Path:
    return config_dir(project_root) / CONFIG_FILE_NAME


def flows_dir(project_root: Path) -> Path:
    return config_dir(project_root) / FLOWS_DIR_NAME


def artifact_dir(project_root: Path) -> Path:
    return project_root / ARTIFACT_DIR_NAME


def runs_dir(project_root: Path) -> Path:
    return artifact_dir(project_root) / RUNS_DIR_NAME


def cli_shim_path(home: Path | None = None) -> Path:
    """A fixed, version-independent path the macOS app always invokes.

    Written by `flow-review install-finder` so the app never has to guess
    which Python environment owns `flow-review` -- it just runs this file.
    """
    return (home or Path.home()) / ".flow-review" / "bin" / "flow-review"
