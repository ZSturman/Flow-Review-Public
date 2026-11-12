from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd: list[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> ProcResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return ProcResult(proc.returncode, proc.stdout, proc.stderr)


_RUBY_HELPER = Path(__file__).parent / "templates" / "xcode_helper.rb"


class RubyHelperError(RuntimeError):
    def __init__(self, message: str, missing_gem: bool = False):
        super().__init__(message)
        self.missing_gem = missing_gem


def xcode_helper(command: str, args: dict[str, Any]) -> dict:
    """Invoke the bundled xcode_helper.rb, which wraps the `xcodeproj` gem.

    This is the only code path that mutates project.pbxproj / .xcscheme files.
    """
    payload = json.dumps({"command": command, "args": args})
    proc = subprocess.run(
        ["ruby", str(_RUBY_HELPER)],
        input=payload,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "").strip()
    if not out:
        raise RubyHelperError(f"xcode_helper.rb produced no output. stderr: {proc.stderr.strip()}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise RubyHelperError(f"xcode_helper.rb produced invalid JSON: {out[:500]}") from e
    if "error" in data:
        raise RubyHelperError(data["error"], missing_gem=data.get("missing_gem", False))
    return data
