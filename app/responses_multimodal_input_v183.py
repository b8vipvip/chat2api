"""Single, authenticated attachment resolver for OpenAI-compatible multimodal inputs.

All content is converted into the same FileStore references already used by
Chat Completions and the canonical Image Generation Worker request. This module
never downloads arbitrary caller URLs, chooses Workers or owns request terminals.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from PIL import Image, UnidentifiedImageError

from .api_keys import ApiPrincipal
from .file_store import FileStore
from .media_archive_v173 import MediaArchive


_OWNED_MEDIA = re.compile(r"^/api/media/(med_[A-Za-z0-9]+)/([A-Za-z0-9_-]+)(?:/original)?$")
_IMAGE_MIME = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}
_TEXT_TYPES = {"input_text", "output_text", "text"}


def multimodal_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Find real image/file input parts; do not confuse tool output with input."""
    result: list[dict[str, Any]] = []
    value = body.get("input")
    if not isinstance(value, list):
        return result
    for entry in value:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in {"input_image", "input_file", "image_url"}:
            result.append(entry)
        parts = entry.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("type") in {"input_image", "input_file", "image_url"}:
                result.append(part)
    return result


def needs_multimodal(body: dict[str, Any]) -> bool:
    return bool(multimodal_parts(body))


def attachment_specs(file_store: FileStore, file_ids: list[str], principal: ApiPrincipal) -> list[dict[str, object]]:
    """One owner policy for both public endpoints and both image/vision tasks."""
    result: list[dict[str, object]] = []
    for file_id in dict.fromkeys(file_ids):
        item = file_store.get(file_id)
        if not item:
            raise HTTPException(status_code=400, detail=f"Unknown attachment file_id: {file_id}")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail=f"Attachment {file_id} belongs to another API key")
        result.append({"file_id": item.file_id, "filename": item.filename,
                       "mime_type": item.mime_type, "size": item.size})
    return result


def verify_raster_image(data: bytes, mime: str) -> None:
    """Validate actual raster bytes, not only a caller-supplied MIME label."""
    _verify_raster(data, mime)


def validate_image_file(file_store: FileStore, file_id: str, principal: ApiPrincipal) -> tuple[Any, bytes]:
    """Resolve one API-key-owned vision file and verify its stored raster bytes."""
    attachment_specs(file_store, [file_id], principal)
    item = file_store.get(file_id)
    if item is None or item.purpose != "vision":
        raise ValueError("input_image file_id must reference a vision image attachment")
    try:
        stored, payload = file_store.read(file_id)
    except (KeyError, FileNotFoundError) as error:
        raise ValueError("input_image file content is unavailable") from error
    if stored.size != len(payload):
        raise ValueError("input_image stored byte count does not match its metadata")
    if len(payload) > 10 * 1024 * 1024:
        raise ValueError("input_image exceeds the 10 MiB per-image limit")
    _verify_raster(payload, stored.mime_type)
    return stored, payload

_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def validate_document_file(file_store: FileStore, file_id: str, principal: ApiPrincipal) -> tuple[Any, bytes]:
    """Resolve one owner-scoped Web Chat document and verify its stored bytes."""
    attachment_specs(file_store, [file_id], principal)
    item = file_store.get(file_id)
    if item is None or item.purpose != "assistants" or item.mime_type not in _DOCUMENT_MIME_TYPES:
        raise ValueError("file content part must reference an assistants document with a supported MIME type")
    try:
        stored, payload = file_store.read(file_id)
    except (KeyError, FileNotFoundError) as error:
        raise ValueError("file content part document bytes are unavailable") from error
    except OSError as error:
        # FileStore distinguishes size and digest integrity failures from
        # ordinary missing/unreadable payloads. Preserve that distinction
        # without exposing filesystem paths or raw storage exceptions.
        if "size integrity" in str(error).lower():
            raise ValueError("file content part document byte count does not match its metadata") from error
        if "sha-256 integrity" in str(error).lower():
            raise ValueError("file content part document SHA-256 integrity check failed") from error
        raise ValueError("file content part document bytes are unavailable") from error
    if not payload or stored.size != len(payload):
        raise ValueError("file content part document byte count does not match its metadata")
    if len(payload) > file_store.max_file_bytes:
        raise ValueError("file content part document exceeds the configured file size limit")
    return stored, payload

