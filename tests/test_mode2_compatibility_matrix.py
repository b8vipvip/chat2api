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
    assert len(records) >= 8
    assert all(r["status"] in {"compatible_subset", "extension_only"} for r in records)
    assert next(r for r in records if r["path"] == "/v1/files" and r["method"] == "POST")["status"] == "extension_only"
    capabilities = {row["id"]: row["status"] for row in data["capabilities"]}
    assert capabilities["chat.usage"] == "estimated"
    assert capabilities["chat.model_provenance"] == "requested_only"
    assert capabilities["files.multipart"] == "unsupported"
    assert capabilities["chat.tool_calls"] == "not_guaranteed"
    assert capabilities["chat.structured_outputs"] == "not_guaranteed"


def test_mode2_matrix_routes_are_registered_in_real_production_entry(tmp_path: Path) -> None:
    # Compose the entire production patch chain in a fresh process; the default
    # module import creates filesystem state and must not pollute other tests.
    script = r'''
import json
from app.entry import app
entries = []
for route in app.routes:
    for method in getattr(route, "methods", ()) or ():
        if method in {"POST", "GET", "DELETE", "PATCH", "PUT"}:
            entries.append((method, getattr(route, "path", ""), route.endpoint.__module__))
print(json.dumps(entries, separators=(",", ":")))
'''
    env = dict(os.environ)
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated")
    env["CHAT2API_API_KEY"] = "mode2-matrix-admin-test"
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        check=False, capture_output=True, text=True, timeout=75,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    routes = json.loads(result.stdout.strip().splitlines()[-1])
    matrix = _matrix()
    for spec in matrix["endpoints"]:
        matches = [
            item for item in routes
            if item[0] == spec["method"] and item[1] == spec["path"]
        ]
        assert len(matches) == 1, (spec["method"], spec["path"], matches)
        assert matches[0][2] == spec["owner"], matches


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
