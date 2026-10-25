"""Status-only OpenAI SSE extension and bounded progress evidence.

Status and verified page summaries are never authoritative answer text.
This module has no terminal ownership and never updates a request deadline.
"""
from __future__ import annotations

import re
from typing import Any

STATUS_LIMIT = 240
_BARE_PROGRESS = re.compile(
    r"^(?:正在(?:思考|分析|推理|生成|处理|搜索|浏览|读取|整理)(?:中|内容|回复|答案|文件)?|"
    r"(?:thinking|analyzing|reasoning|generating|working|searching|browsing)"
    r"(?: now|\.\.\.)?)$", re.I,
)
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f]")


def status_from_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, str] | None:
    """Only Worker-observed ChatGPT status; never echo a general DOM fragment."""
    if not isinstance(diagnostics, dict):
        return None
    source = str(diagnostics.get("visible_reasoning_source") or "").strip()
    if source not in {"visible-chatgpt-ui-v20", "visible-chatgpt-ui-v196", "generation-control-v196"}:
        return None
    text = " ".join(str(diagnostics.get("visible_reasoning_status") or "").split()).strip()
    kind = str(diagnostics.get("visible_reasoning_status_kind") or "working").lower()
    if not text or len(text) > STATUS_LIMIT or _UNSAFE.search(text) or kind not in {"working", "reasoning"}:
        return None
    # A UI status is short and explicit. A longer summary is passed only when
    # the source is a dedicated reasoning observation, not a page text scrape.
    verified_summary = kind == "reasoning" and source in {
        "visible-chatgpt-ui-v20", "visible-chatgpt-ui-v196"
    }
    if not verified_summary and not _BARE_PROGRESS.fullmatch(text.rstrip(".。…")):
        return None
    return {"text": text, "kind": kind, "source": source}


def progress_is_real(state: Any) -> bool:
    """Only real request-correlated content/network progress allows extension.

    A static spinner, changing heartbeat counter, generic status text, or the
    presence of a Stop button is never enough to change the deadline.
    """
    diagnostics = getattr(state, "diagnostics", {}) or {}
    try:
        if int(diagnostics.get("network_recovered_assistant_chars") or 0) >= 32:
            return True
    except (TypeError, ValueError):
        pass
    marker = getattr(state, "_chat2api_generation_activity_marker", None)
    if isinstance(marker, tuple) and len(marker) == 2:
        name, value = marker
        if name in {"generated_chars", "response_chars", "delta_count"}:
            try:
                return int(value) > 0
            except (TypeError, ValueError):
                return False
        if name == "generation_progress":
            return bool(value) and value != "0"
    return len(str(getattr(state, "text", "") or "")) >= 32
