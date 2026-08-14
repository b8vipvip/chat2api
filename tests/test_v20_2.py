from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v20_2_patch import install_v20_2_patch


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="legacy-migration-only",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def test_v20_2_reports_current_server_version_and_live_alias_metadata(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    install_v20_2_patch(app)
    catalog = {row["id"]: row for row in app.state.registry.model_catalog(online_only=False)}
    assert catalog["gpt-live"]["realtime"]["endpoint"] == "/v1/audio/realtime"
    assert catalog["gpt-live"]["realtime"]["protocol"] == "chat2api-live-v1"
    assert catalog["gpt-live-mini"]["alias_of"] == "gpt-live"
    assert catalog["gpt-live-mini"]["realtime"]["effective_model"] == "gpt-live"
    assert catalog["gpt-live-mini"]["realtime"]["performance_difference_guaranteed"] is False

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["version"] == "0.20.2"


def test_v20_2_admin_copy_is_explicit_about_live_mini_alias() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "admin_v20_2.js").read_text(encoding="utf-8")
    docs = (root / "docs" / "REALTIME_VOICE.md").read_text(encoding="utf-8")
    entry = (root / "app" / "entry.py").read_text(encoding="utf-8")
    assert "兼容别名" in script
    assert "不承诺更轻量、更快、更低成本" in script
    assert "/v1/audio/realtime/sessions" in script
    assert "chat2api-live-v1" in docs
    assert "gpt-live-mini" in docs and "gpt-live" in docs
    assert "install_v20_2_patch(app)" in entry
