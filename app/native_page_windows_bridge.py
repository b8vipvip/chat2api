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
import time
import uuid

JOB_ID = re.compile(r"^[a-f0-9]{32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_BYTES = 1_500_000
PART_BYTES = 32_768
PROGRESS_STAGES = frozenset({
    "accepted", "worker_lookup", "window_creating", "window_created", "tab_opening", "tab_opened",
    "tab_loading", "page_ready", "assistant_scanning",
    "assistant_captured", "uploading", "upload_complete",
    "tab_closed", "failed",
})


class WindowsNativePageBridge:
    def __init__(self, registry):
        self.registry = registry
        self.pending: dict[str, dict] = {}
        self.pending_updates: dict[str, dict] = {}

    async def install_tool(self, client_id: str, source: str, manifest: dict,
                           *, timeout: float = 25.0) -> dict:
        """Push only the server-verified fixed GitHub release to an online Worker."""
        if client_id not in self.registry.sockets:
            raise RuntimeError("windows_extension_offline")
        version = str(manifest.get("version") or "")
        sha = str(manifest.get("sha256") or "")
        data = source.encode("utf-8")
        if (not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or not DIGEST.fullmatch(sha)
            or not 0 < len(data) <= 120000
            or manifest.get("bytes") != len(data)
            or hashlib.sha256(data).hexdigest() != sha
            or any(state["client_id"] == client_id
                   for state in self.pending_updates.values())):
            raise RuntimeError("windows_tool_install_not_ready")
        token = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_updates[token] = {
            "future": future, "client_id": client_id,
            "version": version, "sha256": sha,
        }
        try:
            await self.registry.send(client_id, {
                "type": "native.page.windows.tool.update",
                "update_id": token, "version": version,
                "sha256": sha, "source": source,
            })
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending_updates.pop(token, None)

    async def collect(self, client_id: str, job_id: str, url: str, *, timeout: float = 155,
                      show_tab: bool = False, on_progress=None):
        if type(show_tab) is not bool:
            raise ValueError("invalid_visual_diagnostic_option")
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
            "show_tab": show_tab, "on_progress": on_progress, "events": 0,
        }
        try:
            await self.registry.send(client_id, {
                "type": "native.page.windows.start",
                "job_id": job_id, "conversation_url": url,
                "show_tab": show_tab,
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
        if message.get("type") == "native.page.windows.tool.updated":
            update_id = str(message.get("update_id") or "")
            state = self.pending_updates.get(update_id)
            if (not state or state["client_id"] != client_id
                or state["future"].done()):
                return
            if message.get("ok") is not True:
                error = str(message.get("error") or "tool_install_failed")[:120]
                state["future"].set_exception(RuntimeError(
                    "windows_diagnostic_tool_" + error))
            elif (message.get("version") != state["version"]
                  or message.get("sha256") != state["sha256"]
                  or type(message.get("updated")) is not bool):
                state["future"].set_exception(
                    ValueError("windows_diagnostic_tool_receipt_mismatch"))
            else:
                state["future"].set_result({
                    "ok": True,
                    "installed": True,
                    "version": state["version"],
                    "sha256": state["sha256"],
                    "updated": message["updated"],
                    "source": "github-dedicated-release-branch",
                    "capture_mode": "userScripts/USER_SCRIPT",
                })
            return
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
            if kind == "native.page.windows.progress":
                stage = message.get("stage")
                elapsed = message.get("elapsed_ms")
                shown = message.get("show_tab")
                tab_id = message.get("tab_id")
                window_id = message.get("window_id")
                if (stage not in PROGRESS_STAGES
                    or type(elapsed) is not int or not 0 <= elapsed <= 180000
                    or type(shown) is not bool or shown != state["show_tab"]
                    or (tab_id is not None and
                        (type(tab_id) is not int or not 1 <= tab_id <= 2**31-1))
                    or (window_id is not None and
                        (type(window_id) is not int or not 1 <= window_id <= 2**31-1))):
                    return
                if state["events"] >= 48:
                    return
                event = {
                    "stage": stage,
                    "elapsed_ms": elapsed,
                    "received_at_ms": int(time.time() * 1000),
                    "show_tab": shown,
                }
                if tab_id is not None:
                    event["tab_id"] = tab_id
                if window_id is not None:
                    event["window_id"] = window_id
                state["events"] += 1
                callback = state.get("on_progress")
                if callback is not None:
                    await callback(event)
            elif kind == "native.page.windows.chunk":
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
                    or message.get("sha256") != state["sha256"]
                    or type(message.get("size")) is not int
                    or message.get("size") != state["size"]):
                    raise ValueError("windows_diagnostic_transfer_incomplete")
                future.set_result(raw)
        except (ValueError, RuntimeError, TypeError, binascii.Error) as exc:
            if not future.done():
                future.set_exception(
                    ValueError("windows_chunk_invalid_base64")
                    if isinstance(exc,binascii.Error) else exc)
