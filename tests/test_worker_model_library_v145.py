from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from app.worker_model_library_v145_patch import (
    VALIDATION_REVISION,
    _mark_pending,
    _validated_model_rows,
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


def test_worker_validation_runs_on_required_lifecycle_boundaries():
    root = Path(__file__).resolve().parents[1] / "chrome_extension"
    background = (root / "background_model_library_v145.js").read_text(encoding="utf-8")
    for trigger in ("worker-restart", "worker-connect", "worker-bind", "worker-enable"):
        assert trigger in background
    assert "chat2api.models.validate.v145" in background
    assert "chat2api.model.select.v145" in (root / "content_model_library_v145.js").read_text(encoding="utf-8")
