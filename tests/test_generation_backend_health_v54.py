from __future__ import annotations

import json
from pathlib import Path

from app import generation_backend_routing_patch as routing


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generation_probe_checks_real_text_generation_control_paths() -> None:
    source = read("scripts/linux_worker_generation_probe.sh")
    for token in (
        "https://chatgpt.com/",
        "https://chatgpt.com/backend-api/f/conversation",
        "https://chatgpt.com/backend-api/sentinel/chat-requirements",
        'socks5h://127.0.0.1:${PROXY_PORT}',
        "generation_backend_ready=true",
        "generation_backend_ready=false",
        "Accept: text/event-stream",
    ):
        assert token in source
    assert '"generation_bzr|https://bzr.openai.com/"' not in source
    assert '"generation_ws|https://ws.chatgpt.com/"' not in source
    assert "Do NOT use bzr.openai.com as a text-generation health gate" in source


def test_docker_worker_bundle_includes_generation_probe() -> None:
    dockerfile = read("Dockerfile")
    assert "scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "/app/worker_payload/scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "linux-worker-bundle.tar.gz" in dockerfile


def test_proxy_transaction_rejects_node_when_generation_backend_probe_fails() -> None:
    source = read("scripts/linux_worker_proxy_apply.sh")
    probe_stage = source.index('CURRENT_STAGE="generation_backend_connectivity_test"')
    chrome_restart = source.index('CURRENT_STAGE="restart_chrome"')
    complete = source.index('CURRENT_STAGE="complete"')
    assert probe_stage < chrome_restart < complete
    assert 'GENERATION_PROBE="/opt/chat2api-worker/scripts/linux_worker_generation_probe.sh"' in source
    assert 'emit_error "generation_backend_connectivity_test_failed" true 6' in source
    assert 'emit_error "generation_backend_connectivity_test_failed_rollback_failed" false 6' in source


def test_linux_worker_chrome_disables_quic_for_socks_proxy_path() -> None:
    source = read("scripts/linux_worker_chrome_launcher.sh")
    proxy = source.index('--proxy-server="socks5://127.0.0.1:${PROXY_PORT}"')
    disable_quic = source.index("--disable-quic")
    debug = source.index("--remote-debugging-address=127.0.0.1")
    assert proxy < disable_quic < debug


def test_agent_v44_uses_generation_probe_and_reports_watchdog_state() -> None:
    source = read("scripts/linux_worker_agent_v44.py")
    for token in (
        "linux_worker_generation_probe.sh",
        "generation-backend-health.json",
        'base._proxy_test = _generation_proxy_test',
        'base.health = _health_with_generation_backend',
        '"generation_backend_health"',
        'payload["proxy_status"] = "error"',
        '"generation_backend_connectivity_test_failed"',
    ):
        assert token in source


def test_generation_backend_health_parser_is_fail_closed_only_when_fresh() -> None:
    now = int(routing.time.time())
    fresh_bad = {"metadata": {"generation_backend_health": {"ready": False, "checked_at_epoch": now - 10, "source": "linux-worker-watchdog-generation-v54"}}}
    state = routing._health(fresh_bad)
    assert state is not None
    assert state["ready"] is False
    assert state["fresh"] is True

    stale_bad = {"metadata": {"generation_backend_health": {"ready": False, "checked_at_epoch": now - routing.HEALTH_MAX_AGE_SECONDS - 10}}}
    stale = routing._health(stale_bad)
    assert stale is not None
    assert stale["fresh"] is False


def test_generation_health_guard_is_final_after_free_account_admission() -> None:
    entry = read("app/entry.py")
    assert entry.index("install_model_capability_routing_patch(app)") < entry.index("install_account_generation_admission_patch(app)")
    assert entry.index("install_account_generation_admission_patch(app)") < entry.index("install_generation_backend_routing_patch(app)")


def test_bundle_and_runtime_publish_generation_backend_health_revision() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    runtime = read("app/runtime_contract.py")
    assert manifest["version"] == "0.8.27"
    assert 'SERVER_RUNTIME_VERSION = "0.22.58"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.27"' in runtime
    assert '"linux_worker_generation_backend_health": True' in runtime
    assert '"linux_worker_proxy_health_facets": True' in runtime
    assert '"network_response_parser_v62": True' in runtime
    assert "generation-backend-health-v54" in runtime
    assert "proxy-health-v55" in runtime
