from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import stat
from pathlib import Path

import pytest

from app.admin_audit import AdminAuditError, AdminAuditStore
from app.admin_auth import AdminSessionStore
from app.api_keys import ApiKeyStore, load_or_create_data_secret
from app.diagnostics import DiagnosticStore, configure_file_logging
from app.media_archive_v173 import MediaArchive
from app.registry import ClientRegistry
from app.pairing import PairingStore
from app.plugin_registry import PluginRegistry
from app.quota_reservations import QuotaReservationStore
from app.test_runs import TestRunStore
from app.telemetry import TelemetryStore
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.worker_probe_store import WorkerProbeStore


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_web_chat_conversation_wal_and_shm_sidecars_reject_symlinks(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(store.path) + suffix)
        sidecar.unlink(missing_ok=True)
        target = tmp_path / f"outside{suffix}"
        target.write_text("must remain untouched", encoding="utf-8")
        sidecar.symlink_to(target)
        try:
            with pytest.raises(RuntimeError, match="symbolic links"):
                store._connect()
            assert target.read_text(encoding="utf-8") == "must remain untouched"
        finally:
            sidecar.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link assertions")
def test_web_chat_conversation_database_rejects_hard_links(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    linked = tmp_path / "linked-conversation-db.sqlite3"
    os.link(store.path, linked)
    with pytest.raises(RuntimeError, match="private regular files"):
        store._connect()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_media_archive_settings_reject_symlink_and_keep_outside_file_untouched(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-settings.enc"
    outside.write_bytes(b"outside settings must remain untouched")
    store.settings_path.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symbolic link"):
        store.public_config()
    assert outside.read_bytes() == b"outside settings must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_media_archive_rejects_symlinked_intermediate_object_directory(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-objects"
    outside.mkdir()
    (store.root / "objects").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="object directories"):
        store._read({}, "local", "objects/image.png")


def test_media_archive_settings_write_ignores_legacy_predictable_temp_path(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-temp"
    outside.write_bytes(b"must remain untouched")
    predictable_temp = store.settings_path.with_suffix(".tmp")
    try:
        predictable_temp.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported")
    configured = store.configure({"backend": "local"})
    assert configured["backend"] == "local"
    assert outside.read_bytes() == b"must remain untouched"
    assert store.settings_path.is_file()
    assert not store.settings_path.is_symlink()


def test_telemetry_load_uses_the_validated_open_descriptor(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "request_history.jsonl"
    path.write_text(
        '{"request_id":"req_descriptor_read","status":"completed","response_text":"ok"}\n',
        encoding="utf-8",
    )
    original_open = Path.open

    def reject_history_reopen(self, *args, **kwargs):
        if self == path:
            raise AssertionError("validated telemetry path must not be reopened")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_history_reopen)
    store = TelemetryStore(tmp_path)
    asyncio.run(store.load())
    assert store.get("req_descriptor_read")["response_text"] == "ok"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_diagnostic_and_test_run_histories_are_private_and_reject_symlinks(tmp_path: Path) -> None:
    diagnostics = DiagnosticStore(tmp_path / "diagnostics")
    asyncio.run(diagnostics.append({"trace_id": "trace_private_1", "status": 500}))
    assert stat.S_IMODE(diagnostics.path.stat().st_mode) == 0o600

    test_runs = TestRunStore(tmp_path / "test-runs")
    asyncio.run(test_runs.upsert({"run_id": "run_private_1", "status": "completed"}))
    assert stat.S_IMODE(test_runs.path.stat().st_mode) == 0o600

    for filename, factory in (
        ("server_events.jsonl", DiagnosticStore),
        ("test_runs.jsonl", TestRunStore),
    ):
        linked_root = tmp_path / f"linked-{filename}"
        linked_root.mkdir()
        target = tmp_path / f"outside-{filename}"
        target.write_text("must remain untouched", encoding="utf-8")
        (linked_root / filename).symlink_to(target)
        if factory is DiagnosticStore:
            with pytest.raises(RuntimeError, match="symbolic link"):
                factory(linked_root)
        else:
            store = factory(linked_root)
            with pytest.raises(RuntimeError, match="symbolic link"):
                asyncio.run(store.load())
        assert target.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_worker_probe_policy_and_event_storage_are_private(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.events_root.stat().st_mode) == 0o700
    worker_id = "ext_testworker0001"
    request_id = "req_privateprobe12345678"

    async def write_probe() -> None:
        await store.put_policy(
            "worker:" + worker_id,
            expected_revision=0,
            tools={"media_delivery_v1": {"enabled": True}},
        )
        result = await store.ingest(
            request_id=request_id,
            worker_id=worker_id,
            client_id="ext_abcdef123456",
            effective_policy=store.effective(worker_id),
            message={
                "tool_id": "media_delivery_v1",
                "sequence": 1,
                "stage": "before_terminal",
                "classification": "completed_observed",
                "facts": {"main_found": True},
            },
        )
        assert result["accepted"] is True

    asyncio.run(write_probe())
    event_path = store._file(request_id)
    assert stat.S_IMODE(store.policy_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(event_path.stat().st_mode) == 0o600
    assert store.events(request_id)[0]["facts"]["main_found"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_worker_probe_rejects_symlinked_policy_and_event_files(tmp_path: Path) -> None:
    policy_root = tmp_path / "linked-policy"
    policy_root.mkdir()
    worker_root = policy_root / "worker_probes"
    worker_root.mkdir()
    target_policy = tmp_path / "outside-policy.json"
    target_policy.write_text("must remain untouched", encoding="utf-8")
    (worker_root / "policies.json").symlink_to(target_policy)
    with pytest.raises(RuntimeError, match="symbolic link"):
        WorkerProbeStore(policy_root)
    assert target_policy.read_text(encoding="utf-8") == "must remain untouched"

    event_root = tmp_path / "linked-event"
    store = WorkerProbeStore(event_root)
    worker_id = "ext_testworker0001"

    async def enable() -> None:
        await store.put_policy(
            "worker:" + worker_id,
            expected_revision=0,
            tools={"media_delivery_v1": {"enabled": True}},
        )

    asyncio.run(enable())
    request_id = "req_linkedprobe12345678"
    target_event = tmp_path / "outside-probe.jsonl"
    target_event.write_text("must remain untouched", encoding="utf-8")
    store._file(request_id).symlink_to(target_event)
    with pytest.raises(RuntimeError, match="symbolic link"):
        asyncio.run(store.ingest(
            request_id=request_id,
            worker_id=worker_id,
            client_id="ext_abcdef123456",
            effective_policy=store.effective(worker_id),
            message={
                "tool_id": "media_delivery_v1",
                "sequence": 1,
                "stage": "before_terminal",
                "classification": "completed_observed",
                "facts": {"main_found": True},
            },
        ))
    assert target_event.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_rotating_application_log_is_private_and_rejects_symlinks(tmp_path: Path) -> None:
    logger = logging.getLogger("chat2api")
    old_handlers = list(logger.handlers)
    try:
        for handler in old_handlers:
            logger.removeHandler(handler)
            handler.close()
        log_path = configure_file_logging(tmp_path)
        logger.info("private storage permission test")
        for handler in logger.handlers:
            handler.flush()
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
        handler = logger.handlers[-1]
        handler.maxBytes = 1
        logger.info("force a private log rollover")
        handler.flush()
        rotated = Path(str(log_path) + ".1")
        assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        for handler in old_handlers:
            logger.addHandler(handler)

    linked_root = tmp_path / "linked-log"
    linked_root.mkdir()
    target = tmp_path / "outside-chat2api.log"
    target.write_text("must remain untouched", encoding="utf-8")
    (linked_root / "chat2api.log").symlink_to(target)
    logger = logging.getLogger("chat2api")
    try:
        with pytest.raises(RuntimeError, match="symbolic link"):
            configure_file_logging(linked_root)
    finally:
        for handler in list(logger.handlers):
            if getattr(handler, "baseFilename", None) == str((linked_root / "chat2api.log").resolve()):
                logger.removeHandler(handler)
                handler.close()
    assert target.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_worker_probe_policy_writer_ignores_predictable_temp_symlink(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    outside = tmp_path / "outside-policy-temp"
    outside.write_text("must remain untouched", encoding="utf-8")
    predictable_temp = store.policy_path.with_suffix(".tmp")
    predictable_temp.symlink_to(outside)

    async def write_policy() -> None:
        await store.put_policy(
            "global",
            expected_revision=0,
            tools={"media_delivery_v1": {"enabled": True}},
        )

    asyncio.run(write_policy())
    assert outside.read_text(encoding="utf-8") == "must remain untouched"
    assert store.policy_path.is_file()
    assert not store.policy_path.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_api_key_and_registry_files_are_private_and_reject_symlinks(tmp_path: Path) -> None:
    api_store = ApiKeyStore(tmp_path / "api")
    asyncio.run(api_store.save())
    assert stat.S_IMODE(api_store.path.stat().st_mode) == 0o600

    registry = ClientRegistry(tmp_path / "registry")
    asyncio.run(registry.save())
    assert stat.S_IMODE(registry.path.stat().st_mode) == 0o600

    for filename, factory, load in (
        ("api_keys.json", ApiKeyStore, lambda store: asyncio.run(store.load())),
        ("clients.json", ClientRegistry, lambda store: asyncio.run(store.load())),
    ):
        linked_root = tmp_path / f"linked-{filename}"
        linked_root.mkdir()
        target = tmp_path / f"outside-{filename}"
        target.write_text("must remain untouched", encoding="utf-8")
        (linked_root / filename).symlink_to(target)
        store = factory(linked_root)
        with pytest.raises(RuntimeError, match="symbolic link"):
            load(store)
        assert target.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_data_secret_is_private_and_initialization_is_serialized(tmp_path: Path) -> None:
    secret = load_or_create_data_secret(tmp_path)
    assert stat.S_IMODE((tmp_path / "data_secret.key").stat().st_mode) == 0o600
    with ThreadPoolExecutor(max_workers=8) as pool:
        concurrent = list(pool.map(lambda _: load_or_create_data_secret(tmp_path), range(16)))
    assert set(concurrent) == {secret}


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_api_key_and_registry_atomic_writes_ignore_predictable_temp_symlinks(tmp_path: Path) -> None:
    for filename, factory, save in (
        ("api_keys.json", ApiKeyStore, lambda store: asyncio.run(store.save())),
        ("clients.json", ClientRegistry, lambda store: asyncio.run(store.save())),
    ):
        root = tmp_path / f"atomic-{filename}"
        root.mkdir()
        outside = tmp_path / f"outside-{filename}-temp"
        outside.write_text("must remain untouched", encoding="utf-8")
        (root / filename).with_suffix(".tmp").symlink_to(outside)
        store = factory(root)
        save(store)
        assert outside.read_text(encoding="utf-8") == "must remain untouched"
        assert stat.S_IMODE((root / filename).stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_data_secret_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    target = tmp_path / "outside-secret"
    target.write_text("not the application secret", encoding="utf-8")
    (tmp_path / "data_secret.key").symlink_to(target)
    with pytest.raises(RuntimeError, match="symbolic link"):
        load_or_create_data_secret(tmp_path)
    assert target.read_text(encoding="utf-8") == "not the application secret"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_admin_sessions_and_pairing_codes_are_private(tmp_path: Path) -> None:
    sessions_path = tmp_path / "admin" / "admin_sessions.json"
    sessions = AdminSessionStore("admin", "password", storage_path=sessions_path)
    token = sessions.create()
    assert stat.S_IMODE(sessions_path.stat().st_mode) == 0o600
    reloaded = AdminSessionStore("admin", "password", storage_path=sessions_path)
    assert reloaded.authenticate(token)

    pairing_root = tmp_path / "pairing"
    pairing = PairingStore(pairing_root)
    outside = tmp_path / "outside-pairing-temp"
    outside.write_text("must remain untouched", encoding="utf-8")
    pairing.path.with_suffix(".tmp").symlink_to(outside)
    public, raw_code = asyncio.run(pairing.create())
    assert public["pairing_id"]
    assert raw_code.startswith("pair-")
    assert stat.S_IMODE(pairing.path.stat().st_mode) == 0o600
    assert outside.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_admin_session_and_pairing_loaders_reject_symlinked_files(tmp_path: Path) -> None:
    target = tmp_path / "outside-sessions"
    target.write_text('{"sessions":[]}', encoding="utf-8")
    session_path = tmp_path / "linked-sessions.json"
    session_path.symlink_to(target)
    sessions = AdminSessionStore("admin", "password", storage_path=session_path)
    assert sessions.last_persistence_error
    sessions.create()
    assert target.read_text(encoding="utf-8") == '{"sessions":[]}'

    pairing_root = tmp_path / "linked-pairing"
    pairing = PairingStore(pairing_root)
    outside_pairing = tmp_path / "outside-pairing.json"
    outside_pairing.write_text('{"pairing_codes":[]}', encoding="utf-8")
    pairing.path.symlink_to(outside_pairing)
    with pytest.raises(RuntimeError, match="symbolic link"):
        asyncio.run(pairing.load())
    assert outside_pairing.read_text(encoding="utf-8") == '{"pairing_codes":[]}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
def test_admin_audit_rejects_symlinked_and_hard_linked_logs(tmp_path: Path) -> None:
    linked = AdminAuditStore(tmp_path / "linked")
    target = tmp_path / "outside-audit.jsonl"
    target.write_text("", encoding="utf-8")
    linked.path.symlink_to(target)
    with pytest.raises(AdminAuditError, match="opened safely"):
        linked.recent()
    assert target.read_text(encoding="utf-8") == ""

    hardlinked = AdminAuditStore(tmp_path / "hardlinked")
    hardlinked.path.write_text("", encoding="utf-8")
    os.link(hardlinked.path, tmp_path / "outside-audit-hardlink.jsonl")
    with pytest.raises(AdminAuditError, match="opened safely"):
        hardlinked.recent()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_plugin_and_quota_sqlite_files_are_private_and_reject_sidecar_links(tmp_path: Path) -> None:
    plugin = PluginRegistry(tmp_path / "plugin")
    plugin.db.close()
    assert stat.S_IMODE(plugin.path.stat().st_mode) == 0o600
    plugin_wal = Path(str(plugin.path) + "-wal")
    plugin_target = tmp_path / "outside-plugin-wal"
    plugin_target.write_text("must remain untouched", encoding="utf-8")
    plugin_wal.symlink_to(plugin_target)
    with pytest.raises(RuntimeError, match="symbolic link"):
        PluginRegistry(tmp_path / "plugin")
    assert plugin_target.read_text(encoding="utf-8") == "must remain untouched"

    quota = QuotaReservationStore(tmp_path / "quota")
    quota.db.close()
    assert stat.S_IMODE(quota.path.stat().st_mode) == 0o600
    quota_shm = Path(str(quota.path) + "-shm")
    quota_target = tmp_path / "outside-quota-shm"
    quota_target.write_text("must remain untouched", encoding="utf-8")
    quota_shm.symlink_to(quota_target)
    with pytest.raises(RuntimeError, match="symbolic link"):
        QuotaReservationStore(tmp_path / "quota")
    assert quota_target.read_text(encoding="utf-8") == "must remain untouched"
