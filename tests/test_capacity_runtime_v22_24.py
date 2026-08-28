from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_bridge_082_busts_mv3_script_cache_without_touching_login_state():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")

    assert manifest["version"] == "0.8.5"
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
    assert 'SERVER_RUNTIME_VERSION = "0.22.29"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.5"' in runtime
    assert '"bundle_version": CHROME_BRIDGE_BUNDLE_VERSION' in runtime
    assert 'request-hygiene-v42-persistent-draft-ownership-v43-generation-liveness-v42' in runtime
    assert '"bridge_service_worker_cache_bust": True' in runtime
    assert '"rendered_response_capture_recovery": True' in runtime
    assert '"managed_request_draft_recovery": True' in runtime
    assert '"persistent_request_draft_ownership": True' in runtime
    assert '"visible_generation_liveness": True' in runtime
    assert '"linux_worker_initialize": True' in runtime
    assert '"linux_worker_routing_toggle": True' in runtime
