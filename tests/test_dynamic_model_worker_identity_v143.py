from pathlib import Path

from app import model_capability_routing_patch as routing


class Client:
    def __init__(self, account, models):
        self.metadata = {
            "account_type": account,
            "models": [{"id": model, "capabilities": ["text"]} for model in models],
            "chatgpt_login_state": "ready",
            "chatgpt_login_composer_ready": True,
        }


class Registry:
    def __init__(self):
        self.clients = {
            "free": Client("free", ["gpt-5.5-mini"]),
            "plus": Client("paid", ["gpt-5.6-sol", "gpt-6-astra"]),
        }

    def client_models(self, client_id):
        return self.clients[client_id].metadata["models"]

    def chatgpt_routing_ready(self, client_id):
        return True


def test_live_worker_catalog_is_authority_for_future_models():
    registry = Registry()
    assert routing._compatible(registry, "plus", "gpt-5.6-sol") is True
    assert routing._compatible(registry, "plus", "gpt-6-astra") is True
    assert routing._compatible(registry, "free", "gpt-6-astra") is False


def test_explicit_advertisement_wins_over_stale_free_plan_detection():
    registry = Registry()
    registry.clients["plus"].metadata["account_type"] = "free"
    assert routing._compatible(registry, "plus", "gpt-5.6-sol") is True


def test_paid_legacy_value_normalizes_to_plus():
    registry = Registry()
    assert routing._account_type(registry, "plus") == "plus"


def test_worker_identity_decorator_does_not_mutate_canonical_worker_list():
    source = Path("app/admin_worker_identity_v131.js").read_text(encoding="utf-8")
    assert "version: 144" in source
    assert "decorateAccountTierLabels" not in source
    assert 'data-chat2api-column-key="account_type"' not in source
    assert "extensionDeviceBody" not in source
    assert "pill.textContent" not in source


def test_canonical_worker_list_declares_single_structural_owner():
    source = Path("app/admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'structural_owner: "admin_extension_columns"' in source
    assert "legacy_renderers_bypassed: true" in source


def test_worker_presentation_has_no_autonomous_mutation_observer_loop():
    source = Path("app/admin_worker_presentation_v66.js").read_text(encoding="utf-8")
    assert "MutationObserver" not in source
    assert "setInterval(" not in source
    assert "setTimeout(() => refresh(true), 120)" in source
    assert "setTimeout(() => refresh(true), 900)" in source


def test_request_identity_uses_linux_worker_authority():
    source = Path("app/request_device_identity_patch.py").read_text(encoding="utf-8")
    assert "linux_identity_maps" in source
    assert 'worker.get("extension_client_id")' in source
    assert 'metadata.get("device_name")' in source
    assert 'result["linux_worker_id"]' in source
