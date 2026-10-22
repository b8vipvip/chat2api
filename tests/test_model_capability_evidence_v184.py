"""Evidence-specific capabilities on the single v145/v146 Worker model catalog."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from app import model_capability_routing_patch as routing
from app.model_capability_evidence_v184 import CAPABILITY_EVIDENCE_VERSION
from app.worker_model_library_v145_patch import (
    VALIDATION_REVISION, _public_user_library, install_worker_model_library_v145_patch,
)


def _verified(model, caps=None):
    row = {"id": model, "label": model, "validated": True,
           "validation_revision": VALIDATION_REVISION}
    if caps is not None:
        row["capabilities"] = caps
    return row


class FakeRegistry:
    def __init__(self):
        self.clients = {
            "ext_images": SimpleNamespace(connection_enabled=True, metadata={
                "account_type": "plus",
                "model_validation_state": "validated",
                "model_validation_revision": VALIDATION_REVISION,
                "models": [_verified("gpt-6", ["text", "vision", "file-understanding"])],
                "capabilities": ["text", "vision", "image-generation", "image-reference"],
            }),
            "ext_text": SimpleNamespace(connection_enabled=True, metadata={
                "account_type": "plus",
                "model_validation_state": "validated",
                "model_validation_revision": VALIDATION_REVISION,
                "models": [_verified("gpt-6", ["text"]), _verified("gpt-5.5")],
                "capabilities": ["text"],
            }),
        }
    def online_client_ids(self):
        return list(self.clients)
    def chatgpt_routing_ready(self, client_id):
        return True
    def client_models(self, client_id):
        return []
    def supports_model(self, client_id, model_id):
        return False
    def model_catalog(self, online_only=True):
        return []
    def legacy_minimum_model(self):
        return None
    async def attach(self, client_id, socket):
        return None
    async def register(self, *args, **kwargs):
        return "ext_text", "token"
    async def set_connection_enabled(self, *args, **kwargs):
        return None
    async def save(self):
        return None


def _install():
    reg = FakeRegistry()
    app = FastAPI()
    app.state.registry = reg
    compatible = routing._compatible
    dynamic = routing._is_dynamic_text_model
    install_worker_model_library_v145_patch(app)
    return reg, compatible, dynamic


def _restore(compatible, dynamic):
    routing._compatible = compatible
    routing._is_dynamic_text_model = dynamic


def test_model_capabilities_keep_transport_and_per_worker_proof_separate():
    registry, compatible, dynamic = _install()
    try:
        catalog = {row["id"]: row for row in registry.model_catalog()}
        model = catalog["gpt-6"]
        evidence = model["capability_evidence"]
        assert evidence["revision"] == CAPABILITY_EVIDENCE_VERSION
        assert evidence["authority"] == "worker-model-library-v145"
        assert evidence["model_picker_validated"] is True
        assert evidence["legacy_transport_alias"] is False
        assert model["clients"] == ["ext_images", "ext_text"]
        assert model["capabilities"] == ["file-understanding", "text", "vision"]
        ops = evidence["operations"]
        assert ops["text_input"] == {
            "state": "picker_validated", "workers": ["ext_images", "ext_text"],
            "evidence": "model_picker_validation",
        }
        assert ops["image_input"] == {
            "state": "model_reported", "workers": ["ext_images"],
            "evidence": "worker_model_capabilities",
        }
        assert ops["file_input"]["workers"] == ["ext_images"]
        assert ops["image_generation"]["workers"] == ["ext_images"]
        assert ops["image_generation"]["state"] == "transport_advertised"
        assert ops["image_generation"]["model_specific_verified"] is False
        assert ops["image_edit"]["state"] == "transport_advertised"
        assert ops["image_edit"]["model_specific_verified"] is False

        older = catalog["gpt-5.5"]
        assert older["capabilities"] == ["text"]
        assert older["capability_evidence"]["operations"]["image_input"]["state"] == "unverified"
        assert older["capability_evidence"]["operations"]["image_generation"]["state"] == "unverified"
        assert older["capability_evidence"]["operations"]["text_input"]["workers"] == ["ext_text"]
        assert "image-generation" not in model["capabilities"]

        legacy = catalog["gpt-image"]["capability_evidence"]
        assert legacy["legacy_transport_alias"] is True
        assert legacy["model_picker_validated"] is False
        assert legacy["operations"]["text_input"]["state"] == "not_applicable"

        # The ordinary user catalog may disclose evidence and counts, but not
        # transport extension IDs or an admin's live Worker inventory.
        public = {row["model_id"]: row for row in _public_user_library(registry, {"data": []})}
        exposed = public["gpt-6"]["capability_evidence"]
        assert exposed["operations"]["image_generation"]["worker_count"] == 1
        assert exposed["operations"]["image_generation"]["state"] == "transport_advertised"
        assert "ext_images" not in str(exposed)
    finally:
        _restore(compatible, dynamic)


def test_reconnect_invalidates_picker_proof_but_not_global_transport_support():
    registry, compatible, dynamic = _install()
    try:
        # Model-specific rows cannot be reused after the Worker moves to
        # pending, even if the extension still advertises image-generation.
        metadata = registry.clients["ext_images"].metadata
        metadata.update(model_validation_state="pending", models=[])
        catalog = {row["id"]: row for row in registry.model_catalog()}
        gpt6 = catalog["gpt-6"]
        assert gpt6["clients"] == ["ext_text"]
        assert gpt6["capability_evidence"]["operations"]["image_generation"]["state"] == "unverified"
        assert catalog["gpt-image"]["capability_evidence"]["operations"]["image_generation"]["workers"] == ["ext_images"]
    finally:
        _restore(compatible, dynamic)


def test_unverified_free_default_does_not_claim_picker_validation_or_image_operations():
    registry, compatible, dynamic = _install()
    try:
        free = SimpleNamespace(connection_enabled=True, metadata={
            "account_type": "free",
            "account_detection_confidence": "high",
            "account_model_control_present": False,
            "model_validation_state": "failed", "models": [],
            "capabilities": ["text", "image-generation"],
        })
        registry.clients = {"ext_free": free}
        registry.legacy_minimum_model = lambda: "gpt-6"
        model = {row["id"]: row for row in registry.model_catalog()}["gpt-6"]
        evidence = model["capability_evidence"]
        assert model["validated"] is False
        assert evidence["model_validation_state"] == "compatibility_unverified"
        assert evidence["model_picker_validated"] is False
        assert evidence["operations"]["text_input"]["state"] == "unverified"
        assert evidence["operations"]["image_input"]["state"] == "unverified"
        assert evidence["operations"]["image_generation"]["state"] == "transport_advertised"
        assert evidence["operations"]["image_generation"]["model_specific_verified"] is False
    finally:
        _restore(compatible, dynamic)


def test_final_v147_catalog_keeps_one_evidence_projection_for_unverified_candidates(tmp_path):
    from fastapi import Depends
    from app.worker_text_model_management_v146_patch import install_worker_text_model_management_v146_patch
    from app.worker_model_tasks_v147_patch import install_manual_worker_text_model_tasks_v147_patch

    registry = FakeRegistry()
    registry.clients["ext_discovered"] = SimpleNamespace(connection_enabled=True, name="Discovery", metadata={
        "model_validation_state": "discovered",
        "model_validation_revision": VALIDATION_REVISION,
        "model_observed_choices": [{"id": "gpt-6", "selectable": True}],
        "capabilities": ["text", "image-generation"],
    })

    async def touch(cid, metadata=None):
        registry.clients[cid].metadata.update(metadata or {})
    async def send(cid, payload):
        return None
    registry.touch = touch
    registry.send = send
    app = FastAPI()
    app.state.registry = registry
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    @app.get("/api/admin/overview", dependencies=[Depends(lambda: True)])
    def overview():
        return {"ok": True}

    compatible = routing._compatible
    dynamic = routing._is_dynamic_text_model
    try:
        install_worker_model_library_v145_patch(app)
        install_worker_text_model_management_v146_patch(app)
        install_manual_worker_text_model_tasks_v147_patch(app)
        catalog = {row["id"]: row for row in registry.model_catalog()}
        selected = catalog["gpt-6"]
        assert set(selected["clients"]) == {"ext_images", "ext_text", "ext_discovered"}
        evidence = selected["capability_evidence"]
        assert evidence["operations"]["text_input"]["workers"] == ["ext_images", "ext_text"]
        assert evidence["operations"]["image_input"]["workers"] == ["ext_images"]
        assert evidence["operations"]["image_generation"]["workers"] == ["ext_discovered", "ext_images"]
        assert evidence["operations"]["image_generation"]["model_specific_verified"] is False
        from fastapi.testclient import TestClient
        result = TestClient(app).get("/api/admin/text-model-tasks")
        assert result.status_code == 200
        payload = result.json()
        assert payload["capability_evidence"]["gpt-6"] == evidence
        assert registry.supports_model("ext_discovered", "gpt-6")
        registry.clients["ext_discovered"].metadata["model_validation_state"] = "pending"
        updated = {row["id"]: row for row in registry.model_catalog()}["gpt-6"]
        assert "ext_discovered" not in updated["clients"]
        assert updated["capability_evidence"]["operations"]["image_generation"]["workers"] == ["ext_images"]
    finally:
        _restore(compatible, dynamic)
