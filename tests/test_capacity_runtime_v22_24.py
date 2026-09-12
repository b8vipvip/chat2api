from pathlib import Path
import json
import subprocess

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def test_bridge_0831_busts_mv3_script_cache_without_touching_login_state():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")

    assert manifest["version"] == "0.8.35"
    assert 'Default/Service Worker/ScriptCache' in launcher
    assert 'Default/Code Cache/js' in launcher
    assert '--disable-extensions-except="$EXTENSION_DIR"' in launcher
    assert '--load-extension="$EXTENSION_DIR"' in launcher
    assert 'Default/Cookies' not in launcher
    assert 'IndexedDB' in launcher


def test_capacity_controller_vm_contracts_cover_native_and_reporter_paths():
    for script in (
        "capacity_control_v35.mjs",
        "capacity_control_v36.mjs",
        "capacity_capability_v37.mjs",
    ):
        result = subprocess.run(
            ["node", str(ROOT / "tests" / script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


def test_runtime_contract_publishes_single_authority_v02275():
    assert SERVER_RUNTIME_VERSION == "0.22.83"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.35"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["version"] == "0.8.1"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.35"
    assert payload["chrome_bridge"]["route_window_authority_revision"] == 30
    assert payload["chrome_bridge"]["window_observer_revision"] == 90
    assert payload["chrome_bridge"]["route_close_terminal_revision"] == 91
    assert payload["features"]["capacity_scheduler_v58"] is True
    assert payload["features"]["server_side_same_api_fifo_v58"] is True
    assert payload["features"]["worker_single_route_authority_v30"] is True
    assert payload["features"]["unexpected_route_close_terminal_v91"] is True
    assert payload["features"]["window_observer_v90"] is True
    assert payload["features"]["linux_worker_device_console_v122"] is False
    assert payload["features"]["same_api_parallel_requests"] is False
    assert payload["features"]["browser_side_same_api_queue"] is False
    assert payload["features"]["speculative_worker_windows"] is False
    assert payload["features"]["worker_key_capacity_fifo_queue"] is False
    assert payload["features"]["worker_single_route_v28"] is False
    assert payload["features"]["worker_strict_api_fifo_v29"] is False
    assert payload["features"]["worker_window_fifo_manager_v88"] is False
    # Content/request compatibility features carried by the release remain live.
    for feature in (
        "rendered_response_capture_recovery",
        "response_stream_recovery",
        "network_response_recovery",
        "network_response_parser_v62",
        "single_response_observer",
        "assistant_response_semantic_recovery",
        "model_capability_routing_v2",
        "chatgpt_rate_limit_circuit_breaker",
        "request_controller_lifecycle_guard",
        "chatgpt_transient_retry",
        "linux_worker_initialize",
        "linux_worker_master_switch",
        "worker_live_occupancy",
        "multimodal_main_world_v78",
        "responses_tool_stream_v118",
    ):
        assert payload["features"][feature] is True, feature
    revision = payload["server"]["feature_revision"]
    assert "capacity-scheduler-v58" in revision
    assert "single-route-window-authority-v30" in revision
    assert "window-observer-v90" in revision
    assert "route-close-terminal-v91" in revision
    assert "release-v02275" in revision
    assert "linux-device-worker-console-v122" in revision