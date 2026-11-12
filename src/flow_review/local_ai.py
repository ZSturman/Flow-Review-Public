"""Opt-in discovery of candidate Flow Review paths using a local model.

LM Studio and many notebook-based local model runners expose the same small
OpenAI-compatible HTTP surface.  Keeping the protocol here lets the rest of
the app offer one careful, review-first workflow without depending on either
vendor's Python package or sending source code to a hosted service.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


class LocalAIError(RuntimeError):
    """A local model endpoint could not be used safely or successfully."""


LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
NOTEBOOK_URL = "http://127.0.0.1:11434/v1"
# Local models commonly buffer a whole completion before returning any HTTP
# bytes.  Give a loaded, mid-sized model enough time to reason and emit YAML.
DEFAULT_TIMEOUT_S = 120
MAX_CONTEXT_CHARS = 90_000
MAX_FILE_CHARS = 12_000

# This intentionally only includes UI/source formats.  In particular it does
# not collect .env files, keychains, build settings, generated dependency
# files, or arbitrary project assets.
SOURCE_SUFFIXES = {".swift", ".m", ".mm", ".h", ".storyboard", ".xib"}
SKIP_DIRECTORY_NAMES = {
    ".git", ".flow-review", ".build", "build", "DerivedData", "Pods",
    "Carthage", "node_modules", ".swiftpm",
}
IDENTIFIER_RE = re.compile(
    r"(?:accessibilityIdentifier\s*=|\.accessibilityIdentifier\s*\()\s*\"([^\"]+)\""
)

# LM Studio supports OpenAI-style structured output.  Keep the schema broad
# enough for the intentionally small Flow Review step vocabulary, then turn
# the model's JSON envelope into the YAML the developer reviews.  Other local
# servers can still use the plain-YAML path below.
FLOW_SUGGESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "flows": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "object", "additionalProperties": True},
                    },
                },
                "required": ["id", "name", "steps"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["flows"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class LocalAISettings:
    """Connection information for an OpenAI-compatible process on localhost."""

    provider: str
    base_url: str
    model: str

    def normalized_base_url(self) -> str:
        value = self.base_url.strip().rstrip("/")
        if not value:
            raise LocalAIError("Enter the local model server URL, including its /v1 path.")
        if not value.startswith(("http://", "https://")):
            raise LocalAIError("The local model server URL must start with http:// or https://.")
        host = urlsplit(value).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise LocalAIError(
                "Flow Review only connects to a local model server. Use localhost, 127.0.0.1, or ::1."
            )
        return value


def default_base_url(provider: str) -> str:
    """Return a useful default while still allowing any local notebook URL."""
    return NOTEBOOK_URL if provider == "local_notebook" else LM_STUDIO_URL


def _json_request(url: str, payload: Optional[dict], timeout_s: int) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {})},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # nosec B310 - URL is user-selected local server
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LocalAIError(f"Local model server returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise LocalAIError(
            f"Could not reach the local model server at {url}. Start it, then check the URL. ({exc.reason})"
        ) from exc
    except TimeoutError as exc:
        raise LocalAIError(f"The local model server did not respond within {timeout_s} seconds.") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalAIError("The local model server did not return JSON.") from exc


def list_models(base_url: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[str]:
    """List models from ``GET /models`` without generating or sending source."""
    settings = LocalAISettings(provider="local", base_url=base_url, model="unused")
    data = _json_request(settings.normalized_base_url() + "/models", None, timeout_s)
    models = [item.get("id") for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]
    if not models:
        raise LocalAIError("The local model server returned no models. Load a model and try again.")
    return sorted(str(model) for model in models)


def _source_files(project_root: Path) -> Iterable[Path]:
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(parent.name in SKIP_DIRECTORY_NAMES for parent in path.parents):
            continue
        yield path


def collect_ui_context(project_root: Path, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Collect a bounded, source-only view of the project's UI affordances.

    The first section names stable accessibility identifiers directly.  The
    second includes truncated relevant source files so a model can infer the
    surrounding user journey.  A caller can display the result before any
    network request if it wants an additional review step.
    """
    root = project_root.resolve()
    identifiers: set[str] = set()
    snippets: list[str] = []
    used = 0
    for path in sorted(_source_files(root)):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        identifiers.update(IDENTIFIER_RE.findall(text))
        remaining = max_chars - used
        if remaining <= 0:
            break
        clipped = text[:min(MAX_FILE_CHARS, remaining)]
        relative = path.relative_to(root)
        snippets.append(f"// FILE: {relative}\n{clipped}")
        used += len(clipped)

    identifier_lines = "\n".join(f"- {value}" for value in sorted(identifiers)) or "- none found"
    context = (
        "ACCESSIBILITY IDENTIFIERS FOUND IN SOURCE:\n"
        f"{identifier_lines}\n\n"
        "TRUNCATED UI SOURCE (may be incomplete):\n"
        + "\n\n".join(snippets)
    )
    return context[:max_chars]


