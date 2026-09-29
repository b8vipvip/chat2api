from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v147_documents_v146_window_close_trigger_for_regression_visibility() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")
    recovery = text("chrome_extension/content_network_stream_recovery_v55.js")

    # v146 retires an idle window whenever runtime preflight throws. The v147
    # compatibility fix must therefore guarantee the shipped v57 network module
    # is not falsely classified as stale by the v55 compatibility-family key.
    assert "if (await closeWindow(row.window_id))" in pool
    assert "version: 57" in recovery
    assert "network_stream_recovery_v55: Number(networkRecovery?.version || 0) >= 55" in contract
