from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v147_network_recovery_family_accepts_current_v57_implementation() -> None:
    recovery = text("chrome_extension/content_network_stream_recovery_v55.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")

    assert "version: 57" in recovery
    assert "network_stream_recovery_v55: Number(networkRecovery?.version || 0) >= 55" in contract
    assert "network_response_recovery: Number(networkRecovery?.version || 0) >= 55" in contract
    assert "network_stream_recovery_v55: Number(networkRecovery?.version || 0) === 55" not in contract


def test_v147_preflight_no_longer_permanently_rejects_current_network_module() -> None:
    preflight = text("chrome_extension/background_runtime_preflight_v48.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")

    assert "result?.modules?.network_stream_recovery_v55" in preflight
    assert "missing modules" in preflight
    assert "implementation reports version 57" in contract
