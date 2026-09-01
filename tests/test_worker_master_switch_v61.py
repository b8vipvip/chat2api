from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_failed_worker_disable_keeps_master_enabled_in_result_metadata() -> None:
    source = (ROOT / "chrome_extension" / "background_worker_master_switch_v61.js").read_text(encoding="utf-8")
    assert 'worker_master_enabled: !(result.action === "worker.disable" && result.ok)' in source
    assert 'phase: "disable-failed"' in source
    assert '[DISABLED_STORAGE_KEY]: false' in source
    assert '[AWAIT_DISCONNECT_KEY]: false' in source
    assert 'socketState: connected ? "connected" : "disconnected"' in source


def test_successful_worker_disable_closes_socket_only_after_control_result_send() -> None:
    source = (ROOT / "chrome_extension" / "background_worker_master_switch_v61.js").read_text(encoding="utf-8")
    emit = source.index("const result = await emitResult(message, true, data)")
    close = source.index('socket.close(4003, "Worker disabled by administrator")')
    assert emit < close
    assert 'socketState: "disconnecting"' in source
    assert "await markDisabling()" in source
    assert 'worker_master_switch_revision: 62' in source


def test_transient_connected_state_cannot_clear_disabled_flag_before_real_disconnect() -> None:
    source = (ROOT / "chrome_extension" / "background_worker_master_switch_v61.js").read_text(encoding="utf-8")
    assert 'AWAIT_DISCONNECT_KEY = "chat2apiWorkerMasterAwaitDisconnectV62"' in source
    assert '[AWAIT_DISCONNECT_KEY]: true' in source
    assert 'if (nextState === "disconnected")' in source
    assert '[AWAIT_DISCONNECT_KEY]: false' in source
    assert 'previousState !== "disconnected"' in source
    assert 'stored[DISABLED_STORAGE_KEY] && stored[AWAIT_DISCONNECT_KEY]' in source
    # The old implementation unconditionally cleared the flag on every connected
    # storage event; the v62 revision must consult persisted disable state first.
    connected = source.index('if (nextState === "connected")')
    guard = source.index('stored[DISABLED_STORAGE_KEY] && stored[AWAIT_DISCONNECT_KEY]', connected)
    clear = source.index('[DISABLED_STORAGE_KEY]: false', guard)
    assert connected < guard < clear
