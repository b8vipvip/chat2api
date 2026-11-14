"""Native Responses image/file input must actually reach the Worker, not be dropped."""
from __future__ import annotations

import asyncio
import base64
import io
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from PIL import Image

from app.api_keys import ApiPrincipal
from app.file_store import FileStore
from app.media_archive_v173 import MediaArchive
from app.responses_multimodal_input_v183 import (
    attachment_specs, needs_multimodal, resolve_responses_attachments,
)
from app.responses_v108_patch import _dispatch
from app.responses_image_tool_v182 import handle_image_tool


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 96), (40, 60, 110)).save(buffer, "PNG")
    return buffer.getvalue()


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/v1/responses",
                    "scheme": "https", "server": ("chat2api.example", 443),
                    "query_string": b"", "headers": [],
                    "chat2api.responses_trace": {"worker_attempt_ids": []}})


def _key(value: str = "key-a") -> ApiPrincipal:
    return ApiPrincipal(key_id=value, name=value, kind="managed", scopes=("chat", "images", "files"))


def _stores(tmp_path: Path):
    return FileStore(tmp_path), MediaArchive(tmp_path, "master")


def test_standard_data_image_is_stored_and_authenticated_and_marked_multimodal(tmp_path: Path):
    files, archive = _stores(tmp_path)
    body = {"model": "gpt-6", "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "描述照片"},
        {"type": "input_image", "image_url": "data:image/png;base64," +
         base64.b64encode(_png()).decode("ascii"), "detail": "high"},
    ]}]}
    assert needs_multimodal(body)
    specs = asyncio.run(resolve_responses_attachments(body, _request(), files, archive, _key()))
    assert len(specs) == 1
    assert specs[0]["mime_type"] == "image/png"
    saved, payload = files.read(specs[0]["file_id"])
    assert saved.owner_key_id == "key-a" and payload == _png()
    with pytest.raises(HTTPException) as denied:
        attachment_specs(files, [specs[0]["file_id"]], _key("key-b"))
    assert denied.value.status_code == 403


def test_file_id_and_file_data_use_same_owner_boundary(tmp_path: Path):
    files, archive = _stores(tmp_path)
    existing = asyncio.run(files.create(
        filename="photo.png", data_base64=base64.b64encode(_png()).decode(),
        mime_type="image/png", owner_key_id="key-a",
    ))
    data = base64.b64encode(b"%PDF-1.4\nhello").decode()
    body = {"input": [{"role": "user", "content": [
        {"type": "input_image", "file_id": existing.file_id},
        {"type": "input_file", "filename": "notes.pdf", "file_data": data},
    ]}]}
    specs = asyncio.run(resolve_responses_attachments(body, _request(), files, archive, _key()))
    assert specs[0]["file_id"] == existing.file_id
    assert specs[1]["mime_type"] == "application/pdf"
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(resolve_responses_attachments(body, _request(), files, archive, _key("key-b")))
    assert forbidden.value.status_code == 403


def test_archived_original_is_reusable_as_input_without_fetching_remote_url(tmp_path: Path):
    files, archive = _stores(tmp_path)
    owned = archive.store_image(_png(), "image/png", "key-a", "req_original")
    original = f"https://chat2api.example/api/media/{owned['id']}/{owned['token']}"
    body = {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": original},
    ]}]}
    specs = asyncio.run(resolve_responses_attachments(body, _request(), files, archive, _key()))
    assert files.read(specs[0]["file_id"])[1] == _png()
    with pytest.raises(HTTPException) as denied:
        asyncio.run(resolve_responses_attachments(body, _request(), files, archive, _key("key-b")))
    assert denied.value.status_code == 403
    for bad_url in (
        "https://evil.example/x.png",
        "http://127.0.0.1/private",
        original + "?admin=true",
        original.replace(owned["token"], "invalid-token"),
    ):
        with pytest.raises((ValueError, HTTPException)):
            asyncio.run(resolve_responses_attachments(
                {"input": [{"type": "input_image", "image_url": bad_url}]},
                _request(), files, archive, _key(),
            ))


def test_empty_or_excessive_image_part_is_rejected(tmp_path: Path):
    files, archive = _stores(tmp_path)
    bad = {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,YWJj"}]}]}
    with pytest.raises(ValueError, match="raster image"):
        asyncio.run(resolve_responses_attachments(bad, _request(), files, archive, _key()))
    too_many = {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,YWJj"}
        for _ in range(5)
    ]}]}
    with pytest.raises(ValueError, match="four"):
        asyncio.run(resolve_responses_attachments(too_many, _request(), files, archive, _key()))


def test_responses_dispatch_transfers_canonical_attachments_to_one_worker(tmp_path: Path):
    files, archive = _stores(tmp_path)
    stored = asyncio.run(files.create(filename="image.png",
        data_base64=base64.b64encode(_png()).decode(), mime_type="image/png",
        owner_key_id="key-a"))
    specs = attachment_specs(files, [stored.file_id], _key())
    events = []
    registry = SimpleNamespace(
        set_routing_key=lambda key: None, resolve_client=lambda requested: "ext_ready",
        busy_clients=set(),
    )
    async def send(client_id, message):
        events.append((client_id, message))
    registry.send = send
    async def create(request_id, client_id):
        return SimpleNamespace(request_id=request_id, client_id=client_id)
    app = SimpleNamespace(state=SimpleNamespace(registry=registry,
        broker=SimpleNamespace(create=create),
        settings=SimpleNamespace(request_timeout_seconds=600)))
    state, request_id, client, timeout = asyncio.run(_dispatch(
        app, _request(), {"model":"gpt-6"}, _key(), "描述图片", [],
        attachments=specs,
    ))
    assert len(events) == 1
    assert events[0][1]["attachments"] == specs
    assert events[0][1]["options"]["model"] == "gpt-6"
    assert request_id.startswith("resp_req_")
    assert client == "ext_ready"
    assert timeout == 600
    assert registry.busy_clients == {"ext_ready"}


def test_image_generation_tool_passes_image_reference_into_same_existing_executor(tmp_path: Path):
    files, archive = _stores(tmp_path)
    records = []
    async def generate(**kwargs):
        records.append(kwargs)
        return {"data": [{"b64_json": base64.b64encode(_png()).decode()}],
                "chat2api": {"request_id": kwargs["x_chat2api_request_id"], "owned_media": []}}
    app = SimpleNamespace(state=SimpleNamespace(
        file_store=files, media_archive=archive, image_generation_executor=generate,
        responses_v108_store=OrderedDict(),
    ))
    body = {"model": "gpt-6", "input": [{"role":"user","content":[
        {"type":"input_text","text":"以此图片为参考生成插画"},
        {"type":"input_image","image_url":"data:image/png;base64," +
            base64.b64encode(_png()).decode()},
    ]}], "tools":[{"type":"image_generation"}]}
    result = asyncio.run(handle_image_tool(app, _request(), body, _key(), "以此图片为参考生成插画"))
    assert result.status_code == 200
    assert len(records) == 1
    references = records[0]["body"].attachments
    assert len(references) == 1
    assert files.read(references[0].file_id)[1] == _png()
