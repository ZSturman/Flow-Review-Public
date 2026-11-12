"""Install the Finder Quick Action that launches the Flow Review macOS app."""
from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from . import paths


class FinderIntegrationError(RuntimeError):
    pass


QUICK_ACTION_NAME = "Flow Review.workflow"
QUICK_ACTION_MENU_NAME = "Flow Review"


def install_cli_shim(python_executable: str, home: Path | None = None) -> Path:
    """Write a fixed-path launcher the macOS app always invokes.

    Resolved once, at install time, the same way the previous Terminal
    hand-off resolved ``sys.executable`` -- this just gives the Swift app a
    single stable path instead of needing to rediscover the environment.
    """
    destination = paths.cli_shim_path(home)
    destination.parent.mkdir(parents=True, exist_ok=True)
    script = f'#!/bin/sh\nexec {shlex.quote(python_executable)} -m flow_review.cli "$@"\n'
    destination.write_text(script)
    destination.chmod(0o755)
    return destination


def services_directory(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Services"


def quick_action_path(home: Path | None = None) -> Path:
    return services_directory(home) / QUICK_ACTION_NAME


def _workflow_document(python_executable: str, app_path: Optional[str] = None) -> dict:
    """Return Automator's plist format for a folder-only Run Shell Script action.

    The action receives Finder's folder selection as POSIX paths. When the
    Flow Review app is installed, each selection launches its own instance
    of it (`open -n`) so analysis and any setup happen in that single
    consolidated window instead of a chain of dialogs. Without a built app
    -- e.g. this checkout has no Xcode/xcodegen toolchain -- it falls back
    to the previous headless hand-off, which still runs a ready project and
    otherwise prints what is missing, deliberately without opening dialogs.
    """
    if app_path:
        command = (
            'for selected_folder in "$@"; do\n'
            f'  open -n -a {shlex.quote(app_path)} --args "$selected_folder"\n'
            "done\n"
        )
    else:
        command = (
            'for selected_folder in "$@"; do\n'
            f'  {python_executable!r} -m flow_review.cli desktop "$selected_folder"\n'
            "done\n"
        )
    action = {
        # This is Automator's current Run Shell Script action contract.  The
        # verbose fields matter: Finder ignores workflow plists with only the
        # older minimal action metadata even when they pass `plutil`.
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "CheckedForUserDefaultShell": True,
            "COMMAND_STRING": command,
            "inputMethod": 1,  # Pass input as arguments rather than stdin.
            "shell": "/bin/zsh",
            "source": "",
        },
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {
            "CheckedForUserDefaultShell": {}, "COMMAND_STRING": {}, "inputMethod": {}, "shell": {}, "source": {},
        },
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "arguments": {
            "0": {"default value": 0, "name": "inputMethod", "required": "0", "type": "0", "uuid": "0"},
            "1": {"default value": False, "name": "CheckedForUserDefaultShell", "required": "0", "type": "0", "uuid": "1"},
            "2": {"default value": "", "name": "source", "required": "0", "type": "0", "uuid": "2"},
            "3": {"default value": "", "name": "COMMAND_STRING", "required": "0", "type": "0", "uuid": "3"},
            "4": {"default value": "/bin/sh", "name": "shell", "required": "0", "type": "0", "uuid": "4"},
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "CFBundleVersion": "2.0.3",
        "Class Name": "RunShellScriptAction",
        "conversionLabel": 0,
        "InputUUID": str(uuid.uuid4()).upper(),
        "isViewVisible": 1,
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "location": "309.000000:305.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
        "OutputUUID": str(uuid.uuid4()).upper(),
        "UnlocalizedApplications": ["Automator"],
        "UUID": str(uuid.uuid4()).upper(),
    }
    return {
        "AMApplicationBuild": "534",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        # Automator writes this as an integer.  Keep the exact canonical
        # representation because Finder's Quick Actions broker is stricter
        # than either plutil or the `automator` command-line runner.
        "actions": [{"action": action, "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleID": "com.apple.finder",
            "applicationBundleIDsByPath": {"/System/Library/CoreServices/Finder.app": "com.apple.finder"},
            "applicationPath": "/System/Library/CoreServices/Finder.app",
            "applicationPaths": ["/System/Library/CoreServices/Finder.app"],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject.folder",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": False,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject.folder",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def _workflow_info() -> dict:
    """Return the bundle service declaration Finder requires to discover it.

    ``document.wflow`` describes the Automator graph, while this separate
    bundle plist registers that graph as a Finder service.  A syntactically
    valid graph without this declaration can be run with ``automator`` but
    remains invisible in Finder and Keyboard Shortcuts.
    """
    return {
        "NSServices": [{
            "NSBackgroundColorName": "background",
            # Deliberately omit NSIconName. Finder places icon-bearing
            # Automator workflows inside the Quick Actions submenu, which on
            # this macOS release shows only the first four custom actions.
            # A plain Service appears directly in the folder context menu.
            "NSMenuItem": {"default": QUICK_ACTION_MENU_NAME},
            "NSMessage": "runWorkflowAsService",
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
            "NSSendFileTypes": ["public.folder"],
        }],
    }


def install_quick_action(force: bool = False, home: Path | None = None, app_path: Optional[str] = None) -> Path:
    """Install a complete Finder workflow bundle and refresh its service broker."""
    if sys.platform != "darwin":
        raise FinderIntegrationError("Finder Quick Actions can only be installed on macOS.")
    destination = quick_action_path(home)
    if destination.exists():
        if not force:
            raise FinderIntegrationError(
                f"A Quick Action already exists at {destination}. Use --force to replace this Flow Review action."
            )
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Build outside ~/Library/Services, then copy the completed bundle.  The
    # pbs service broker can cache a workflow seen while its two plists are
    # only partially written; this mirrors Automator's bundle-install shape.
    staging_dir = Path(tempfile.mkdtemp(prefix=".flow-review-", dir=destination.parent))
    try:
        staged_bundle = staging_dir / QUICK_ACTION_NAME
        contents = staged_bundle / "Contents"
        contents.mkdir(parents=True)
        with (contents / "document.wflow").open("wb") as stream:
            plistlib.dump(_workflow_document(sys.executable, app_path), stream, sort_keys=False)
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump(_workflow_info(), stream, sort_keys=False)
        try:
            if Path("/usr/bin/ditto").is_file():
                subprocess.run(["/usr/bin/ditto", str(staged_bundle), str(destination)], check=True)
            else:  # Lets the isolated installer test run on non-macOS hosts.
                shutil.copytree(staged_bundle, destination)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise FinderIntegrationError(f"Could not install Finder Quick Action: {exc}") from exc
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    # pbs is the per-user service broker. Restart it only for the real user
    # installation; test installs deliberately pass a temporary home.
    if home is None:
        try:
            subprocess.run(["/usr/bin/killall", "pbs"], check=False, capture_output=True, text=True)
        except OSError:
            pass
    return destination


def uninstall_quick_action(home: Path | None = None) -> bool:
    """Remove only the named Quick Action and report whether it existed."""
    destination = quick_action_path(home)
    if not destination.exists():
        return False
    shutil.rmtree(destination)
    return True


def validate_quick_action(path: Path) -> None:
    """Parse a generated workflow; used in tests and after installation."""
    document = path / "Contents" / "document.wflow"
    try:
        with document.open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise FinderIntegrationError(f"Quick Action plist is invalid: {exc}") from exc
    if payload.get("workflowMetaData", {}).get("serviceInputTypeIdentifier") != "com.apple.Automator.fileSystemObject.folder":
        raise FinderIntegrationError("Quick Action was not generated for Finder folders.")
    try:
        with (path / "Contents" / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise FinderIntegrationError(f"Quick Action service declaration is invalid: {exc}") from exc
    service = next(iter(info.get("NSServices", [])), {})
    if service.get("NSSendFileTypes") != ["public.folder"] or service.get("NSMenuItem", {}).get("default") != QUICK_ACTION_MENU_NAME:
        raise FinderIntegrationError("Quick Action is not registered as the Flow Review Finder folder service.")
