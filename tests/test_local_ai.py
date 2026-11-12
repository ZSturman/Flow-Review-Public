from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flow_review import local_ai


class _LocalModelHandler(BaseHTTPRequestHandler):
    received: dict = {}

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
        self.assertEqual(self.path, "/v1/models")
        self._send({"data": [{"id": "small-local"}, {"id": "larger-local"}]})

    def do_POST(self) -> None:  # noqa: N802 - HTTP handler API
        self.assertEqual(self.path, "/v1/chat/completions")
        length = int(self.headers["Content-Length"])
        type(self).received = json.loads(self.rfile.read(length))
        if "response_format" in type(self).received:
            content = json.dumps({
                "flows": [{"id": "add-item", "name": "Add item", "steps": [{"launch": {}}]}],
            })
        else:
            content = "```yaml\n- id: add-item\n  steps: [launch]\n```"
        self._send({"choices": [{"message": {"content": content}}]})

    # unittest assertions are not available on the handler itself.
    def assertEqual(self, left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class LocalAITests(unittest.TestCase):
    def setUp(self) -> None:
        _LocalModelHandler.received = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalModelHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_lists_models_and_requests_yaml_from_a_local_openai_compatible_server(self) -> None:
        self.assertEqual(local_ai.list_models(self.url), ["larger-local", "small-local"])
        draft = local_ai.suggest_flows(
            local_ai.LocalAISettings("lm_studio", self.url, "small-local"),
            "Example",
            "iOS",
            "ACCESSIBILITY IDENTIFIERS FOUND IN SOURCE:\n- addButton",
        )
        self.assertIn("id: add-item", draft)
        self.assertIn("launch: {}", draft)
        self.assertEqual(_LocalModelHandler.received["model"], "small-local")
        self.assertIn("addButton", _LocalModelHandler.received["messages"][1]["content"])
        self.assertEqual(_LocalModelHandler.received["response_format"]["type"], "json_schema")

    def test_context_is_source_only_and_excludes_secret_and_dependency_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "View.swift").write_text('Text("Add").accessibilityIdentifier("addButton")')
            (root / ".env").write_text("SECRET=must-not-leave")
            pods = root / "Pods"
            pods.mkdir()
            (pods / "Ignored.swift").write_text("dependency-private")

            context = local_ai.collect_ui_context(root)

        self.assertIn("addButton", context)
        self.assertNotIn("must-not-leave", context)
        self.assertNotIn("dependency-private", context)

    def test_requires_an_explicit_model(self) -> None:
        with self.assertRaisesRegex(local_ai.LocalAIError, "Choose a loaded local model"):
            local_ai.suggest_flows(
                local_ai.LocalAISettings("lm_studio", self.url, ""), "Example", "iOS", "source",
            )

    def test_rejects_non_local_endpoints_before_any_request(self) -> None:
        with self.assertRaisesRegex(local_ai.LocalAIError, "only connects to a local model server"):
            local_ai.list_models("https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
