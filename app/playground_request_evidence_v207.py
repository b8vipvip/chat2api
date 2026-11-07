"""Correlate production API requests to persisted Playground runs.

Read-only reporting layer: never sets or changes broker/HTTP request status.
IDs originate from the authenticated TestRunStore record created before dispatch.
"""
from __future__ import annotations

from typing import Any


def request_test_index(test_runs: Any, limit: int = 300) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if test_runs is None:
        return index
    for run in test_runs.recent(limit):
        if not isinstance(run, dict):
            continue
        ids = [run.get("request_id"), *(run.get("request_ids") or [])]
        ids.extend(item.get("request_id") for item in (run.get("results") or [])
                   if isinstance(item, dict))
        for rid in ids:
            if isinstance(rid, str) and rid.startswith(("req_", "imgreq_", "resp_req_")):
                index.setdefault(rid, run)
    return index


def playground_request_badge(run: dict[str, Any] | None) -> str:
    """A label grounded in the *persisted* TestRun, never in prompt text.

    ABCD shares the chat test_type with ordinary Playground chat, so scene
    quality must take priority. Automatically scheduled Playground tests are
    produced by the distinct lifecycle-v1/testrun_ owner.
    """
    if not isinstance(run, dict):
        return ""
    quality = run.get("quality") if isinstance(run.get("quality"), dict) else {}
    report = run.get("abcd_report") if isinstance(run.get("abcd_report"), dict) else {}
    scene = quality.get("abcd_scene") or report.get("scene")
    if scene in {"A", "B", "C", "D"}:
        return "测" + scene
    run_id = str(run.get("run_id") or "")
    kind = str(run.get("test_type") or "")
    if (quality.get("lifecycle") == "playground-lifecycle-v1"
            or run_id.startswith("testrun_")):
        return "测自"
    if (quality.get("playground_chat") == "playground-chat-v3"
            or (run_id.startswith("chatrun_") and kind == "chat")):
        return "测聊"
    # An old or incomplete test row has no verified classification.
    return "测"


def playground_link(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {"is_playground_test": False}
    return {
        "is_playground_test": True,
        "playground_run_id": str(run.get("run_id") or ""),
        "playground_test_type": str(run.get("test_type") or ""),
        "playground_request_badge": playground_request_badge(run),
    }


def playground_diagnostics(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Include the exact recorded test diagnostics and ABCD checks, not status guesses."""
    if not run:
        return None
    return {
        **playground_link(run),
        "test_status": run.get("status"),
        "test_error": run.get("error"),
        "test_summary": run.get("summary"),
        "test_results": run.get("results") or [],
        "test_quality": run.get("quality") or {},
        "abcd_report": run.get("abcd_report"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }
