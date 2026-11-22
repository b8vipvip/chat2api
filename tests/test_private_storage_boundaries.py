from __future__ import annotations

import asyncio
import logging
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.admin_audit import AdminAuditError, AdminAuditStore
from app.admin_auth import AdminSessionStore
from app.api_keys import ApiKeyStore, load_or_create_data_secret
from app.diagnostics import DiagnosticStore, configure_file_logging
from app.media_archive_v173 import MediaArchive
from app.registry import ClientRegistry
from app.pairing import PairingStore
from app.payment_channels_v106_patch import (
    PaymentChannelsStore, _payment_channels_secret_candidate, _payment_channels_secret_lock,
    _persist_payment_channels_secret,
)
from app.prompt_config import PromptConfigStore, default_config
from app.private_storage import private_file_lock
from app.plugin_registry import PluginRegistry
from app.quota_reservations import QuotaReservationStore
from app.test_runs import TestRunStore
from app.telemetry import TelemetryStore
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.worker_probe_store import WorkerProbeStore
from app.runtime_logs import RuntimeLogStore
from app.registration_invitations import InvitationStore
from app.tenant_membership import TenantMembershipStore
from app.user_subscriptions import UserSubscriptionStore


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
    # A stale mkdir lock from an older release must not block the new lock file.
    (tmp_path / ".data_secret.lock").mkdir()
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_payment_credentials_and_prompt_config_use_private_files(tmp_path: Path) -> None:
    payment_root = tmp_path / "payment"
    payment = PaymentChannelsStore(payment_root, "payment-secret")
    assert stat.S_IMODE(payment.path.stat().st_mode) == 0o600
    _persist_payment_channels_secret(payment_root, "payment-data-secret")
    secret_path = payment_root / ".chat2api_data_secret"
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600

    prompt_root = tmp_path / "prompts"
    prompt = PromptConfigStore(prompt_root)
    prompt.save(default_config())
    assert stat.S_IMODE(prompt.path.stat().st_mode) == 0o600

    outside = tmp_path / "outside-prompt-temp"
    outside.write_text("must remain untouched", encoding="utf-8")
    prompt.path.with_suffix(".tmp").symlink_to(outside)
    prompt.save(default_config())
    assert outside.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_payment_secret_and_prompt_config_loaders_reject_symlinks(tmp_path: Path) -> None:
    payment_root = tmp_path / "linked-payment"
    payment_root.mkdir()
    outside_secret = tmp_path / "outside-payment-secret"
    outside_secret.write_text("must remain untouched", encoding="utf-8")
    (payment_root / ".chat2api_data_secret").symlink_to(outside_secret)
    with pytest.raises(RuntimeError, match="safely"):
        _payment_channels_secret_candidate(payment_root, SimpleNamespace(api_key="legacy"))

    prompt_root = tmp_path / "linked-prompt"
    prompt_root.mkdir()
    outside_prompt = tmp_path / "outside-prompt.json"
    outside_prompt.write_text('{"prefix":"must not be loaded"}', encoding="utf-8")
    (prompt_root / "prompt_config.json").symlink_to(outside_prompt)
    prompt = PromptConfigStore(prompt_root)
    assert prompt.last_error
    assert prompt.config["prefix"] == ""
    with pytest.raises(RuntimeError, match="symbolic link"):
        prompt.save(default_config())
    assert outside_prompt.read_text(encoding="utf-8") == '{"prefix":"must not be loaded"}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_payment_configuration_rejects_symlinked_credentials_file(tmp_path: Path) -> None:
    target = tmp_path / "outside-payment-config.json"
    target.write_text('{"aggregate":{},"direct":{}}', encoding="utf-8")
    path = tmp_path / "user_payment_channels_v106.json"
    path.symlink_to(target)
    with pytest.raises(RuntimeError):
        PaymentChannelsStore(tmp_path, "payment-secret")
    assert target.read_text(encoding="utf-8") == '{"aggregate":{},"direct":{}}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertions")
