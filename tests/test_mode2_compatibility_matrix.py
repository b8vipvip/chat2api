from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.models import ChatCompletionRequest
from app.prompting import build_prompt


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "architecture" / "mode2-api-compatibility-matrix.json"


def _matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def test_mode2_matrix_is_machine_readable_and_non_overclaiming() -> None:
    data = _matrix()
    assert data["schema_version"] == 1
    assert data["ingress"]["default"] == "mode2"
    assert {"chat", "mode1"} <= set(data["ingress"]["forbidden_on_v1"])
    records = data["endpoints"]
    keys = [(r["method"], r["path"]) for r in records]
    assert len(keys) == len(set(keys))
    assert len(records) == 19
    assert sum(row["protocol"] == "http" for row in records) == 18
    assert sum(row["protocol"] == "websocket" for row in records) == 1
    assert all(r["status"] in {"compatible_subset", "extension_only"} for r in records)
    assert next(r for r in records if r["path"] == "/v1/files" and r["method"] == "POST")["status"] == "compatible_subset"
    capabilities = {row["id"]: row["status"] for row in data["capabilities"]}
    assert capabilities["chat.usage"] == "estimated"
    assert capabilities["chat.model_provenance"] == "requested_only"
    assert capabilities["files.multipart"] == "supported_subset"
    assert capabilities["files.content"] == "supported_subset"
    assert capabilities["files.list"] == "unsupported"
    assert capabilities["audio.transcriptions"] == "unsupported"
    assert capabilities["chat.tool_calls"] == "not_guaranteed"
    assert capabilities["chat.structured_outputs"] == "not_guaranteed"
    assert capabilities["model.aliases"] == "supported_subset"
    assert capabilities["responses.retrieve"] == "supported_subset"

    parameters = data["request_parameters"]
    chat = {row["name"]: row["status"] for row in parameters["POST /v1/chat/completions"]}
    assert chat["model"] == "supported_with_aliases"
    assert chat["reasoning_effort"] == "chat2api_extension"
    assert chat["max_completion_tokens"] == "accepted_but_ignored"
    assert chat["temperature"] == "accepted_but_ignored"
    assert chat["tools"] == "accepted_but_ignored"

    responses = {row["name"]: row["status"] for row in parameters["POST /v1/responses"]}
    assert responses["tools"] == "supported_subset"
    assert responses["store"] == "supported_subset"
    assert responses["max_output_tokens"] == "echo_only"
    assert responses["previous_response_id"] == "echo_only"


def test_mode2_matrix_covers_every_production_v1_route_and_openapi_operation(tmp_path: Path) -> None:
    # Compose the entire production patch chain in a fresh process; the default
    # module import creates filesystem state and must not pollute other tests.
    script = r'''
import json
from app.entry import app
entries = []
for route in app.routes:
    path = str(getattr(route, "path", "") or "")
    if not path.startswith("/v1/"):
        continue
    methods = getattr(route, "methods", None) or ()
    if methods:
        for method in methods:
            entries.append((method, path, route.endpoint.__module__, "http"))
    elif "WebSocket" in type(route).__name__:
        entries.append(("WEBSOCKET", path, route.endpoint.__module__, "websocket"))
openapi = app.openapi()
schema = []
for path, operations in openapi.get("paths", {}).items():
    if path.startswith("/v1/"):
        for method in operations:
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                schema.append((method.upper(), path))
file_body = openapi["paths"]["/v1/files"]["post"].get("requestBody", {})
file_upload_media_types = sorted((file_body.get("content") or {}).keys())
print(json.dumps({
    "routes": entries,
    "openapi": schema,
    "file_upload_media_types": file_upload_media_types,
}, separators=(",", ":")))
'''
    env = dict(os.environ)
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated")
    env["CHAT2API_API_KEY"] = "mode2-matrix-admin-test"
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        check=False, capture_output=True, text=True, timeout=75,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    runtime = json.loads(result.stdout.strip().splitlines()[-1])
    routes = runtime["routes"]
    schema = set(tuple(row) for row in runtime["openapi"])
    assert {"multipart/form-data", "application/json"} <= set(runtime["file_upload_media_types"])
    matrix = _matrix()
    specs = matrix["endpoints"]
    expected = {(row["method"], row["path"], row["protocol"]) for row in specs}
    actual = {(method, path, protocol) for method, path, _, protocol in routes}
    assert actual == expected, {
        "missing_from_matrix": sorted(actual - expected),
        "stale_matrix_entries": sorted(expected - actual),
    }
    for spec in specs:
        matches = [
            item for item in routes
            if item[0] == spec["method"] and item[1] == spec["path"]
        ]
        assert len(matches) == 1, (spec["method"], spec["path"], matches)
        assert matches[0][2] == spec["owner"], matches
        if spec["protocol"] == "http":
            assert spec["openapi"] is True
            assert (spec["method"], spec["path"]) in schema
        else:
            assert spec["protocol"] == "websocket"
            assert spec["openapi"] is False


def test_standard_sdk_message_shape_keeps_roles_and_transcript_order() -> None:
    body = ChatCompletionRequest.model_validate({
        "model": " GPT-5.6-Sol ",
        "messages": [
            {"role": "system", "content": "Prefer short answers."},
            {"role": "developer", "content": "Use Chinese."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First reply"},
            {"role": "user", "content": [{"type": "text", "text": "Second question"}]},
        ],
        "stream": True,
    })
    assert body.model == "gpt-5.6-sol"
    assert body.prompt_mode == "full"
    assert body.stream
    prompt = build_prompt(body.messages, body.prompt_mode)
    anchors = [
        "[System]\nPrefer short answers.",
        "[Developer]\nUse Chinese.",
        "[User]\nFirst question",
        "[Assistant]\nFirst reply",
        "[User]\nSecond question",
    ]
    assert all(fragment in prompt for fragment in anchors)
    assert [prompt.index(fragment) for fragment in anchors] == sorted(prompt.index(fragment) for fragment in anchors)
