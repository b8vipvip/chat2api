from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v147_network_recovery_contract_matches_shipped_runtime() -> None:
    contract = text("chrome_extension/content_runtime_contract_v71.js")
    recovery = text("chrome_extension/content_network_stream_recovery_v55.js")
    assert "version: 57" in recovery
    assert "network_stream_recovery_v55: Number(networkRecovery?.version || 0) >= 55" in contract
    assert "network_response_recovery: Number(networkRecovery?.version || 0) >= 55" in contract
