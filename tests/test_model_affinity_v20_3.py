from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v20_3_patch import _top_affinity, install_v20_3_patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-master-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-password-for-affinity-test",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=30,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def test_top_affinity_counts_model_and_reasoning_combinations() -> None:
    rows = [
        {"requested_model": "gpt-5.6-sol", "diagnostics": {"requested_reasoning": "medium"}},
        {"requested_model": "gpt-5.6-sol", "diagnostics": {"requested_reasoning": "medium"}},
        {"requested_model": "gpt-5.5", "reasoning_effort": "low"},
        {"requested_model": "gpt-5.5", "diagnostics": {"actual_reasoning": "instant"}},
        {"requested_model": "gpt-5.5-mini", "diagnostics": {"effective_reasoning": "instant"}},
        {"requested_model": "gpt-image"},
    ]
    presets = _top_affinity(rows, 2)
    assert presets[0] == {
        "rank": 1,
        "model": "gpt-5.6-sol",
        "reasoning": "medium",
        "count": 2,
        "key": "gpt-5.6-sol:medium",
    }
    assert presets[1]["model"] == "gpt-5.5"
    assert presets[1]["reasoning"] == "instant"
    assert presets[1]["count"] == 2


def test_extension_affinity_endpoint_uses_extension_headers_not_query_token(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    install_v20_3_patch(app)

    with TestClient(app) as client:
        # Add fixtures after lifespan startup so TelemetryStore.load() cannot read the
        # same persisted rows back a second time and inflate the counts.
        app.state.telemetry.items.append({
            "request_id": "req-a",
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "diagnostics": {"requested_reasoning": "high"},
        })
        app.state.telemetry.items.append({
            "request_id": "req-b",
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "diagnostics": {"requested_reasoning": "high"},
        })

        registered = client.post(
            "/api/extensions/register",
            headers={"X-Pairing-Code": "test-pair-code"},
            json={"name": "Affinity Test", "browser_name": "Chrome", "version": "0.7.6", "metadata": {}},
        )
        assert registered.status_code == 200
        credentials = registered.json()

        denied = client.get("/api/extensions/model-affinity")
        assert denied.status_code == 401

        response = client.get(
            "/api/extensions/model-affinity",
            headers={
                "X-Extension-Client-ID": credentials["client_id"],
                "X-Extension-Token": credentials["token"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["interval_seconds"] == 600
        assert payload["history_limit"] == 200
        assert payload["presets"][0]["key"] == "gpt-5.6-sol:high"
        assert payload["presets"][0]["count"] == 2


def test_extension_refreshes_every_ten_minutes_and_prepares_model_reasoning() -> None:
    source = (EXTENSION / "model_affinity_v23.js").read_text(encoding="utf-8")
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    assert "periodInMinutes: 10" in source
    assert "/api/extensions/model-affinity" in source
    assert "?token=" not in source
    assert '"X-Extension-Client-ID"' in source
    assert '"X-Extension-Token"' in source
    assert "chat2api.model.prepare.v5" in source
    assert "chat2api.reasoning.prepare.v7" in source
    assert "chat2api.model.probe.v7" in source
    assert "gpt-5.5-mini" in source
    assert entry.index('"model_affinity_v23.js"') < entry.index('"conversation_warm_pool_v2.js"')


def test_warm_pool_keeps_two_affinity_slots_and_matches_requests_first() -> None:
    source = (EXTENSION / "conversation_warm_pool_v2.js").read_text(encoding="utf-8")
    assert "MAX_WARM_SLOTS = 2" in source
    assert "warmSlots: new Map()" in source
    assert "desiredSlotDefinitions" in source
    assert "history-model-affinity-preselected" in source
    assert "preset_key" in source
    assert "conversation_prewarm_preset_match" in source
    assert '"claim-model-affinity-window"' in source
    assert "scheduleWarm(350, warm.slot_key)" in source
    assert "onAffinityChanged" in source
    assert "conversation_warm_pool_slots: MAX_WARM_SLOTS" in source


def test_entry_installs_v20_3_patch_after_v20_2_and_docs_overlay() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "v20_3_patch.py").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "admin_v20_3.js").read_text(encoding="utf-8")
    assert "from .v20_3_patch import install_v20_3_patch" in source
    assert source.index("install_v20_2_patch(app)") < source.index("install_v20_3_patch(app)")
    assert '"0.20.3"' in patch
    assert "/assets/chat2api-v20-3.js" in patch
    assert "每 10 分钟" in admin
    assert "Server Console · v${VERSION}" in admin
