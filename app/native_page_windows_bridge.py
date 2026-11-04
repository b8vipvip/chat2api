"""Authenticated, bounded, memory-only transfer for Windows extension diagnostics.

Read-only transport: it never touches the chat broker, request terminals,
live routing windows or the Linux Python/CDP diagnosis controller.
"""
from __future__ import annotations
import asyncio
import base64
import binascii
import hashlib
import json
import re

JOB_ID = re.compile(r"^[a-f0-9]{32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_BYTES = 1_500_000
PART_BYTES = 32_768


class WindowsNativePageBridge:
    def __init__(self, registry):
        self.registry = registry
        self.pending: dict[str, dict] = {}

    async def collect(self, client_id: str, job_id: str, url: str, *, timeout: float = 155):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("invalid_windows_job_id")
        if job_id in self.pending:
            raise RuntimeError("duplicate_windows_diagnostic_job")
        if client_id not in self.registry.sockets:
            raise RuntimeError("windows_extension_offline")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[job_id] = {
            "client_id": client_id, "future": future, "chunks": [],
            "size": None, "sha256": None, "count": None,
        }
        try:
            await self.registry.send(client_id, {
                "type": "native.page.windows.start",
                "job_id": job_id, "conversation_url": url,
            })
            raw = await asyncio.wait_for(future, timeout)
            record = json.loads(raw)
            if (not isinstance(record, dict)
                or record.get("schema") != "windows-native-page-evidence-v1"
                or not isinstance(record.get("probe"), dict)
                or not isinstance(record.get("counts"), dict)
                or not isinstance(record.get("limitations"), list)
                or not isinstance(record.get("boundary_verified"), bool)
                or not isinstance(record.get("complete"), bool)):
                raise ValueError("windows_diagnostic_schema_invalid")
            return record, raw
        finally:
            self.pending.pop(job_id, None)

    async def ingest(self, client_id: str, message: dict):
        job_id = str(message.get("job_id") or "")
        if not JOB_ID.fullmatch(job_id):
            return
        state = self.pending.get(job_id)
        if not state or state["client_id"] != client_id:
            return
        future = state["future"]
        if future.done():
            return
        try:
            kind = str(message.get("type") or "")
            if kind == "native.page.windows.chunk":
                count = message.get("count")
                index = message.get("index")
                length = message.get("size")
                digest = str(message.get("sha256") or "")
                raw = str(message.get("data") or "")
                if (type(count) is not int or type(index) is not int
                    or type(length) is not int
                    or not 1 <= count <= (MAX_BYTES + PART_BYTES - 1) // PART_BYTES
                    or not 0 <= index < count or not 1 <= length <= MAX_BYTES
                    or not DIGEST.fullmatch(digest) or len(raw) > 50000):
                    raise ValueError("windows_chunk_metadata_invalid")
                if (index != len(state["chunks"])
                    or len(state["chunks"]) >= count):
                    raise ValueError("windows_chunk_order_invalid")
                if state["size"] is not None and (
                    state["size"] != length or state["sha256"] != digest
                    or state["count"] != count):
                    raise ValueError("windows_chunk_header_changed")
                part = base64.b64decode(raw, validate=True)
                expected = min(PART_BYTES, length - index * PART_BYTES)
                if expected <= 0 or len(part) != expected:
                    raise ValueError("windows_chunk_length_invalid")
                state.update({"size": length, "sha256": digest, "count": count})
                state["chunks"].append(part)
            elif kind == "native.page.windows.finished":
                if message.get("ok") is not True:
                    raise RuntimeError(
                        "windows_diagnostic_" +
                        str(message.get("error") or "capture_failed")[:100])
                count = state["count"]
                raw = b"".join(state["chunks"])
                if (count is None or len(state["chunks"]) != count
                    or len(raw) != state["size"]
                    or hashlib.sha256(raw).hexdigest() != state["sha256"]
                    or message.get("sha256") != state["sha256"]):
                    raise ValueError("windows_diagnostic_transfer_incomplete")
                future.set_result(raw)
        except (ValueError, RuntimeError, TypeError, binascii.Error) as exc:
            if not future.done():
                future.set_exception(
                    ValueError("windows_chunk_invalid_base64")
                    if isinstance(exc,binascii.Error) else exc)
