"""Regression tests for original-image delivery and a single archival SSE gate."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.media_handoff import StreamMediaMarkerGate


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1254, 1254), (42, 61, 90)).save(out, format="PNG")
    return out.getvalue()


def test_owned_media_metadata_is_private_and_both_original_and_thumb_are_served(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    with TestClient(app) as client:
        owner, owner_token = asyncio.run(app.state.api_keys.create("owner"))
        another, other_token = asyncio.run(app.state.api_keys.create("another"))
        row = app.state.media_archive.store_image(
            _png(), "image/png", owner["key_id"], "req_example",
        )
        metadata_path = f"/v1/media/{row['id']}"
        headers = {"Authorization": "Bearer " + owner_token}
        unauth = client.get(metadata_path)
        assert unauth.status_code == 401
        foreign = client.get(metadata_path, headers={"Authorization": "Bearer " + other_token})
        assert foreign.status_code == 404
        # A Key without image or chat scope must never reveal bearer-capability URLs.
        app.state.api_keys.keys[another["key_id"]].scopes = ["models"]
        forbidden = client.get(metadata_path, headers={"Authorization": "Bearer " + other_token})
        assert forbidden.status_code == 403

        response = client.get(metadata_path, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["object"] == "chat2api.media"
        assert data["width"] == 1254 and data["height"] == 1254
        assert data["request_id"] == "req_example"
        assert data["url"].endswith(f"/api/media/{row['id']}/{row['token']}")
        assert data["thumbnail_url"] == data["url"] + "/thumbnail"
        assert "token" not in data and "owner" not in data

        original = client.get(data["url"])
        assert original.status_code == 200
        assert original.headers["content-type"] == "image/png"
        assert original.content.startswith(bytes([137, 80, 78, 71]))
        assert client.get(data["url"] + "/original").content == original.content
        thumb = client.get(data["thumbnail_url"])
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/webp"
        assert len(thumb.content) < len(original.content)
        # Explicit administrator credentials retain emergency read access.
        assert client.get(metadata_path, headers={"Authorization": "Bearer test-key"}).status_code == 200


def test_stream_marker_gate_withholds_source_image_until_canonical_archive_output() -> None:
    gate = StreamMediaMarkerGate()
    frames = ["先输出文字!", "[Generated image](https://media.oaiusercontent.com/",
              "temporary-image.png)", "尾部文字"]
    delivered = "".join(gate.push(frame) for frame in frames)
    assert delivered == "先输出文字"
    assert "temporary-image" not in delivered
    terminal = "先输出文字![Generated image](https://chat2api.example/api/media/med_abc/token)尾部文字"
    assert terminal.startswith(delivered)
    assert delivered + terminal[len(delivered):] == terminal
    assert gate.holding is True

    gate = StreamMediaMarkerGate()
    delivered = gate.push("文本") + gate.push("!") + gate.push("其他文本")
    assert delivered == "文本!其他文本"
    assert gate.holding is False

    gate = StreamMediaMarkerGate()
    assert gate.push("start![pic](data:image/png;base64,AAAA") == "start"
    assert gate.push("BBBB)") == ""
    assert gate.holding is True
