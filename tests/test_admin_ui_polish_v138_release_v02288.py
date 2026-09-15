from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from app.request_window_observability_v117_patch import _registry_window_fields, _window_fields

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02288_server_only_release_contract() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert SERVER_RUNTIME_VERSION == "0.22.91"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.40"
    assert payload["server"]["runtime_aligned"] is True
    assert payload["features"]["admin_worker_manager_layout_v138"] is True
    assert payload["features"]["window_identity_v138"] is True
    assert payload["features"]["worker_limit_popover_v138"] is True
    assert "release-v02288" in payload["server"]["feature_revision"]


def test_worker_manager_is_wide_and_secondary_metadata_is_removed() -> None:
    source = read("app/admin_worker_identity_v131.js")
    assert "min(1280px, calc(100vw - 28px))" in source
    assert 'button.style.whiteSpace = "nowrap"' in source
    assert 'const html = `<b>${esc(primary)}</b>`;' in source
    assert "设备 Worker ${slot} · 中心" not in source
    assert "常驻窗口池跟随并发" not in source
    assert "常驻窗口池独立设置" not in source


def test_worker_limit_cell_is_compact_summary_with_popover_editor() -> None:
    source = read("app/admin_worker_limits_clipboard_v121.js")
    assert "data-v121-limit-summary" in source
    assert "${concurrency}/${windows}" in source
    assert "data-v121-edit-limits" in source
    assert "data-v121-limit-popover hidden" in source
    assert "data-v121-cancel-limits" in source
    assert "窗口跟随并发" not in source
    assert "窗口独立设置" not in source


def test_window_manager_uses_worker_tail_plus_local_window_number() -> None:
    source = read("app/admin_window_manager_v88.js")
    assert source.count("<th>窗口标识</th>") == 2
    assert 'String(clientId || "").trim().slice(-4)' in source
    assert "`${workerTail}#${localNo}`" in source
    assert '<td>${esc(windowIdentity(clientId, row.window_no))}</td>' in source


def test_request_history_uses_same_window_identity_and_registry_fallback() -> None:
    source = read("app/request_window_observability_v117_patch.py")
    assert ">窗口标识</th>" in source
    assert "`${workerTail}#${windowNumber}`" in source
    fields = _window_fields({"window_number": 11, "client_id": "ext_pCzTxpdSpaur"})
    assert fields["window_number"] == 11
    assert fields["worker_client_id"] == "ext_pCzTxpdSpaur"

    class Registry:
        def summaries(self):
            return [{
                "client_id": "ext_pCzTxpdSpaur",
                "metadata": {"window_manager_v88": {"active": [{"request_id": "req_demo", "window_no": 12, "window_id": 72}]}}
            }]

    resolved = _registry_window_fields(Registry(), "req_demo")
    assert resolved["worker_client_id"] == "ext_pCzTxpdSpaur"
    assert resolved["window_number"] == 12
    assert resolved["window_id"] == 72