def _verify_raster(data: bytes, mime: str) -> None:
    if mime not in _IMAGE_MIME:
        raise ValueError("input_image supports PNG, JPEG, WebP or GIF")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != _IMAGE_MIME[mime]:
                raise ValueError("input_image bytes do not match declared MIME")
            if image.width * image.height > 36_000_000:
                raise ValueError("input_image dimensions are too large")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("input_image is not a valid raster image") from error


def _owned_image_bytes(url: str, request: Request, archive: MediaArchive,
                       principal: ApiPrincipal) -> tuple[bytes, str]:
    candidate = urlsplit(url)
    if candidate.username or candidate.password or candidate.query or candidate.fragment:
        raise ValueError("Media image URL may not contain credentials, query or fragment")
    configured = str(archive._config().get("public_base_url") or "").rstrip("/")
    allowed = {str(request.base_url).rstrip("/")}
    if configured:
        allowed.add(configured)
    if f"{candidate.scheme}://{candidate.netloc}" not in allowed:
        raise ValueError("Remote image URLs are not fetched: use a base64 data URL, file_id, or chat2api-owned media URL")
    match = _OWNED_MEDIA.fullmatch(candidate.path)
    if not match:
        raise ValueError("Only original chat2api archived media URLs can be reused as input_image")
    media_id, token = match.groups()
    try:
        archive.describe(media_id, principal.key_id, admin=principal.kind == "master")
        data, mime = archive.load(media_id, token, thumbnail=False)
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(status_code=403, detail="Media input is unavailable to this API key") from error
    return data, mime


async def resolve_responses_attachments(
    body: dict[str, Any], request: Request, file_store: FileStore,
    archive: MediaArchive, principal: ApiPrincipal,
) -> list[dict[str, object]]:
    parts = multimodal_parts(body)
    if len(parts) > 4:
        raise ValueError("At most four image/file inputs are supported per request")
    file_ids: list[str] = []
    for index, part in enumerate(parts, start=1):
        kind = str(part.get("type") or "")
        if kind in {"input_image", "image_url"}:
            image_value = part.get("image_url")
            if isinstance(image_value, dict):
                image_value = image_value.get("url")
            file_id = part.get("file_id") or (
                image_value if isinstance(image_value, str) and image_value.startswith("file_") else None
            )
            if file_id:
                validate_image_file(file_store, str(file_id), principal)
                file_ids.append(str(file_id))
                continue
            if not isinstance(image_value, str) or not image_value:
                raise ValueError("input_image requires image_url or file_id")
            if image_value.startswith("data:"):
                data, mime = file_store._decode(image_value, None)
            elif image_value.startswith("https://") or image_value.startswith("http://"):
                data, mime = _owned_image_bytes(image_value, request, archive, principal)
            else:
                raise ValueError("input_image URL must be an image data URL, a file_id, or a chat2api archived original")
            _verify_raster(data, mime)
            created = await file_store.create(
                filename=f"responses-image-{index}.{_IMAGE_MIME[mime].lower()}",
                data_base64=base64.b64encode(data).decode("ascii"),
                mime_type=mime, owner_key_id=principal.key_id, purpose="vision",
            )
            file_ids.append(created.file_id)
        elif kind == "input_file":
            file_id = part.get("file_id")
            if file_id:
                attachment_specs(file_store, [str(file_id)], principal)
                file_ids.append(str(file_id))
                continue
            if part.get("file_url"):
                raise ValueError("Remote input_file URLs are not fetched; upload with POST /v1/files")
            raw = part.get("file_data")
            if not isinstance(raw, str) or not raw:
                raise ValueError("input_file requires file_id or base64 file_data")
            created = await file_store.create(
                filename=str(part.get("filename") or f"responses-file-{index}.bin"),
                data_base64=raw, mime_type=None,
                owner_key_id=principal.key_id, purpose="file-understanding",
            )
            file_ids.append(created.file_id)
    return attachment_specs(file_store, file_ids, principal)
