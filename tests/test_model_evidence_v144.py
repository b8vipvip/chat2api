from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path

from fastapi import FastAPI

from app.model_catalog import model_transport_id, normalize_model_id, normalize_reasoning_level, prioritize_models
from app.model_evidence import extract_request_evidence, extract_response_evidence
from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


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



def test_gptwork_profile_defaults_never_pollute_served_model_conflict() -> None:
    # GPTWork response-evidence policy: only explicit served/resolved/used
    # fields are authoritative; conflicting default profiles are diagnostic.
    body = json.dumps({
        "message": {"metadata": {"default_model_slug": "gpt-5.6-sol-wm"}},
        "profile": {"default_model_slug": "gpt-6-astra-wm"},
    })
    evidence = extract_response_evidence(body)
    assert evidence.served_model is None
    assert evidence.model_conflict is False
    assert evidence.default_model is None
    assert evidence.default_model_conflict is True

    with_served = json.dumps({
        "served_model_slug": "gpt-6-astra-wm",
        "message": {"metadata": {"default_model_slug": "gpt-5.6-sol-wm"}},
        "profile": {"default_model_slug": "gpt-6-astra-wm"},
    })
    evidence = extract_response_evidence(with_served)
    assert evidence.served_model == "gpt-6-astra"
    assert evidence.model_conflict is False
    assert evidence.default_model_conflict is True


def test_gptwork_default_profile_mismatch_is_not_a_fake_fallback() -> None:
    from app.model_observability_v144_patch import _trace

    authority = {"requested_model": "gpt-6-astra", "routed_model": "gpt-6-astra"}
    evidence = {
        "served_model": "gpt-6-astra",
        "default_model": "gpt-5.6-sol",
        "default_model_conflict": False,
        "evidence_source": "network_response_metadata",
    }
    trace = _trace(authority, evidence)
    assert trace["model_verified"] is True
    assert trace["model_default_profile_mismatch"] is True
    assert trace["model_default_profile_conflict"] is False
    assert trace["model_fallback_reason"] is None

    # A profile default by itself can never prove an upstream model fallback.
    trace = _trace(authority, {"default_model": "gpt-5.6-sol", "default_model_conflict": True})
    assert trace["model_verified"] is False
    assert trace["served_model"] is None
    assert trace["model_verification_level"] == "profile-default"
    assert trace["model_conflict"] is False
    assert trace["model_default_profile_conflict"] is True
    assert trace["model_fallback_reason"] is None


def test_free_default_network_request_is_not_misrepresented_as_concrete_model():
    from app.model_observability_v144_patch import _trace
    authority = {"requested_model": "gpt-5.6-sol", "routed_model": "gpt-5.6-sol",
                 "default_page_route": True, "strict": False, "transport_model": None}
    trace = _trace(authority, {"request_model": "auto", "default_model": "gpt-5.6-sol"})
    assert trace["default_page_route"] is True
    assert trace["model_authority_violation"] is False
    assert trace["model_verified"] is False
    assert trace["transport_model"] is None
    assert trace["model_verification_level"] == "page-default-unverified"
    served = _trace(authority, {"served_model": "gpt-5.6-sol"})
    assert served["model_verified"] is True

    source = (ROOT / "chrome_extension" / "model_evidence_main_v144.js").read_text(encoding="utf-8")
    assert "authority?.default_page_route === true" in source
    assert "request_rewritten:false" in source


def test_v144_is_final_server_and_worker_boundary() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    v144 = entry.rindex("install_model_observability_v144_patch(app)")
    request_history = entry.rindex("install_request_history_v94_patch(app)")
    assert v144 < request_history
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")
    for authority in (
        "install_model_capability_routing_patch(app)",
        "install_generation_backend_routing_patch(app)",
        "install_responses_model_routing_v108_patch(app)",
        "install_request_id_namespace_v136_patch(app)",
        "install_worker_limits_clipboard_v121_patch(app)",
    ):
        assert entry.rindex(authority) < v144

    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    main_scripts = manifest["content_scripts"][0]["js"]
    isolated_scripts = manifest["content_scripts"][1]["js"]
    assert main_scripts.index("model_evidence_main_v144.js") > main_scripts.index("network_stream_main_v55.js")
    assert "content_model_evidence_v144.js" in isolated_scripts

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (ROOT / "chrome_extension" / "content_runtime_contract_v71.js").read_text(encoding="utf-8")
    assert "model_evidence_main_v144.js" in bootstrap
    assert "content_model_evidence_v144.js" in bootstrap
    assert "model_evidence_main_v144.js" in preflight
    assert "content_model_evidence_v144.js" in preflight
    assert "model_evidence_v144" in contract
    assert "model_evidence_main_v144" in contract


def test_v144_formal_release_contract_is_v02297() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.245"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.22.245"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["server"]["runtime_aligned"] is True
    assert "model-evidence-v144" in payload["server"]["feature_revision"]
    assert "final-model-authority-v144" in payload["server"]["feature_revision"]
    assert "model-evidence-v144" in payload["chrome_bridge"]["build_revision"]
    assert payload["features"]["model_evidence_v144"] is True
    assert payload["features"]["final_model_authority_v144"] is True
    assert payload["features"]["worker_redesigned_chatgpt_model_evidence_v144"] is True

def test_gptwork_response_evidence_bridge_matches_server_truth() -> None:
    if shutil.which("node") is None:
        return
    run = subprocess.run(
        ["node", str(ROOT / "tests" / "gptwork_response_evidence_bridge.mjs")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr

def test_served_mismatch_is_explicit_model_conflict_not_silent_fallback() -> None:
    from app.model_observability_v144_patch import _trace
    authority = {"requested_model": "gpt-5.6-sol", "routed_model": "gpt-5.6-sol"}
    evidence = {"request_model": "gpt-5.6-sol", "served_model": "gpt-6",
                "model_conflict": False, "evidence_source": "network_response_metadata"}
    trace = _trace(authority, evidence)
    assert trace["model_conflict"] is True
    assert trace["model_verified"] is False
    assert trace["model_fallback_reason"] == "upstream_served_model_mismatch"
    assert trace["model_authority_violation"] is False
    # A page default alone cannot invent a served-model conflict.
    unknown = _trace(authority, {"default_model": "gpt-6"})
    assert unknown["model_conflict"] is False
