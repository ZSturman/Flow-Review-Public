"""Simulator screen recording via `xcrun simctl io <udid> recordVideo`.

The recording process is started just before a flow's test invocation and
stopped immediately after, so recordings capture the flow itself rather than
build time or idle simulator time.
"""
from __future__ import annotations

import signal
import subprocess
import time
from pathlib import Path


class Recorder:
    def __init__(self, udid: str, output_path: Path):
        self.udid = udid
        self.output_path = output_path
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_path.exists():
            self.output_path.unlink()
        self._proc = subprocess.Popen(
            [
                "xcrun", "simctl", "io", self.udid, "recordVideo",
                "--codec=h264", "--force", str(self.output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        # recordVideo takes a beat to actually start capturing; give it a
        # moment so we don't clip the first second of the flow.
        time.sleep(0.6)

    def stop(self) -> bool:
        """Stops recording. Returns True if a non-empty video file was produced."""
        if self._proc is None:
            return self.output_path.exists() and self.output_path.stat().st_size > 0
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        return self.output_path.exists() and self.output_path.stat().st_size > 0

    def __enter__(self) -> "Recorder":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
