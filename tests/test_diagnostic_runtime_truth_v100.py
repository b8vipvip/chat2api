from __future__ import annotations

import re
from pathlib import Path

from app.linux_worker_diagnostics_patch import _pairing_evidence
from app.runtime_contract import SERVER_RUNTIME_VERSION


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v8_diagnostics_use_canonical_runtime_contract_not_historical_patch_version() -> None:
    source = read("app/v8_patch.py")
    runtime = read("app/runtime_contract.py")

    assert "from .runtime_contract import SERVER_RUNTIME_VERSION" in source
    assert source.count('"server_version": SERVER_RUNTIME_VERSION') == 2
    assert '"server_version": PATCH_VERSION' not in source
    match = re.search(r'^SERVER_RUNTIME_VERSION = "([^"]+)"$', runtime, re.MULTILINE)
    assert match is not None
    assert match.group(1) == SERVER_RUNTIME_VERSION


def test_pairing_diagnostics_keep_current_server_binding_when_worker_window_is_empty() -> None:
    report = _pairing_evidence(
        worker_id="wrk_test",
        client_id="ext_test",
        worker={"extension_client_id": "ext_test"},
        extension_state={
            "online": True,
            "connection_enabled": True,
            "metadata": {
                "linux_worker_pairing_id": "pair_test",
                "linux_worker_binding_source": "worker-ticket-v30",
                "linux_worker_binding_version": 30,
                "chatgpt_login_state": "ready",
                "chatgpt_login_confidence": "high",
                "status": "connected",
            },
        },
        worker_log="unrelated worker log line\n",
        server_runtime=(
            '{"message":"[linux-worker] pairing matched worker_id=wrk_test pairing_id=pair_test"}\n'
            '{"message":"[linux-worker] binding completed worker_id=wrk_test extension_id=ext_test"}\n'
        ),
    )

    assert "[worker-bounded-window]" in report
    assert "No pairing/worker-bind lines in the Worker's bounded 90-minute window." in report
    assert "[server-current-binding]" in report
    assert '"online": true' in report
    assert '"pairing_id": "pair_test"' in report
    assert '"chatgpt_login_state": "ready"' in report
    assert "[server-runtime-pairing-events]" in report
    assert "pairing matched worker_id=wrk_test" in report
    assert "binding completed worker_id=wrk_test" in report


def test_worker_diagnostic_zip_writes_pairing_evidence_instead_of_empty_window_placeholder() -> None:
    source = read("app/linux_worker_diagnostics_patch.py")

    assert 'archive.writestr("pairing.log", pairing_evidence)' in source
    assert 'server_runtime = _server_runtime_for_worker(app, worker_id, client_id)' in source
    assert '"pairing.log": ("pairing", "worker-bind")' not in source