def flow_suggestion_prompt(project_name: str, platform: str, ui_context: str, structured: bool = False) -> str:
    """Build a tightly scoped prompt that asks for drafts, not automation truth."""
    format_instruction = (
        "The response schema is an object with a `flows` list. In `steps`, use Flow Review step mappings "
        "such as `{\"launch\": {}}` and `{\"tap\": {\"accessibility_id\": \"addButton\"}}`. "
        "Do not add prose or fields outside the schema."
        if structured else
        "Return YAML only, beginning with `-` as the first character. Do not include explanation, markdown fences, or reasoning."
    )
    return f"""You are helping draft human-reviewed XCUITest flow specifications for the Apple app {project_name!r}.
Target platform: {platform}.

{format_instruction} Return 1-4 candidate flows. Every item must use the Flow Review vocabulary below and should be plausible from the supplied source only. Prefer found accessibility identifiers. Do not invent network calls, credentials, or screen behavior. If a needed locator is unclear, omit that flow rather than guessing. Every flow needs `id`, `name`, and non-empty `steps`; use `launch`, `tap`, `type`, `assert_exists`, `wait_for`, and `checkpoint` only when supported by source evidence. If a `type` step is justified, the literal fixture value `Flow Review Draft` is explicitly allowed.

These are drafts for a developer to review, never executable until explicitly saved into flow-review/flows.

SOURCE CONTEXT START
{ui_context}
SOURCE CONTEXT END
"""


def _extract_yaml(content: str) -> str:
    """Accept a model's fenced YAML response while rejecting unrelated prose."""
    match = re.search(r"```(?:yaml|yml)?\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL)
    return (match.group(1) if match else content).strip()


def suggest_flows(
    settings: LocalAISettings,
    project_name: str,
    platform: str,
    ui_context: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Ask the selected *local* model for YAML candidate flows.

    Callers must choose the server and model explicitly.  The returned YAML is
    intentionally not written anywhere; the GUI and CLI require a separate
    user action to save a draft.
    """
    if not settings.model.strip():
        raise LocalAIError("Choose a loaded local model before generating flow suggestions.")
    structured = settings.provider == "lm_studio"
    payload = {
        "model": settings.model.strip(),
        "messages": [
            {"role": "system", "content": "Return only conservative, review-first UI flow drafts."},
            {"role": "user", "content": flow_suggestion_prompt(project_name, platform, ui_context, structured)},
        ],
        "temperature": 0.2,
    }
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "flow_review_drafts", "schema": FLOW_SUGGESTIONS_SCHEMA},
        }
    data = _json_request(settings.normalized_base_url() + "/chat/completions", payload, timeout_s)
    try:
        message = data["choices"][0]["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalAIError("The local model response did not include a chat completion.") from exc
    if not isinstance(content, str) or not content.strip():
        if isinstance(message, dict) and message.get("reasoning_content"):
            raise LocalAIError(
                "The local model returned reasoning but no final YAML. Try again with a longer timeout or a non-reasoning model."
            )
        raise LocalAIError("The local model returned an empty flow suggestion.")
    if structured:
        try:
            response = json.loads(content)
            flows = response["flows"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LocalAIError("LM Studio returned a structured response that was not a flow list.") from exc
        if not isinstance(flows, list) or not flows:
            raise LocalAIError("LM Studio returned no candidate flows.")
        return yaml.safe_dump(flows, sort_keys=False, allow_unicode=True).strip()
    return _extract_yaml(content)
