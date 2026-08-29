from __future__ import annotations

import asyncio
import base64
import struct
from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.models import PlaygroundRunRequest
from app.playground_lifecycle_patch import install_playground_lifecycle_patch
from app.playground_multimodal_defaults_patch import (
    default_file_bytes,
    default_vision_png,
    install_playground_multimodal_defaults_patch,
)


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


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, file_id: str) -> None:
        self.file_id = file_id

    def json(self) -> dict[str, str]:
        return {"id": self.file_id}


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict, dict]] = []

    async def post(self, path: str, *, headers: dict, json: dict):
        self.posts.append((path, headers, json))
        return FakeResponse(f"file_default_{len(self.posts)}")


def make_manager(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_playground_lifecycle_patch(app)
    install_playground_multimodal_defaults_patch(app)
    return app, app.state.playground_run_manager


def request(test_type: str) -> PlaygroundRunRequest:
    return PlaygroundRunRequest(
        test_type=test_type,
        model="gpt-5.5-mini",
        api_key="sk-chat2api-test",
        files=[],
    )


def test_default_vision_sample_is_real_png_and_default_file_has_marker() -> None:
    png = default_vision_png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (96, 64)
    assert b"FILE-UNDERSTANDING-742" in default_file_bytes()


def test_empty_vision_and_file_runs_receive_real_request_ids(tmp_path: Path) -> None:
    _app, manager = make_manager(tmp_path)
    for kind in ("vision", "file"):
        planned = manager._planned_request_ids(request(kind), [kind])
        assert planned[kind].startswith("req_")


async def _exercise_kind(tmp_path: Path, kind: str):
    _app, manager = make_manager(tmp_path)
    client = FakeClient()
    body = request(kind)
    planned = manager._planned_request_ids(body, [kind])
    captured: dict = {}

    async def fake_run_chat(client_arg, **kwargs):
        captured.update(kwargs)
        return {
            "kind": kind,
            "label": kind,
            "status": "passed",
            "message": "调用完成",
            "request_id": kwargs["request_id"],
        }

    manager._run_chat = fake_run_chat
    uploaded: list[dict] = []
    result = await manager._run_kind(
        client,
        kind=kind,
        body=body,
        token="business-key",
        request_id=planned[kind],
        uploaded=uploaded,
    )
    return client, uploaded, captured, result


def test_empty_vision_run_generates_sample_and_dispatches_normal_mini_request(tmp_path: Path) -> None:
    client, uploaded, captured, result = asyncio.run(_exercise_kind(tmp_path, "vision"))
    assert result["status"] == "passed"
    assert result["sample_source"] == "generated-default"
    assert result["sample_filename"] == "chat2api-default-vision.png"
    assert captured["model"] == "gpt-5.5-mini"
    assert captured["attachment"]["id"].startswith("file_default_")
    assert uploaded[0]["mime_type"] == "image/png"
    assert client.posts[0][0] == "/v1/files"
    assert base64.b64decode(client.posts[0][2]["data_base64"]).startswith(b"\x89PNG")


def test_empty_file_run_generates_sample_and_dispatches_normal_mini_request(tmp_path: Path) -> None:
    client, uploaded, captured, result = asyncio.run(_exercise_kind(tmp_path, "file"))
    assert result["status"] == "passed"
    assert result["sample_source"] == "generated-default"
    assert result["sample_filename"] == "chat2api-default-file.txt"
    assert captured["model"] == "gpt-5.5-mini"
    assert captured["attachment"]["id"].startswith("file_default_")
    assert uploaded[0]["mime_type"] == "text/plain"
    text = base64.b64decode(client.posts[0][2]["data_base64"]).decode("utf-8")
    assert "FILE-UNDERSTANDING-742" in text


def test_generated_vision_sample_does_not_leak_into_image_generation(tmp_path: Path) -> None:
    _app, manager = make_manager(tmp_path)
    client = FakeClient()
    body = request("all")
    planned = manager._planned_request_ids(body, manager._kinds("all"))
    uploaded: list[dict] = []

    async def fake_run_chat(client_arg, **kwargs):
        return {"kind": "vision", "label": "vision", "status": "passed", "message": "ok", "request_id": kwargs["request_id"]}

    captured: dict = {}

    async def fake_run_image(client_arg, **kwargs):
        captured.update(kwargs)
        return {"kind": "image_generation", "label": "image", "status": "passed", "message": "ok", "request_id": kwargs["request_id"]}

    manager._run_chat = fake_run_chat
    manager._run_image = fake_run_image
    awaitable = manager._run_kind(
        client,
        kind="vision",
        body=body,
        token="business-key",
        request_id=planned["vision"],
        uploaded=uploaded,
    )
    asyncio.run(awaitable)
    asyncio.run(
        manager._run_kind(
            client,
            kind="image_generation",
            body=body,
            token="business-key",
            request_id=planned["image_generation"],
            uploaded=uploaded,
        )
    )
    assert captured["attachment"] is None


def test_production_entry_installs_defaults_after_playground_lifecycle() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_playground_multimodal_defaults_patch(app)" in entry
    assert entry.index("install_playground_lifecycle_patch(app)") < entry.index("install_playground_multimodal_defaults_patch(app)")
    assert entry.index("install_playground_multimodal_defaults_patch(app)") < entry.index("install_mini_multimodal_quota_patch(app)")
