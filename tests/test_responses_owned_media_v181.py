"""Responses native transport uses the same archived-media authority as Chat Completions."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from collections import OrderedDict
from types import SimpleNamespace

from fastapi import FastAPI, Request
from PIL import Image

from app.api_keys import ApiPrincipal
from app.media_archive_v173 import MediaArchive
from app.responses_v108_patch import _archive_response_media, _collect, _stream


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (500, 500), (28, 65, 137)).save(out, "PNG")
    return out.getvalue()


def _request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/v1/responses",
        "query_string": b"", "headers": [], "scheme": "https",
        "server": ("chat2api.example", 443),
    })


def _principal() -> ApiPrincipal:
    return ApiPrincipal(key_id="key_A", name="owner", kind="managed", scopes=("chat",))


def test_responses_archived_original_and_thumbnail_from_native_markdown(tmp_path):
    app = FastAPI()
    app.state.media_archive = MediaArchive(tmp_path, "master")
    inline = base64.b64encode(_png()).decode("ascii")
    source = "你好\\n![Generated image](data:image/png;base64," + inline + ")\\n结束"
    text, files = asyncio.run(_archive_response_media(
        app, _request(), _principal(), "resp_req_testabc", source
    ))
    assert len(files) == 1
    assert "data:image/" not in text
    assert "https://chat2api.example/api/media/med_" in text
    assert files[0]["url"] in text
    assert files[0]["thumbnail_url"] not in text
    media_id, token = files[0]["url"].split("/api/media/", 1)[1].split("/")
    assert app.state.media_archive.load(media_id, token)[0] == _png()
    preview, mime = app.state.media_archive.load(media_id, token, thumbnail=True)
    assert mime == "image/webp" and preview.startswith(b"RIFF")
    assert app.state.media_archive.describe(media_id, "key_A")["request_id"] == "resp_req_testabc"


def test_responses_collect_accepts_chat_completed_after_a_snapshot():
    async def run():
        state = SimpleNamespace(queue=asyncio.Queue())
        await state.queue.put({"type": "chat.response.snapshot", "text": "模型内容"})
        await state.queue.put({"type": "chat.completed", "text": "模型内容"})
        return await _collect(state, 1, False)
    text, items = asyncio.run(run())
    assert text == "模型内容" and items == []


def test_responses_stream_defers_image_source_and_completes_with_owned_link(tmp_path):
    app = FastAPI()
    app.state.media_archive = MediaArchive(tmp_path, "master")
    app.state.responses_v108_store = OrderedDict()
    released = []
    app.state.registry = SimpleNamespace(
        busy_clients=set(["ext_a"]),
        send=None,
    )

    async def release(request_id):
        released.append(request_id)

    app.state.broker = SimpleNamespace(release=release)
    inline = base64.b64encode(_png()).decode("ascii")
    original = "前文![Generated image](data:image/png;base64," + inline + ")尾声"

    async def run():
        queue = asyncio.Queue()
        await queue.put({"type": "chat.response.snapshot", "text": original[:30]})
        await queue.put({"type": "chat.response.snapshot", "text": original})
        await queue.put({"type": "chat.completed", "text": original})
        state = SimpleNamespace(queue=queue)
        req = _request()
        async def connected():
            return False
        req.is_disconnected = connected
        output = []
        async for frame in _stream(
            app, req, state, principal=_principal(),
            response_id="resp_test", body={"model": "gpt-6", "store": True},
            prompt="生成一张图片", request_id="resp_req_testabc",
            client_id="ext_a", timeout=3, created_at=int(time.time()),
            include_sources=False,
        ):
            output.append(frame)
        return output

    frames = asyncio.run(run())
    parsed = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    deltas = [v["delta"] for v in parsed if v["type"] == "response.output_text.delta"]
    assert "".join(deltas).startswith("前文")
    assert "data:image/" not in "".join(deltas)
    assert "chat2api.example/api/media/" in "".join(deltas)
    assert "data:image/" not in "".join(frames)
    completed = [item for item in parsed if item["type"] == "response.completed"]
    assert len(completed) == 1
    response = completed[0]["response"]
    assert response["status"] == "completed"
    assert response["chat2api"]["media_files"][0]["thumbnail_url"].endswith("/thumbnail")
    assert response["output_text"] == "".join(deltas)
    assert released == ["resp_req_testabc"]
    assert "ext_a" not in app.state.registry.busy_clients
