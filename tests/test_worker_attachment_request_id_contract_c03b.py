from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{6,120}")


def test_worker_attachment_request_id_grammar_matches_responses_server_contract() -> None:
    responses = (ROOT / "app/responses_v108_patch.py").read_text(encoding="utf-8")
    background = (ROOT / "chrome_extension/background.js").read_text(encoding="utf-8")
    multimodal = (ROOT / "chrome_extension/content_multimodal.js").read_text(encoding="utf-8")
    multimodal_v78 = (ROOT / "chrome_extension/content_multimodal_v78.js").read_text(encoding="utf-8")

    # Responses accepts any bounded safe ID, not only the server-generated
    # req_/resp_/imgreq_ prefixes. Every Worker hop must accept that same set.
    assert 're.fullmatch(r"[A-Za-z0-9_-]{6,120}", raw_id)' in responses
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(request))" in background
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(requestId))" in multimodal
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(scopedRequestId))" in multimodal

    # The compatibility content-script path already uses the same alphabet
    # and bound; keep it aligned as the Worker manifest continues to load it.
    assert "if (!/^[a-zA-Z0-9_-]{6,120}$/.test(requestId))" in multimodal_v78

    valid = (
        "custom-client-123456",
        "req_abcdef123456",
        "resp_req_abcdef123456",
        "imgreq_abcdef123456",
    )
    invalid = ("", "short", "bad/request", "bad.request", "x" * 121)
    assert all(SAFE_REQUEST_ID.fullmatch(value) for value in valid)
    assert all(not SAFE_REQUEST_ID.fullmatch(value) for value in invalid)


def test_worker_request_prepares_request_scoped_attachments_before_prompt_submission() -> None:
    request_v6 = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    run_request = request_v6.split("async function runRequest(message)", 1)[1].split(
        "async function cancelRequest", 1
    )[0]

    assert "globalThis.__CHAT2API_MULTIMODAL_V4__" in run_request
    assert "await multimodal.attach(attachmentSpecs" in run_request
    assert "request_id: active.requestId" in run_request
    assert "prepared?.attachment_verified !== true" in run_request

    # The owner-scoped file download and ChatGPT composer upload must finish
    # before the request prompt is written or submitted.
    prepare = run_request.index("await multimodal.attach(attachmentSpecs")
    write_prompt = run_request.index("await writePrompt(active, prompt)")
    submit = run_request.index("await submitAndConfirm(active)")
    assert prepare < write_prompt < submit
