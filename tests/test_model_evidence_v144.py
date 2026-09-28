from __future__ import annotations

import json
from pathlib import Path

from app.model_catalog import model_transport_id, normalize_model_id, normalize_reasoning_level, prioritize_models
from app.model_evidence import extract_request_evidence, extract_response_evidence


ROOT = Path(__file__).resolve().parents[1]


def test_model_catalog_matches_modelpro_aliases_and_priority() -> None:
    assert normalize_model_id("gpt-5-6-thinking") == "gpt-5.6-sol"
    assert normalize_model_id("gpt-6-astra-wm") == "gpt-6-astra"
    assert model_transport_id("gpt-6-astra") == "gpt-6-astra-wm"
    assert normalize_reasoning_level("xhigh") == "extra-high"
    assert prioritize_models(["gpt-5.6-sol", "gpt-6-sol", "gpt-6-astra", "gpt-6-luna"]) == [
        "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol"
    ]


def test_request_evidence_only_trusts_root_model() -> None:
    body = json.dumps({"model": "gpt-6-astra-wm", "metadata": {"model": "gpt-5.6-sol-wm"}})
    evidence = extract_request_evidence(body)
    assert evidence.request_model == "gpt-6-astra"
    assert evidence.model_conflict is False


def test_response_served_model_beats_default_profile_metadata() -> None:
    body = "data: " + json.dumps({
        "message": {"metadata": {"model_slug": "gpt-5.6-sol-wm"}},
        "resolved_model_slug": "gpt-6-astra-wm",
        "default_model_slug": "gpt-5.6-sol-wm",
    }) + "\n\ndata: [DONE]\n\n"
    evidence = extract_response_evidence(body, mime_type="text/event-stream")
    assert evidence.served_model == "gpt-6-astra"
    assert evidence.default_model == "gpt-5.6-sol"
    assert evidence.model_conflict is False


def test_response_equal_authority_conflict_fails_closed() -> None:
    body = "data: " + json.dumps({
        "served_model_slug": "gpt-6-astra-wm",
        "resolved_model_slug": "gpt-5.6-sol-wm",
    }) + "\n\n"
    evidence = extract_response_evidence(body, mime_type="text/event-stream")
    assert evidence.served_model is None
    assert evidence.model_conflict is True


def test_redesigned_hidden_work_default_profile_remains_distinct_evidence() -> None:
    body = "data: " + json.dumps({"default_model": "gpt-6-astra-wm"}) + "\n\n"
    evidence = extract_response_evidence(body, mime_type="text/event-stream")
    assert evidence.served_model is None
    assert evidence.default_model == "gpt-6-astra"


def test_v144_is_final_server_and_worker_boundary() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert entry.rstrip().endswith("install_model_observability_v144_patch(app)")

    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    main_scripts = manifest["content_scripts"][0]["js"]
    isolated_scripts = manifest["content_scripts"][1]["js"]
    assert main_scripts.index("model_evidence_main_v144.js") > main_scripts.index("network_stream_main_v55.js")
    assert "content_model_evidence_v144.js" in isolated_scripts
