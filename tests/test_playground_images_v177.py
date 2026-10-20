from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_archived_generated_picture_is_admin_only_and_request_scoped(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="test-key", CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    with TestClient(app) as client:
        import asyncio

        async def seed() -> str:
            item = await app.state.file_store.create(
                filename="generated.png",
                data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\nPNGTEST").decode("ascii"),
                mime_type="image/png",
                owner_key_id="master",
                purpose="generated-output",
            )
            await app.state.telemetry.upsert({
                "request_id": "imgreq_testmedia0123456789",
                "status": "completed",
                "request_type": "image_generation",
                "response_media": [{"file_id": item.file_id, "mime_type": "image/png", "bytes": item.size}],
                "response_media_count": 1,
                "response_text": "已生成图片（在查看对话中预览）",
            })
            return item.file_id

        file_id = asyncio.run(seed())
        url = f"/api/admin/requests/imgreq_testmedia0123456789/media/{file_id}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer bad-key"}).status_code == 401
        assert client.get(
            f"/api/admin/requests/imgreq_other00000000/media/{file_id}",
            headers={"Authorization": "Bearer test-key"},
        ).status_code == 404
        good = client.get(url, headers={"Authorization": "Bearer test-key"})
        assert good.status_code == 200
        assert good.headers["content-type"] == "image/png"
        assert good.headers["cache-control"] == "private, no-store"
        assert good.content.startswith(b"\x89PNG")
        row = app.state.telemetry.get("imgreq_testmedia0123456789")
        assert row is not None and row["response_media_count"] == 1
        assert "base64" not in (tmp_path / "request_history.jsonl").read_text(encoding="utf-8")
        assert "PNGTEST" not in (tmp_path / "request_history.jsonl").read_text(encoding="utf-8")


def test_request_history_source_renders_markdown_and_uses_authenticated_image_fetch() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "requestHistoryMediaUrls" in source
    assert "requestHistoryRevokeMedia" in source
    assert "renderMarkdown(actualReply)" in source
    assert "response_media" in source
    assert "Authorization:" in source
    assert 'image/png' in source
