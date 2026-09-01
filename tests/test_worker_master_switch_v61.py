from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_failed_worker_disable_keeps_master_enabled_in_result_metadata() -> None:
    source = (ROOT / "chrome_extension" / "background_worker_master_switch_v61.js").read_text(encoding="utf-8")
    assert 'worker_master_enabled: !(result.action === "worker.disable" && result.ok)' in source
    assert 'phase: "disable-failed"' in source
    assert '[DISABLED_STORAGE_KEY]: false' in source
    assert 'socketState: connected ? "connected" : "disconnected"' in source


def test_successful_worker_disable_closes_socket_only_after_control_result_send() -> None:
    source = (ROOT / "chrome_extension" / "background_worker_master_switch_v61.js").read_text(encoding="utf-8")
    emit = source.index("const result = await emitResult(message, true, data)")
    close = source.index('socket.close(4003, "Worker disabled by administrator")')
    assert emit < close
    assert 'socketState: "disconnecting"' in source
    assert "await markDisabling()" in source
