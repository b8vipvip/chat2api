from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_disable_is_blocked_while_broker_owns_active_requests() -> None:
    server = read("app/worker_disable_authority_patch.py")
    extension = read("chrome_extension/background_worker_master_switch_v61.js")
    assert "def active_request_ids(client_id: str)" in server
    assert "require_disable_lease(client_id)" in server
    assert "worker_active_request_lease" in server
    assert "activeRequestLease()" in extension
    assert "active_request_disable_lease_revision: 79" in extension
    assert "Worker has active requests; disable is blocked" in extension


def test_composer_clear_is_not_submission_confirmation() -> None:
    request = read("chrome_extension/content_request_v6.js")
    assert 'reason: "composer-cleared"' not in request
    assert "active.attachmentCount > 0 ? 45000 : 20000" in request
    assert "submission_liveness_revision: 79" in request
    assert "did not expose an accepted user turn, generation state, or response" in request


def test_multimodal_waits_for_upload_processing_to_settle() -> None:
    multimodal = read("chrome_extension/content_multimodal_v78.js")
    assert "waitForUploadSettled" in multimodal
    assert "upload_settle_revision: 79" in multimodal
    assert "upload/processing did not settle before timeout" in multimodal
    assert "await delay(550)" not in multimodal


def test_v02255_release_contract() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = read("chrome_extension/manifest.json")
    assert 'SERVER_RUNTIME_VERSION = "0.22.59"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.27"' in runtime
    assert '"version": "0.8.27"' in manifest
    assert '"active_request_disable_lease_v79": True' in runtime
    assert '"multimodal_upload_settle_v79": True' in runtime
    assert '"submission_liveness_v79": True' in runtime
