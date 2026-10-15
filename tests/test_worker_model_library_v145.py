from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from app.worker_model_library_v145_patch import (
    VALIDATION_REVISION,
    _mark_pending,
    _free_default_composer_eligible,
    _configured_default_model,
    _free_default_route,
    _validated_model_rows,
    _selectable_observed_model_ids,
)


def _registry(metadata: dict):
    client = SimpleNamespace(metadata=dict(metadata))
    return SimpleNamespace(clients={"ext_test": client})


def test_validated_rows_require_fresh_validation_state_and_revision():
    row = {
        "id": "gpt-6-astra",
        "label": "GPT-6 Astra",
        "validated": True,
        "validation_revision": VALIDATION_REVISION,
        "capabilities": ["text"],
    }
    pending = _registry({
        "model_validation_state": "pending",
        "model_validation_revision": VALIDATION_REVISION,
        "models": [row],
    })
    assert _validated_model_rows(pending, "ext_test") == []

    stale = _registry({
        "model_validation_state": "validated",
        "model_validation_revision": VALIDATION_REVISION - 1,
        "models": [row],
    })
    assert _validated_model_rows(stale, "ext_test") == []

    fresh = _registry({
        "model_validation_state": "validated",
        "model_validation_revision": VALIDATION_REVISION,
        "models": [row],
    })
    assert [item["id"] for item in _validated_model_rows(fresh, "ext_test")] == ["gpt-6-astra"]


def test_only_worker_validated_normal_models_survive_catalog_filter():
    registry = _registry({
        "model_validation_state": "validated",
        "model_validation_revision": VALIDATION_REVISION,
        "models": [
            {"id": "gpt-6-astra", "validated": True, "validation_revision": VALIDATION_REVISION},
            {"id": "gpt-5.6-sol", "validated": False, "validation_revision": VALIDATION_REVISION},
            {"id": "gpt-image", "validated": True, "validation_revision": VALIDATION_REVISION},
            {"id": "default", "validated": True, "validation_revision": VALIDATION_REVISION},
        ],
    })
    assert [item["id"] for item in _validated_model_rows(registry, "ext_test")] == ["gpt-6-astra"]


def test_connect_enable_bind_boundary_clears_old_model_authority():
    registry = _registry({
        "model_validation_state": "validated",
        "model_validation_revision": VALIDATION_REVISION,
        "current_model": "gpt-6-astra",
        "models": [
            {"id": "gpt-6-astra", "validated": True, "validation_revision": VALIDATION_REVISION},
        ],
    })
    _mark_pending(registry, "ext_test", "connect")
    metadata = registry.clients["ext_test"].metadata
    assert metadata["models"] == []
    assert metadata["current_model"] is None
    assert metadata["model_validation_state"] == "pending"
    assert metadata["model_validation_trigger"] == "connect"


def test_worker_scripts_have_no_static_normal_model_catalog_fallback():
    root = Path(__file__).resolve().parents[1] / "chrome_extension"
    routing = (root / "model_routing_v2.js").read_text(encoding="utf-8")
    contract = (root / "model_contract_v25.js").read_text(encoding="utf-8")
    account = (root / "background_account_v20.js").read_text(encoding="utf-8")
    background = (root / "background_model_library_v145.js").read_text(encoding="utf-8")

    assert "STATIC_MODELS" not in routing
    assert "rows.push({\n        id: MINI_MODEL" not in contract
    assert "base.models = [{ ...MINI_MODEL }]" not in account
    assert "modelValidationRuntimeNonce" in background
    assert "sendValidatedExtensionStatusV145" in background


