from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_window_manager_adds_extension_worker_id_column() -> None:
    source = text("app/admin_worker_identity_v131.js")
    assert 'th.textContent = "Worker ID"' in source
    assert 'String(tr.dataset?.client || "").trim()' in source
    assert 'data-v131-worker-id-cell' in source
    assert 'tr.cells[0].colSpan !== 8' in source
    # Window numbers remain Worker-local telemetry, so Worker ID is the stable
    # disambiguator when two independent Profiles both report e.g. window #5.
    observer = text("chrome_extension/background_window_observer_v90.js")
    assert "nextWindowNo: 1" in observer
    assert "window_no: state.nextWindowNo++" in observer


def test_linux_device_manager_worker_column_uses_extension_worker_id_without_secondary_metadata() -> None:
    source = text("app/admin_worker_identity_v131.js")
    assert 'header.cells[0].textContent !== "Worker ID"' in source
    assert 'String(worker.extension_client_id || "").trim()' in source
    assert 'const primary = extensionId || "Extension 待连接"' in source
    assert 'const html = `<b>${esc(primary)}</b>`;' in source
    assert "设备 Worker ${slot}" not in source
    assert '中心 ${esc(controllerId || "-")}' not in source


def test_unlogged_linux_child_can_inherit_device_name_before_pairing_bind() -> None:
    source = text("app/worker_presentation_v64_patch.py")
    assert 'IDENTITY_ASSET = "/assets/chat2api-worker-identity-v131.js"' in source
    assert 'linux_workers = getattr(app.state, "linux_workers", None)' in source
    assert 'client_id = str(worker.get("extension_client_id") or "").strip()' in source
    assert 'device_name = str(metadata.get("device_name") or worker_pairing.get("name") or "").strip()' in source
    assert 'linux_by_client[client_id] = device_name' in source
    assert 'linux_name = linux_by_client.get(client_id) or linux_by_worker.get(linux_worker_id) or ""' in source
    assert 'if not pairing_id:\n                    pairing_id = fallback_pairing' in source
    assert 'row["device_name"] = by_pairing.get(pairing_id) or fallback_name or linux_name or None' in source


def test_identity_asset_is_served_and_injected_once() -> None:
    source = text("app/worker_presentation_v64_patch.py")
    assert '@app.get(IDENTITY_ASSET, include_in_schema=False)' in source
    assert 'path = Path(__file__).with_name("admin_worker_identity_v131.js")' in source
    assert 'identity_marker = f\'<script src="{IDENTITY_ASSET}"></script>\'' in source
    assert 'if identity_marker not in text:' in source
