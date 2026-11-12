"""Building and installing the Flow Review macOS app.

The app (``macapp/FlowReviewApp``) is a thin SwiftUI client of this
package's CLI: it never reimplements discovery, config, or the
build/test/record pipeline, it just shells out to ``flow-review`` the same
way a Terminal user would. This module's only job is to build that Xcode
project and put the result where the Finder Quick Action can find it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

APP_PRODUCT_NAME = "Flow Review.app"
_MACAPP_DIR = Path(__file__).resolve().parent.parent.parent / "macapp" / "FlowReviewApp"
_XCODEPROJ = _MACAPP_DIR / "FlowReviewApp.xcodeproj"
_SCHEME = "FlowReviewApp"


class MacAppError(RuntimeError):
    pass


def installed_app_path(home: Optional[Path] = None) -> Path:
    return (home or Path.home()) / "Applications" / APP_PRODUCT_NAME


def project_available() -> bool:
    return _XCODEPROJ.exists()


def build_app(configuration: str = "Release", derived_data: Optional[Path] = None) -> Path:
    """Build the SwiftUI app and return the path to the built .app bundle.

    Raises MacAppError with xcodebuild's own output on failure rather than
    swallowing it -- a build failure here should be exactly as debuggable as
    running the same xcodebuild command by hand.
    """
    if not project_available():
        raise MacAppError(
            f"The Flow Review macOS app project was not found at {_XCODEPROJ}. "
            "This checkout may be incomplete; the CLI and `flow-review desktop` "
            "fallback remain available."
        )
    dd = derived_data or (Path.home() / ".flow-review" / "macapp-build")
    try:
        result = subprocess.run(
            [
                "xcodebuild", "-project", str(_XCODEPROJ), "-scheme", _SCHEME,
                "-configuration", configuration, "-derivedDataPath", str(dd),
                "build",
            ],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MacAppError(f"Could not run xcodebuild: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stdout[-4000:] + "\n" + result.stderr[-2000:]).strip()
        raise MacAppError(f"Building the Flow Review app failed:\n{tail}")
    built = dd / "Build" / "Products" / configuration / APP_PRODUCT_NAME
    if not built.exists():
        raise MacAppError(f"xcodebuild reported success but {built} is missing.")
    return built


def install_app(configuration: str = "Release", home: Optional[Path] = None) -> Path:
    """Build the app and copy it to ~/Applications, replacing any prior copy."""
    built = build_app(configuration)
    destination = installed_app_path(home)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(built, destination)
    return destination
