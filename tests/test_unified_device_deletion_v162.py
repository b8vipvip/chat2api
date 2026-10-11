from __future__ import annotations

from pathlib import Path

import pytest

from app.linux_worker_device_authority_v124_patch import _unified_device_delete_plan

ROOT = Path(__file__).resolve().parents[1]


def worker(worker_id: str, client_id: str = "", **metadata: object) -> dict:
    return {"worker_id": worker_id, "extension_client_id": client_id, "metadata": metadata}


def client(client_id: str, device_id: str = "", pairing_id: str = "", **metadata: object) -> dict:
    return {"client_id": client_id, "device_id": device_id,
            "pairing_id": pairing_id, "metadata": metadata}


def pair(pairing_id: str, client_id: str = "", device_id: str = "") -> dict:
    return {"pairing_id": pairing_id, "bound_client_id": client_id,
            "bound_device_id": device_id}


def test_linux_deletion_removes_entire_identity_and_retired_slots() -> None:
    devices = [{
        "device_id": "linux_tx03", "install_id": "install_tx03",
        "_primary_worker_id": "wrk_primary", "device_pairing_id": "pair_primary",
        "device_name": "TX03",
        "workers": [worker("wrk_primary", "ext_primary"), worker("wrk_child", "ext_child")],
    }]
    all_workers = [
        worker("wrk_primary", "ext_primary", device_id_v124="linux_tx03"),
        worker("wrk_child", "ext_child", device_id_v124="linux_tx03", controller_worker_id="wrk_primary"),
        worker("wrk_retired", "ext_retired", device_id_v124="linux_tx03", controller_worker_id="wrk_primary"),
    ]
    clients = [
        client("ext_primary", pairing_id="pair_primary", linux_worker_id="wrk_primary"),
        client("ext_child", linux_worker_id="wrk_child"),
        client("ext_retired", linux_worker_id="wrk_retired"),
        client("ext_unrelated", pairing_id="pair_elsewhere"),
    ]
    codes = [
        pair("pair_primary", "ext_primary"),
        pair("pair_secondary", "ext_child"),
        pair("pair_elsewhere", "ext_unrelated"),
    ]
    plan = _unified_device_delete_plan("linux:linux_tx03", devices, clients, codes, all_workers)
    assert plan["install_id"] == "install_tx03"
    assert plan["worker_ids"] == ["wrk_child", "wrk_primary", "wrk_retired"]
    assert plan["client_ids"] == ["ext_child", "ext_primary", "ext_retired"]
    assert plan["pairing_ids"] == ["pair_primary", "pair_secondary"]
    assert "ext_unrelated" not in plan["client_ids"]


def test_physical_record_removes_all_exact_client_and_pairing_identities_only() -> None:
    clients = [
        client("ext_win1", "hardware_123", "pair_a"),
        client("ext_win2", "hardware_123", "pair_b"),
        client("ext_win3", "hardware_456", "pair_c"),
    ]
    codes = [
        pair("pair_a", "ext_win1", "hardware_123"),
        pair("pair_b", "ext_win2", "hardware_123"),
        pair("pair_c", "ext_win3", "hardware_456"),
    ]
    plan = _unified_device_delete_plan("physical:hardware_123", [], clients, codes, [])
    assert plan["client_ids"] == ["ext_win1", "ext_win2"]
    assert plan["pairing_ids"] == ["pair_a", "pair_b"]
    assert plan["worker_ids"] == []
    assert plan["install_id"] == ""
    assert "ext_win3" not in plan["client_ids"]


def test_pending_credential_and_standalone_client_both_deletable() -> None:
    codes = [pair("pending_a"), pair("pending_b")]
    plan = _unified_device_delete_plan("pairing:pending_a", [], [], codes, [])
    assert plan["pairing_ids"] == ["pending_a"]
    assert plan["client_ids"] == []

    clients = [client("orphan_1"), client("orphan_2")]
    plan = _unified_device_delete_plan("client:orphan_1", [], clients, [], [])
    assert plan["client_ids"] == ["orphan_1"]
    assert plan["pairing_ids"] == []


def test_orphan_worker_record_is_retired_when_exact_bridge_identity_matches() -> None:
    clients = [client("bridge_orphan", linux_worker_id="wrk_orphan")]
    workers = [worker("wrk_orphan", "bridge_orphan")]
    plan = _unified_device_delete_plan("client:bridge_orphan", [], clients, [], workers)
    assert plan["worker_ids"] == ["wrk_orphan"]


def test_other_linux_device_cannot_be_deleted_through_partial_bridge_row() -> None:
    devices = [
        {"device_id": "linux_a", "install_id": "i_a", "workers": [worker("wrk_a", "ext_a")]},
        {"device_id": "linux_b", "install_id": "i_b", "workers": [worker("wrk_b", "ext_b")]},
    ]
    clients = [client("ext_a", "hardware_a", linux_worker_id="wrk_a"),
               client("ext_b", "hardware_b", linux_worker_id="wrk_b")]
    with pytest.raises(ValueError, match="Linux installation"):
        _unified_device_delete_plan("physical:hardware_a", devices, clients, [], [])
    with pytest.raises(ValueError, match="Linux installation"):
        _unified_device_delete_plan("client:ext_b", devices, clients, [], [])


def test_conflicting_linux_bridge_owner_must_fail_closed() -> None:
    devices = [
        {"device_id": "linux_a", "install_id": "i_a",
         "workers": [worker("wrk_a", "ext_shared")]},
        {"device_id": "linux_b", "install_id": "i_b",
         "workers": [worker("wrk_b", "ext_shared")]},
    ]
    clients = [client("ext_shared", linux_worker_id="wrk_a")]
    with pytest.raises(ValueError, match="multiple Linux devices"):
        _unified_device_delete_plan("linux:linux_a", devices, clients, [], [])


def test_shared_pairing_from_another_client_blocks_device_deletion() -> None:
    devices = [{"device_id": "linux_a", "install_id": "i_a",
                "device_pairing_id": "pair_shared",
                "workers": [worker("wrk_a", "ext_a")]}]
    with pytest.raises(ValueError, match="another device"):
        _unified_device_delete_plan(
            "linux:linux_a", devices,
            [client("ext_a", linux_worker_id="wrk_a"), client("ext_b")],
            [pair("pair_shared", "ext_b")], [],
        )


def test_device_deletion_backend_is_authenticated_and_requires_explicit_confirmation() -> None:
    backend = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    ui = (ROOT / "app" / "admin_unified_workers_v153.js").read_text(encoding="utf-8")
    assert '@app.get("/api/admin/unified-devices/delete-preview")' in backend
    assert '@app.delete("/api/admin/unified-devices")' in backend
    assert 'body.get("confirm") != "DELETE_DEVICE"' in backend
    assert "admin(request)" in backend
    assert "app.state.registry.delete_client(client_id)" in backend
    assert "app.state.pairings.delete(pairing_id)" in backend
    assert "_delete_worker(workers, worker_id)" in backend
    assert "_delete_install_and_enrollment(app, plan[" in backend
    assert "data-unified-delete-device" in ui
    assert 'confirm:"DELETE_DEVICE"' in ui
    assert 'method:"DELETE"' in ui
