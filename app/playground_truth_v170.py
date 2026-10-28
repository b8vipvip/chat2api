"""Reconcile Playground UI state from authoritative production requests.

The browser is a *viewer*, not a second terminal owner. Large generated media is
archived as a protected file, never sent in a multi-megabyte admin JSON response.
"""
from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Any

from .timezone_utils import beijing_now_iso
from .response_history_media_v188 import history_safe_response_text

TERMINAL_REQUESTS = {"completed", "error", "cancelled", "stalled"}
DATA_IMAGE = re.compile(
    r"!\[[^\]\r\n]{0,150}\]\(data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]{100,6000000}={0,2})\)",
    re.IGNORECASE,
)


def _test_status(row: dict[str, Any]) -> str:
    state = str(row.get("status") or "")
    if state == "completed":
        return "passed"
    if state == "cancelled":
        return "cancelled"
    if state == "stalled" or (state == "error" and "timed out" in str(row.get("error") or "").lower()):
        return "stalled"
    return "failed"


def _is_authoritative_test(run: dict[str, Any]) -> bool:
    quality = run.get("quality")
    return isinstance(quality, dict) and (
        quality.get("source") == "manual-playground-chat"
        or quality.get("lifecycle") == "playground-lifecycle-v1"
    )


async def reconcile_playground_runs(test_runs: Any, telemetry: Any, limit: int = 100) -> int:
    """Make test-history statuses reflect backend truth, including browser fetch failures.

    A running backend request remains running. Never derive completion from a
    media candidate, probe status, HTTP connection lifetime, or client report.
    """
    count = 0
    for run in test_runs.recent(limit):
        if not _is_authoritative_test(run):
            continue
        ids = [str(x) for x in (run.get("request_ids") or []) if x]
        if not ids:
            continue
        result_rows = [dict(x) for x in (run.get("results") or [])]
        # ABCD artifact/quality checks are a separate observer. The production
        # broker remains the only owner of completed/error, while test history
        # must not turn a failed four-file acceptance into "passed" merely
        # because the SSE API returned stop + DONE.
        abcd = run.get("abcd_report")
        abcd_failed = isinstance(abcd, dict) and (
            abcd.get("status") == "failed" or abcd.get("passed") is False
        )
        statuses: list[str] = []
        changed = False
        for entry in result_rows:
            rid = str(entry.get("request_id") or "")
            if not rid:
                statuses.append(str(entry.get("status") or "skipped"))
                continue
            row = telemetry.get(rid) or {}
            if str(row.get("status") or "") not in TERMINAL_REQUESTS:
                statuses.append(str(entry.get("status") or "running"))
                continue
            status = _test_status(row)
            # A completed Voice call can still have a partial-coverage test
            # warning (e.g. no real PCM microphone input or reverse-proxy
            # Upgrade check). Backend success must not silently turn it
            # into a full coverage pass. The request terminal still belongs
            # exclusively to the server/Worker broker.
            if (
                status == "passed"
                and entry.get("kind") == "voice_call"
                and isinstance(entry.get("quality"), dict)
                and entry["quality"].get("grade") == "warning"
            ):
                status = "warning"
            if status == "passed" and abcd_failed:
                status = "failed"
            statuses.append(status)
            if entry.get("status") != status or entry.get("authoritative_request_status") != row["status"]:
                entry.update({
                    "status": status,
                    "authoritative_request_status": row["status"],
                    "message": (
                        "ABCD 文件/内容验收失败；API 请求本身已完成"
                        if abcd_failed and row["status"] == "completed"
                        else "已按服务端请求记录同步完成" if status == "passed"
                        else str(row.get("error") or status)[:300]
                    ),
                    "request_id": rid,
                    "response_chars": int(row.get("completion_chars") or 0),
                    "image_count": int(row.get("response_media_count") or 0),
                })
                changed = True
        # Reconcile terminal status only if ALL planned API requests are terminal;
        # never turn a mixed/multi-case running suite into a false successful run.
        completed = all(
            (telemetry.get(rid) or {}).get("status") in TERMINAL_REQUESTS
            for rid in ids
        )
        if not completed:
            if changed:
                await test_runs.update(run["run_id"], {"results": result_rows})
                count += 1
            continue
        status = "stalled" if "stalled" in statuses else "failed" if "failed" in statuses else (
            "cancelled" if "cancelled" in statuses else "warning" if "warning" in statuses else "passed"
        )
        if run.get("status") != status or changed:
            quality = dict(run.get("quality") or {})
            quality["authoritative_request_status"] = "reconciled"
            quality["record_lifecycle"] = "server-request-terminal-reconciliation-v170"
            if isinstance(abcd, dict):
                quality["abcd_content_validation_status"] = abcd.get("status") or "unknown"
                quality["abcd_content_validation_scene"] = abcd.get("scene")
            await test_runs.update(run["run_id"], {
                "results": result_rows, "status": status, "quality": quality,
                "finished_at": run.get("finished_at") or beijing_now_iso(),
                "summary": (
                    "ABCD 内容验收失败；底层 API 请求状态独立记录"
                    if abcd_failed and status == "failed"
                    else "已同步服务端请求终态：" + status
                ),
                "error": next((x["message"] for x in result_rows if x.get("status") in {"stalled", "failed"}), None),
            })
            count += 1
    return count