def test_worker_model_discovery_requires_explicit_administrator_task():
    root = Path(__file__).resolve().parents[1] / "chrome_extension"
    background = (root / "background_model_library_v145.js").read_text(encoding="utf-8")
    for automatic_trigger in (
        'schedule("worker-restart"',
        'schedule("worker-connect"',
        'schedule("worker-bind"',
        'schedule("worker-enable"',
        'schedule("model-validation-retry"',
    ):
        assert automatic_trigger not in background
    assert 'message?.type !== "model.task.v147"' in background
    assert "chat2api.models.validate.v145" in background
    assert "chat2api.model.select.v145" in (root / "content_model_library_v145.js").read_text(encoding="utf-8")


def test_free_default_mini_route_requires_ready_composer_and_reliable_plan() -> None:
    def r(meta, ready=True):
        client = SimpleNamespace(metadata=meta, connection_enabled=True)
        return SimpleNamespace(clients={"ext_test": client}, chatgpt_routing_ready=lambda _: ready)

    details = {
        "account_type": "free",
        "account_detection_confidence": "high",
        "account_model_control_present": False,
        "chatgpt_login_state": "ready",
        "chatgpt_login_composer_ready": True,
        "model_validation_state": "failed",
        "model_validation_revision": VALIDATION_REVISION,
        "models": [],
    }
    assert _free_default_composer_eligible(r(details), "ext_test")
    assert not _free_default_composer_eligible(r(details, False), "ext_test")
    assert not _free_default_composer_eligible(r({**details, "account_type": "paid"}), "ext_test")
    assert not _free_default_composer_eligible(r({**details, "account_detection_confidence": "low"}), "ext_test")
    assert not _free_default_composer_eligible(r({**details, "account_model_control_present": True}), "ext_test")
    assert _validated_model_rows(r(details), "ext_test") == []

    configured = r(details)
    configured.legacy_minimum_model = lambda: "gpt-5.6-sol"
    assert _configured_default_model(configured) == "gpt-5.6-sol"
    assert _free_default_route(configured, "ext_test", "gpt-5.6-sol")
    assert not _free_default_route(configured, "ext_test", "gpt-5.5-mini")
    configured.legacy_minimum_model = lambda: "gpt-6"
    assert _free_default_route(configured, "ext_test", "gpt-6")
    configured.legacy_minimum_model = lambda: None
    assert not _free_default_route(configured, "ext_test", "gpt-6")


def test_browser_free_compatibility_does_not_fake_validated_model_evidence() -> None:
    root = Path(__file__).resolve().parents[1] / "chrome_extension"
    source = (root / "model_routing_v2.js").read_text(encoding="utf-8")
    assert 'FREE_DEFAULT_MINI' not in source
    assert 'serverFreeDefaultRoute' in source
    assert 'await freeDefaultComposerEligible()' in source
    assert 'model_selection_validated: !freeDefaultCompat' in source
    assert 'effective_model: freeDefaultCompat ? null : requestedModel' in source
    assert '"free-default-compatibility-unverified"' in source
    assert "await selectValidatedModel(tab.id, requestedModel)" in source


def test_free_default_mini_browser_integration() -> None:
    import shutil
    import subprocess
    if shutil.which("node") is None:
        return
    path = Path(__file__).resolve().parents[1] / "tests" / "free_mini_without_picker_v145.mjs"
    run = subprocess.run(["node", str(path)], capture_output=True, text=True, check=False, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


def test_picker_observations_are_candidates_not_served_model_proof():
    registry = _registry({
        "model_validation_state": "discovered",
        "model_validation_revision": VALIDATION_REVISION,
        "models": [],
        "model_observed_choices": [
            {"id": "gpt-6", "selectable": True, "validated": False},
            {"id": "gpt-5.6-sol", "selectable": True, "validated": False},
            {"id": "gpt-5.5", "selectable": False, "validated": False},
        ],
    })
    assert _selectable_observed_model_ids(registry, "ext_test") == {"gpt-6", "gpt-5.6-sol"}
    assert _validated_model_rows(registry, "ext_test") == []
    _mark_pending(registry, "ext_test", "reconnect")
    assert _selectable_observed_model_ids(registry, "ext_test") == set()
