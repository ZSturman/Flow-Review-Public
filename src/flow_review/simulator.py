"""Simulator selection and lifecycle via `xcrun simctl`.

Deliberately thin: simctl already does device listing, booting, resetting,
and video recording well. This module just picks a sensible destination and
gives callers structured data instead of raw text.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from . import procutil


class SimulatorError(RuntimeError):
    pass


@dataclass
class SimDevice:
    udid: str
    name: str
    state: str
    runtime: str  # e.g. "com.apple.CoreSimulator.SimRuntime.iOS-26-5"
    is_available: bool


def _simctl_json(*args: str) -> dict:
    result = procutil.run(["xcrun", "simctl", *args, "-j"], timeout=30)
    if not result.ok:
        # not all simctl subcommands support -j; caller should use raw for those
        raise SimulatorError(result.stderr.strip())
    return json.loads(result.stdout)


def list_devices(platform_filter: Optional[str] = None) -> list[SimDevice]:
    result = procutil.run(["xcrun", "simctl", "list", "devices", "available", "-j"], timeout=30)
    if not result.ok:
        raise SimulatorError(f"simctl list failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    devices: list[SimDevice] = []
    for runtime, entries in data.get("devices", {}).items():
        if platform_filter and platform_filter.lower() not in runtime.lower():
            continue
        for e in entries:
            if not e.get("isAvailable", True):
                continue
            devices.append(SimDevice(
                udid=e["udid"], name=e["name"], state=e["state"],
                runtime=runtime, is_available=e.get("isAvailable", True),
            ))
    return devices


def _platform_runtime_key(platform: str) -> str:
    p = platform.lower()
    if p in ("ios", "iphoneos"):
        return "ios"
    if p in ("ipados",):
        return "ios"  # iPad devices run on the iOS runtime family
    if p in ("macos", "osx"):
        return "macos"
    return p


def find_or_create_device(platform: str, name_hint: Optional[str], os_version: Optional[str]) -> SimDevice:
    """Find a booted/available simulator matching the request, preferring an
    already-booted one to avoid unnecessary boot churn.
    """
    plat_key = _platform_runtime_key(platform)
    if plat_key == "macos":
        raise SimulatorError(
            "macOS UI tests run against the local Mac, not a simulator. "
            "flow-review runs macOS flows directly via xcodebuild -destination 'platform=macOS'."
        )

    candidates = list_devices(platform_filter="ios")
    if os_version:
        candidates = [d for d in candidates if os_version.replace(".", "-") in d.runtime]
    if name_hint:
        named = [d for d in candidates if d.name == name_hint]
        if named:
            candidates = named

    if not candidates:
        raise SimulatorError(
            f"No available iOS simulator matches name='{name_hint}' os='{os_version}'.\n"
            f"Run `flow-review doctor` to see available simulators, or install one via Xcode > "
            f"Settings > Platforms, or `xcrun simctl create`."
        )

    booted = [d for d in candidates if d.state == "Booted"]
    return booted[0] if booted else candidates[0]


def boot(udid: str, timeout_s: int = 120) -> None:
    result = procutil.run(["xcrun", "simctl", "boot", udid], timeout=30)
    if not result.ok and "current state: Booted" not in (result.stderr or ""):
        raise SimulatorError(f"Failed to boot simulator {udid}: {result.stderr.strip()}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        devices = list_devices()
        dev = next((d for d in devices if d.udid == udid), None)
        if dev and dev.state == "Booted":
            return
        time.sleep(1)
    raise SimulatorError(f"Timed out waiting for simulator {udid} to boot")


def ensure_booted(device: SimDevice, timeout_s: int = 120) -> SimDevice:
    if device.state == "Booted":
        return device
    boot(device.udid, timeout_s=timeout_s)
    device.state = "Booted"
    return device


def erase(udid: str) -> None:
    result = procutil.run(["xcrun", "simctl", "erase", udid], timeout=60)
    if not result.ok:
        raise SimulatorError(f"Failed to erase simulator {udid}: {result.stderr.strip()}")


def app_container(udid: str, bundle_id: str, container: str = "data") -> Optional[str]:
    result = procutil.run(["xcrun", "simctl", "get_app_container", udid, bundle_id, container], timeout=15)
    if not result.ok:
        return None
    return result.stdout.strip()