def present_playground_run(run: dict[str, Any]) -> dict[str, Any]:
    """Present elapsed time without writing a new JSONL snapshot on every poll."""
    displayed = dict(run)
    if not _is_authoritative_test(run) or str(run.get("status")) != "running":
        return displayed
    try:
        start = datetime.fromisoformat(str(run.get("started_at") or ""))
        if start.tzinfo is None:
            return displayed
        seconds = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    except (ValueError, TypeError):
        return displayed
    timeout = 600 if str(run.get("test_type")) == "image_generation" else 300
    displayed["elapsed_seconds"] = seconds
    displayed["request_timeout_seconds"] = timeout
    displayed["duration_ms"] = seconds * 1000
    displayed["summary"] = f"请求执行中 · 已等待 {seconds} 秒 / 图片最长 600 秒" if timeout == 600 else (
        f"请求执行中 · 已等待 {seconds} 秒 / 最长 300 秒"
    )
    return displayed


async def compact_playground_outcome(
    row: dict[str, Any], telemetry: Any, file_store: Any,
    *, include_full_text: bool = False,
) -> dict[str, Any]:
    """Return a small, admin-authenticated reply preview without image base64."""
    rid = str(row.get("request_id") or "")
    status = str(row.get("status") or "")
    content = str(row.get("response_text") or "")
    media = list(row.get("response_media") or [])
    if status == "completed" and content and not media:
        match = DATA_IMAGE.search(content)
        if match:
            mime, encoded = match.group(1).lower(), match.group(2)
            try:
                raw = base64.b64decode(encoded, validate=True)
                if 64 <= len(raw) <= 4 * 1024 * 1024:
                    magic_ok = (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or (
                        mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")
                    ) or (mime == "image/webp" and raw[0:4] == b"RIFF" and raw[8:12] == b"WEBP") or (
                        mime == "image/gif" and raw.startswith((b"GIF87a", b"GIF89a"))
                    )
                    if magic_ok:
                        item = await file_store.create(
                            filename=f"playground-{rid}.{mime.split('/')[-1]}",
                            data_base64=encoded, mime_type=mime,
                            owner_key_id=str(row.get("api_key_id") or ""),
                            purpose="generated-output",
                        )
                        media = [{"file_id": item.file_id, "mime_type": mime, "bytes": item.size}]
                        # Preserve the real asset separately while keeping JSONL
                        # and subsequent admin pages small.
                        replacement = DATA_IMAGE.sub("【生成图片已归档，可在请求对话中预览】", content, count=1)
                        await telemetry.upsert({
                            "request_id": rid,
                            "response_media": media,
                            "response_media_count": len(media),
                            "response_text": replacement,
                        })
            except (ValueError, binascii.Error, OSError):
                pass
    if DATA_IMAGE.search(content):
        preview = DATA_IMAGE.sub("【生成图片：请在请求记录中预览】", content)
    else:
        preview = content
    result = {
        "request_id": rid, "status": status, "error": row.get("error"),
        "request_type": row.get("request_type"), "model": row.get("requested_model"),
        "response_chars": int(row.get("completion_chars") or 0),
        "response_text_preview": preview[:3500],
        "response_media": media[:4],
        "response_media_count": len(media),
        "is_authoritative": True,
    }
    if include_full_text and status == "completed":
        # Opt-in, admin-authenticated recovery of original long text. The
        # default endpoint remains compact, and inline Base64 is NEVER exposed
        # from legacy history even when a previous version saved it verbatim.
        safe = history_safe_response_text(preview)
        result["response_text"] = safe[:500000]
        result["response_text_truncated"] = len(safe) > 500000
    return result
