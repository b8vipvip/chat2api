from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
from app.v12_patch import install_v12_patch
from app.v13_patch import install_v13_patch
from app.v14_patch import install_v14_patch
from app.v15_patch import install_v15_patch
from app.v16_patch import install_v16_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def app_v16(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    install_v13_patch(app)
    install_v14_patch(app)
    install_v15_patch(app)
    install_v16_patch(app)
    return app


def test_v16_health_overview_and_admin_include_model_plaza(tmp_path: Path) -> None:
    with TestClient(app_v16(tmp_path)) as client:
        health = client.get("/healthz").json()
        assert health["version"] == "0.16.0"

        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.16.0"
        assert overview["capabilities"]["model_plaza"] is True
        assert overview["capabilities"]["model_plaza_live_catalog"] is True
        assert overview["capabilities"]["model_plaza_search_and_filters"] is True

        html = client.get("/admin").text
        assert '/assets/chat2api-v16.js' in html
        script = client.get("/assets/chat2api-v16.js")
        assert script.status_code == 200
        assert "模型广场" in script.text
        assert "Server Console · v${VERSION}" in script.text


def test_model_plaza_covers_all_published_model_ids_and_live_catalog() -> None:
    source = (ROOT / "app" / "admin_v16.js").read_text(encoding="utf-8")
    for model_id in ("gpt-5.6-sol", "gpt-5.5", "gpt-image", "gpt-live", "gpt-live-mini"):
        assert f'"{model_id}"' in source
    assert 'fetch("/v1/models"' in source
    assert 'Authorization: `Bearer ${token}`' in source
    assert 'data-filter="text"' in source
    assert 'data-filter="image"' in source
    assert 'data-filter="audio"' in source
    assert 'id="modelPlazaSearch"' in source
    assert 'data-copy-model' in source
    assert 'data-copy-sample' in source
    assert "default_reasoning_effort" in source
    assert 'meta.category === "text" ? "medium"' in source


def test_model_plaza_does_not_invent_unsupported_commercial_metadata() -> None:
    source = (ROOT / "app" / "admin_v16.js").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    assert "不虚构价格、上下文窗口或速率限制" in source
    assert "价格、上下文窗口、吞吐、限速、基准分数" in docs
    assert "不得编造" in docs


def test_model_plaza_documents_dynamic_status_and_responsive_cards() -> None:
    source = (ROOT / "app" / "admin_v16.js").read_text(encoding="utf-8")
    assert "当前可用" in source
    assert "未在线" in source
    assert "modelPlazaGrid" in source
    assert "grid-template-columns:repeat(2" in source
    assert "@media(max-width:1050px)" in source
    assert "modelPlazaGrid{grid-template-columns:1fr}" in source


def test_current_patch_keeps_reasoning_default_and_request_log_version(tmp_path: Path) -> None:
    with TestClient(app_v16(tmp_path)) as client:
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["capabilities"]["default_reasoning_effort"] == "medium"
        catalog = client.get("/v1/models", headers=headers()).json()["data"]
        text_models = {row["id"]: row for row in catalog if row["id"] in {"gpt-5.6-sol", "gpt-5.5"}}
        assert text_models["gpt-5.6-sol"]["default_reasoning_effort"] == "medium"
        assert text_models["gpt-5.5"]["default_reasoning_effort"] == "medium"


def test_production_entry_installs_v16_after_v15() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v16_patch import install_v16_patch" in source
    assert source.index("install_v15_patch(app)") < source.index("install_v16_patch(app)")
