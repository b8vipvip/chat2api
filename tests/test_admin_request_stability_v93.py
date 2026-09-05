from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_request_history_v93_suppresses_legacy_request_id_observer_before_window_manager() -> None:
    request_source = (ROOT / "app" / "admin_request_device_identity_v47.js").read_text(encoding="utf-8")
    window_source = (ROOT / "app" / "admin_window_manager_v88.js").read_text(encoding="utf-8")
    entry_source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    assert "requestStabilityRevision: 93" in request_source
    assert 'body.dataset.chat2apiRequestIdObserverV88 = "1";' in request_source
    assert 'body.dataset.chat2apiRequestOwnerV93 = "device-identity";' in request_source
    assert 'th[data-chat2api-request-id-v88]' in request_source
    assert 'td[data-chat2api-request-id-v88]' in request_source
    assert "setTimeout(paintRequestRows, 0)" in request_source
    assert "new MutationObserver" not in request_source

    # The legacy v88 hook is guarded by exactly the marker v93 sets. The request
    # identity asset must execute first so that observer can never be installed on
    # a fresh admin page.
    assert "if (!body || body.dataset.chat2apiRequestIdObserverV88) return;" in window_source
    assert entry_source.index("install_request_device_identity_patch(app)") < entry_source.index("install_window_manager_v88_patch(app)")


def test_request_history_v93_request_id_rendering_is_xss_safe_and_one_shot() -> None:
    source = (ROOT / "app" / "admin_request_device_identity_v47.js").read_text(encoding="utf-8")

    assert 'code.textContent = requestId;' in source
    assert 'requestIdCell.appendChild(code);' in source
    assert "requestIdCell.innerHTML" not in source
    assert "scheduleRequestPaint();" in source
