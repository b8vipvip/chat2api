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
        diagnostics = row.get("diagnostics")
        if isinstance(diagnostics, dict) and (
            diagnostics.get("api_stream_closed_before_delivery") is True
            and diagnostics.get("api_delivery_state") == "disconnected"
        ):
            # The Worker completed in the background after the HTTP consumer
            # disappeared. This is NOT an end-to-end successful SSE delivery.
            # Preserve the broker's completed terminal and surface a warning.
            return "warning"
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


async def reconcile_playground_runs(
    test_runs: Any, telemetry: Any, limit: int = 100,
    *, only_run_id: str | None = None,
    active_run_ids: set[str] | None = None,
) -> int:
    """Make test-history statuses reflect backend truth, including browser fetch failures.

    A running backend request remains running. Never derive completion from a
    media candidate, probe status, HTTP connection lifetime, or client report.
    """
    count = 0
    active = {str(value) for value in (active_run_ids or set())}
    # On a Broker terminal, update only the one previously-registered
    # chat run instead of waiting for an operator to open the Tests page.
    # Full list reconciliation remains available for old/recovered runs.
    if only_run_id is not None:
        selected = test_runs.get(only_run_id)
        runs = [selected] if isinstance(selected, dict) else []
    else:
        runs = test_runs.recent(limit)
    for run in runs:
        # A terminal Playground result belongs to its executor and is
        # immutable. In particular, startup recovery marks interrupted runs
        # stalled; a later Telemetry read must not upgrade them to passed.
        if str(run.get("status") or "") in {
            "passed", "warning", "failed", "skipped", "cancelled", "stalled",
        }:
            continue
        if not _is_authoritative_test(run):
            continue
        ids = [str(x) for x in (run.get("request_ids") or []) if x]
        if not ids:
            continue
        result_rows = [dict(x) for x in (run.get("results") or [])]
        execution_active = str(run.get("run_id") or "") in active
        # ABCD artifact/quality checks are a separate observer. The production
        # broker remains the only owner of completed/error, while test history
        # must not turn a failed four-file acceptance into "passed" merely
        # because the SSE API returned stop + DONE.
        abcd = run.get("abcd_report")
        abcd_failed = isinstance(abcd, dict) and (
            abcd.get("status") == "failed" or abcd.get("passed") is False
        )
        # The visual/file C-group can have all four ready archives while the
        # upstream ChatGPT stream lacks trusted terminal proof. Such a run is
        # NOT a full end-to-end pass, even if the broker row is completed.
        abcd_warning = isinstance(abcd, dict) and abcd.get("status") == "warning"
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
            # Backend completion is not the same as completion of the
            # Playground executor. It still has to consume/validate the HTTP
            # response and persist rich result fields (audio bytes, effective
            # model, artifacts, quality). Never upgrade a local failure or
            # cancellation to a backend-only pass.
            local_status = str(entry.get("status") or "")
            if local_status in {"failed", "cancelled", "stalled"}:
                status = local_status
            elif local_status == "warning" and status == "passed":
                status = "warning"
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
            elif status == "passed" and abcd_warning:
                status = "warning"
            statuses.append(status)
            # While the executor is live, keep its visible case pending/running
            # even though the authoritative request has reached terminal. The
            # executor owns final response parsing and rich result persistence.
            visible_status = (
                local_status
                if execution_active and local_status in {"pending", "running"}
                else status
            )
            if entry.get("status") != visible_status or entry.get("authoritative_request_status") != row["status"]:
                if execution_active and local_status in {"pending", "running"}:
                    message = "Worker 已完成；正在等待 Playground 执行器完成响应和媒体验证"
                elif abcd_failed and row["status"] == "completed":
                    message = "ABCD 文件/内容验收失败；API 请求本身已完成"
                elif status == "passed":
                    message = "已按服务端请求记录同步完成"
                elif (
                    status == "warning"
                    and isinstance(row.get("diagnostics"), dict)
                    and row["diagnostics"].get("api_stream_closed_before_delivery") is True
                ):
                    message = "Worker 已完成但 HTTP 流提前断开；调用方没有完整收到最终回复"
                else:
                    message = str(row.get("error") or status)[:300]
                entry.update({
                    "status": visible_status,
                    "authoritative_request_status": row["status"],
                    "message": message,
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
        # A live Playground task owns the aggregate result. It may still be
        # parsing an HTTP body or validating media after the Worker/Broker has
        # reached terminal. Persist backend evidence, but leave the run active
        # until that executor finalizes its complete result object. After a
        # process restart there are no active tasks, so reconciliation resumes.
        if str(run.get("run_id") or "") in active:
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


async def reconcile_playground_request_terminal(
    test_runs: Any, telemetry: Any, request_id: str,
    *, active_run_ids: set[str] | None = None,
) -> int:
    """Refresh a known manual Playground run from the persisted Broker terminal.

    The admin UI may be closed or the direct ABCD SSE may have disconnected
    before finalization. A run is recorded *before* dispatch under a stable
    UUID5 derived from this same request ID. Only update that existing run;
    never invent a test or a completion from an HTTP close.
    """
    import uuid

    if not isinstance(request_id, str) or not request_id.startswith("req_"):
        return 0
    run_id = "chatrun_" + uuid.uuid5(
        uuid.NAMESPACE_URL, f"chat2api-playground-chat:{request_id}"
    ).hex
    if not isinstance(test_runs.get(run_id), dict):
        return 0
    return await reconcile_playground_runs(
        test_runs, telemetry, only_run_id=run_id,
        active_run_ids=active_run_ids,
    )


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
