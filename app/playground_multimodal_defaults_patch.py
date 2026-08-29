from __future__ import annotations

import base64
import struct
import uuid
import zlib
from typing import Any

from fastapi import FastAPI

from .models import PlaygroundRunRequest


PATCH_ID = "playground-default-multimodal-v22-34"
_DEFAULT_MARKER = "_chat2api_playground_default_kind"
_DEFAULT_FILE_TEXT = (
    "chat2api default file-understanding sample\n"
    "Marker: FILE-UNDERSTANDING-742\n"
    "This document exists only to verify that the selected Worker can upload, attach, "
    "read, and answer from a document through the normal /v1/chat/completions path.\n"
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def default_vision_png() -> bytes:
    """Return a small deterministic RGB PNG without depending on Pillow."""
    width, height = 96, 64
    scanlines: list[bytes] = []
    for y in range(height):
        row = bytearray([0])  # PNG filter type 0.
        for x in range(width):
            if x < width // 2 and y < height // 2:
                rgb = (230, 55, 55)
            elif x >= width // 2 and y < height // 2:
                rgb = (55, 180, 75)
            elif x < width // 2:
                rgb = (55, 95, 220)
            else:
                rgb = (245, 210, 55)
            row.extend(rgb)
        scanlines.append(bytes(row))
    raw = b"".join(scanlines)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def default_file_bytes() -> bytes:
    return _DEFAULT_FILE_TEXT.encode("utf-8")


async def _upload_default(
    client: Any,
    token: str,
    *,
    kind: str,
) -> dict[str, Any]:
    if kind == "vision":
        filename = "chat2api-default-vision.png"
        mime_type = "image/png"
        payload = default_vision_png()
    elif kind == "file":
        filename = "chat2api-default-file.txt"
        mime_type = "text/plain"
        payload = default_file_bytes()
    else:  # pragma: no cover - caller constrains this to the two multimodal tests.
        raise ValueError(f"Unsupported default playground sample kind: {kind}")

    response = await client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "filename": filename,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "purpose": "chat2api",
        },
    )
    if response.status_code >= 400:
        try:
            value = response.json()
        except Exception:
            value = {}
        detail = str(value.get("detail") or getattr(response, "text", "") or response.status_code)
        raise RuntimeError(f"Playground default {kind} sample upload failed: {detail[:1000]}")
    value = response.json()
    return {
        "id": str(value.get("id") or ""),
        "filename": filename,
        "mime_type": mime_type,
        _DEFAULT_MARKER: kind,
    }


def install_playground_multimodal_defaults_patch(app: FastAPI) -> FastAPI:
    """Make the console's “留空自动生成默认样本” promise true for vision/file tests.

    This patch does not grant or fake model capabilities. It only ensures the
    playground actually dispatches a normal multimodal API request when the
    administrator leaves the attachment chooser empty. The existing Mini quota
    router remains authoritative for Free-account availability/cooldowns.
    """
    if getattr(app.state, "playground_multimodal_defaults_patch_installed", False):
        return app
    manager = getattr(app.state, "playground_run_manager", None)
    if manager is None:
        raise RuntimeError("playground lifecycle must be installed before multimodal defaults")

    base_planned_request_ids = manager._planned_request_ids
    base_run_kind = manager._run_kind

    def planned_request_ids(body: PlaygroundRunRequest, kinds: list[str]) -> dict[str, str]:
        planned = dict(base_planned_request_ids(body, kinds))
        # Vision/file must receive a request id even without an administrator
        # upload because a deterministic sample will be attached at execution.
        for kind in ("vision", "file"):
            if kind in kinds and kind not in planned:
                planned[kind] = "req_" + uuid.uuid4().hex
        return planned

    async def run_kind(
        client: Any,
        *,
        kind: str,
        body: PlaygroundRunRequest,
        token: str,
        request_id: str | None,
        uploaded: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if kind not in {"vision", "file"}:
            # Generated self-test inputs must not accidentally become a reference
            # image/file for a later image-generation case in an "all" run.
            explicit_only = [item for item in uploaded if not item.get(_DEFAULT_MARKER)]
            return await base_run_kind(
                client,
                kind=kind,
                body=body,
                token=token,
                request_id=request_id,
                uploaded=explicit_only,
            )

        want_image = kind == "vision"
        attachment = manager._file_for(uploaded, want_image)
        generated = False
        if attachment is None:
            attachment = await _upload_default(client, token, kind=kind)
            uploaded.append(attachment)
            generated = True
        if not request_id:
            raise RuntimeError(f"Missing planned request ID for playground test: {kind}")

        result = await manager._run_chat(
            client,
            kind=kind,
            model=body.model,
            reasoning_effort=body.reasoning_effort,
            token=token,
            request_id=request_id,
            attachment=attachment,
        )
        result["sample_source"] = "generated-default" if generated else "administrator-upload"
        result["sample_filename"] = str(attachment.get("filename") or "")
        if generated and result.get("status") == "passed":
            result["message"] = "调用完成（自动默认样本）"
        return result

    manager._planned_request_ids = planned_request_ids
    manager._run_kind = run_kind
    app.state.playground_multimodal_defaults_patch_installed = True
    return app