def test_payment_secret_initialization_is_serialized_across_workers(tmp_path: Path) -> None:
    root = tmp_path / "concurrent-payment"
    root.mkdir()
    # An abandoned legacy directory lock must not block the lock-file version.
    (root / ".payment_channels_secret.lock").mkdir()

    def initialize(_: int) -> str:
        with _payment_channels_secret_lock(root):
            value, persist = _payment_channels_secret_candidate(
                root, SimpleNamespace(api_key="legacy-api-key"),
            )
            if persist:
                _persist_payment_channels_secret(root, value)
            return value

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(initialize, range(16)))
    assert len(set(values)) == 1
    assert stat.S_IMODE((root / ".chat2api_data_secret").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock concurrency assertion")
def test_admin_audit_hash_chain_is_serialized_across_store_instances(tmp_path: Path) -> None:
    stores = [AdminAuditStore(tmp_path) for _ in range(8)]

    def append(index: int) -> None:
        stores[index % len(stores)].append(
            actor="worker",
            action="request.completed",
            target_type="request",
            target_id=f"req_{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(80)))

    rows = stores[0].recent(200)
    assert len(rows) == 80
    assert len({row["event_id"] for row in rows}) == 80
    assert rows[-1]["previous_hash"] == AdminAuditStore.ZERO_HASH


def _crash_while_holding_private_lock(path: str) -> None:
    with private_file_lock(Path(path)):
        os._exit(23)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock crash-recovery assertion")
def test_private_file_lock_is_released_by_os_after_process_crash(tmp_path: Path) -> None:
    lock_path = tmp_path / ".crash-recovery.lock"
    process = multiprocessing.get_context("fork").Process(
        target=_crash_while_holding_private_lock,
        args=(str(lock_path),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive()
    assert process.exitcode == 23

    # flock ownership is tied to the open descriptor, so the kernel releases it
    # even though the child bypassed Python's finally/context-manager cleanup.
    with private_file_lock(lock_path):
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission, symlink and hard-link assertions")
def test_invitation_tenant_and_subscription_sqlite_stores_reject_linked_sidecars(tmp_path: Path) -> None:
    factories = (
        ("invitations", InvitationStore),
        ("tenants", TenantMembershipStore),
        ("subscriptions", UserSubscriptionStore),
    )
    for name, factory in factories:
        root = tmp_path / name
        store = factory(root)
        database = store.path
        connection = getattr(store, "db", None)
        if connection is not None:
            connection.close()
        assert stat.S_IMODE(database.stat().st_mode) == 0o600

        sidecar_target = tmp_path / f"outside-{name}-wal"
        sidecar_target.write_text("must remain untouched", encoding="utf-8")
        sidecar = Path(str(database) + "-wal")
        sidecar.symlink_to(sidecar_target)
        with pytest.raises(RuntimeError, match="symbolic link"):
            factory(root)
        assert sidecar_target.read_text(encoding="utf-8") == "must remain untouched"
        sidecar.unlink()

        hardlink = tmp_path / f"outside-{name}-database"
        os.link(database, hardlink)
        with pytest.raises(RuntimeError, match="single-link regular files"):
            factory(root)
        assert database.exists() and hardlink.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission, symlink and hard-link assertions")
def test_runtime_log_storage_is_private_and_rejects_linked_files(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    store = RuntimeLogStore(root)
    store.append(
        level="INFO", logger_name="storage-test",
        message="private runtime log entry",
    )
    log_dir = root / "runtime_logs"
    log_path = log_dir / "chat2api-runtime.ndjson"
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600

    outside = tmp_path / "outside-runtime-log"
    outside.write_text('{"message":"outside secret"}\\n', encoding="utf-8")
    log_path.unlink()
    log_path.symlink_to(outside)
    store.append(
        level="ERROR", logger_name="storage-test",
        message="must not follow symlink",
    )
    assert outside.read_text(encoding="utf-8") == '{"message":"outside secret"}\\n'
    assert RuntimeLogStore(root).query(limit=10)["returned"] == 0

    log_path.unlink()
    store.append(
        level="INFO", logger_name="storage-test",
        message="create a new regular log",
    )
    before = log_path.read_bytes()
    hardlink = tmp_path / "outside-runtime-hardlink"
    os.link(log_path, hardlink)
    store.append(
        level="ERROR", logger_name="storage-test",
        message="must not append through hard link",
    )
    assert log_path.read_bytes() == before
    assert hardlink.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertions")
def test_runtime_log_rotation_preserves_private_modes(tmp_path: Path, monkeypatch) -> None:
    import app.runtime_logs as runtime_logs_module

    monkeypatch.setattr(runtime_logs_module, "MAX_FILE_BYTES", 1)
    store = RuntimeLogStore(tmp_path)
    store.append(level="INFO", logger_name="rotation", message="first")
    store.append(level="INFO", logger_name="rotation", message="second")
    rotated = tmp_path / "runtime_logs" / "chat2api-runtime.ndjson.1"
    current = tmp_path / "runtime_logs" / "chat2api-runtime.ndjson"
    assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
