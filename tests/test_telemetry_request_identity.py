"""Telemetry request identity is immutable across duplicate admissions."""
from __future__ import annotations

import asyncio

import pytest

from app.telemetry import DuplicateRequestIdentity, TelemetryStore


def test_duplicate_request_id_cannot_overwrite_an_active_request(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    original = {
        "request_id": "req_identity_guard",
        "response_id": "resp_original",
        "api_key_id": "key_original",
        "status": "queued",
        "requested_model": "gpt-5.6-sol",
        "response_text": "",
    }
    asyncio.run(telemetry.upsert(original))

    with pytest.raises(DuplicateRequestIdentity, match="another response"):
        asyncio.run(telemetry.upsert({
            "request_id": "req_identity_guard",
            "response_id": "resp_replay",
            "api_key_id": "key_replay",
            "status": "queued",
            "requested_model": "gpt-5.5",
            "response_text": "replacement",
        }))

    persisted = telemetry.get("req_identity_guard")
    assert persisted is not None
    assert persisted["response_id"] == "resp_original"
    assert persisted["api_key_id"] == "key_original"
    assert persisted["status"] == "queued"
    assert persisted["requested_model"] == "gpt-5.6-sol"


def test_duplicate_request_id_cannot_reopen_a_terminal_request(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    asyncio.run(telemetry.upsert({
        "request_id": "req_terminal_identity",
        "response_id": "resp_terminal",
        "status": "completed",
        "response_text": "original final response",
    }))

    with pytest.raises(DuplicateRequestIdentity):
        asyncio.run(telemetry.upsert({
            "request_id": "req_terminal_identity",
            "response_id": "resp_new",
            "status": "queued",
            "response_text": "new attempt",
        }))

    persisted = telemetry.get("req_terminal_identity")
    assert persisted is not None
    assert persisted["response_id"] == "resp_terminal"
    assert persisted["status"] == "completed"
    assert persisted["response_text"] == "original final response"


def test_same_response_identity_can_receive_normal_diagnostic_updates(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    asyncio.run(telemetry.upsert({
        "request_id": "req_same_response",
        "response_id": "resp_same",
        "status": "running",
        "diagnostics": {"stage": "initial"},
    }))
    updated = asyncio.run(telemetry.upsert({
        "request_id": "req_same_response",
        "response_id": "resp_same",
        "status": "running",
        "diagnostics": {"stage": "advanced"},
    }))
    assert updated["response_id"] == "resp_same"
    assert updated["diagnostics"]["stage"] == "advanced"
