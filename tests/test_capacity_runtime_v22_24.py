from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_bridge_082_busts_mv3_script_cache_without_touching_login_state():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")

    assert manifest["version"] == "0.8.17"
    assert 'Default/Service Worker/ScriptCache' in launcher
    assert 'Default/Code Cache/js' in launcher
    assert '--disable-extensions-except="$EXTENSION_DIR"' in launcher
    assert '--load-extension="$EXTENSION_DIR"' in launcher
    assert 'Default/Cookies' not in launcher
    assert 'IndexedDB' in launcher  # preservation comment documents the safety boundary


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


def test_runtime_contract_separates_protocol_from_new_bundle_build():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.45"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.17"' in runtime
    assert '"bundle_version": CHROME_BRIDGE_BUNDLE_VERSION' in runtime
    assert 'request-hygiene-v42-persistent-draft-ownership-v43-generation-liveness-v49' in runtime
    assert 'response-stream-v49-network-response-v55-parser-v62-same-api-concurrency-v25' in runtime
    assert 'request-lifecycle-v50-route-quarantine-v50-transient-retry-v50' in runtime
    assert 'response-semantic-guard-v1-response-semantic-recovery-v51' in runtime
    assert 'single-response-owner-v53' in runtime
    assert 'model-capability-routing-v1' in runtime
    assert 'rate-limit-guard-v52' in runtime
    assert 'worker-key-capacity-queue-v57' in runtime
    assert 'admin-render-owner-v58' in runtime
    assert 'routed-dispatch-terminal-v58' in runtime
    assert 'worker-master-switch-v61-r62' in runtime
    assert 'worker-disable-authority-v62' in runtime
    assert 'worker-live-occupancy-v61' in runtime
    assert 'multimodal-upload-v64' in runtime
    assert 'worker-presentation-v64' in runtime
    assert 'worker-presentation-v65-console-liveness-v65' in runtime
    assert 'worker-presentation-v66-column-registry-v67' in runtime
    assert 'multimodal-upload-v68-api-key-console-v68-rich-response-v69-content-runtime-v71' in runtime
    assert '"bridge_service_worker_cache_bust": True' in runtime
    assert '"rendered_response_capture_recovery": True' in runtime
    assert '"response_stream_recovery": True' in runtime
    assert '"network_response_recovery": True' in runtime
    assert '"network_response_parser_v62": True' in runtime
    assert '"single_response_observer": True' in runtime
    assert '"assistant_response_semantic_guard": True' in runtime
    assert '"assistant_response_semantic_recovery": True' in runtime
    assert '"model_capability_routing_guard": True' in runtime
    assert '"chatgpt_rate_limit_circuit_breaker": True' in runtime
    assert '"worker_window_reopen_loop_guard": True' in runtime
    assert '"active_rate_limit_terminal_error": True' in runtime
    assert '"routed_dispatch_terminal_error": True' in runtime
    assert '"admin_single_render_owner": True' in runtime
    assert '"worker_key_capacity_fifo_queue": True' in runtime
    assert '"worker_window_concurrency_controls": True' in runtime
    assert '"api_key_concurrency_controls": True' in runtime
    assert '"managed_request_draft_recovery": True' in runtime
    assert '"persistent_request_draft_ownership": True' in runtime
    assert '"visible_generation_liveness": True' in runtime
    assert '"browser_page_progress_probe": True' in runtime
    assert '"same_api_parallel_requests": True' in runtime
    assert '"failed_route_quarantine": True' in runtime
    assert '"request_controller_lifecycle_guard": True' in runtime
    assert '"chatgpt_transient_retry": True' in runtime
    assert '"linux_worker_initialize": True' in runtime
    assert '"linux_worker_routing_toggle": True' in runtime
    assert '"linux_worker_master_switch": True' in runtime
    assert '"linux_worker_disable_authority": True' in runtime
    assert '"worker_live_occupancy": True' in runtime
    assert '"worker_device_name_column": True' in runtime
    assert '"worker_pairing_rename": True' in runtime
    assert '"worker_presentation_console_liveness_v65": True' in runtime
    assert '"worker_presentation_console_liveness_v66": True' in runtime
    assert '"worker_column_registry_v67": True' in runtime
    assert '"multimodal_upload_confirmation_v64": True' in runtime
    assert '"multimodal_upload_v68": True' in runtime
    assert '"api_key_console_v68": True' in runtime
    assert '"rich_response_v69": True' in runtime
    assert '"request_response_epoch_v69": True' in runtime
    assert '"worker_content_runtime_epoch_v71": True' in runtime
    assert '"linux_worker_proxy_health_facets": True' in runtime
