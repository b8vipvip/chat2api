"""P0/P1 Worker probe policies and privacy-preserving request evidence.

Probes are *observers*. They cannot publish chat terminal events, change
scheduler routing, or carry page HTML, credentials or signed media URLs.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import secrets
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso

TOOL_CATALOG = (
    {"id": "media_delivery_v1", "name": "图片交付探针", "version": "1.0.0",
     "phase": "P0", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "media_handoff", "unresolved_after_threshold", "before_terminal"]},
    {"id": "conversation_dom_v1", "name": "聊天回合与 DOM 探针", "version": "1.0.0",
     "phase": "P0", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "media_handoff", "before_terminal"]},
    {"id": "network_evidence_v1", "name": "网络事件摘要探针", "version": "1.0.0",
     "phase": "P1", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "media_handoff", "unresolved_after_threshold", "before_terminal"]},
    {"id": "model_tool_state_v1", "name": "模型与工具状态探针", "version": "1.0.0",
     "phase": "P1", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "unresolved_after_threshold", "before_terminal"]},
    {"id": "selector_compat_v1", "name": "页面改版兼容探针", "version": "1.0.0",
     "phase": "P1", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "unresolved_after_threshold", "before_terminal"]},
    {"id": "abcd_richtext_v1", "name": "A 组富文本/表格/代码/公式证据", "version": "1.0.0",
     "phase": "P2", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "unresolved_after_threshold", "before_terminal"]},
    {"id": "abcd_images_v1", "name": "B 组多图片生成与交付证据", "version": "1.0.0",
     "phase": "P2", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "media_handoff", "before_terminal"]},
    {"id": "abcd_files_v1", "name": "C 组多文件类型与下载证据", "version": "1.0.0",
     "phase": "P2", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "media_handoff", "before_terminal"]},
    {"id": "abcd_context_v1", "name": "D 组上下文/回合关联证据", "version": "1.0.0",
     "phase": "P2", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "unresolved_after_threshold", "before_terminal"]},
    {"id": "response_boundary_v1", "name": "回复正文选区完整性与终态证据", "version": "1.0.0",
     "phase": "P2", "platforms": ["linux", "win"], "triggers": ["request_pre_submit", "progress_checkpoint", "before_terminal"]},
)
ACTIVE_TOOLS = frozenset(item["id"] for item in TOOL_CATALOG if item["phase"] in {"P0", "P1", "P2"})
STAGES = frozenset(["request_pre_submit", "request_submitted", "media_handoff",
                    "unresolved_after_threshold", "progress_checkpoint", "before_terminal", "manual_capture"])
ID_RE = re.compile(r"^(?:req|imgreq|resp|probe)[_-][a-zA-Z0-9_-]{6,100}$")
WORKER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{2,105}$")
ALLOWED_FACTS = frozenset([
    "main_found", "main_selector", "assistant_turn_nodes", "user_turn_nodes",
    "conversation_turn_nodes", "last_turn_role", "candidate_image_count",
    "new_image_candidate_count", "visible_large_images", "picture_nodes",
    "canvas_nodes", "css_background_nodes", "blob_image_nodes",
    "data_image_nodes", "https_image_nodes", "untrusted_image_nodes",
    "image_complete_nodes", "image_unloaded_nodes", "image_with_dimensions",
    "image_without_dimensions", "image_natural_width_max",
    "image_natural_height_max", "edit_buttons", "download_buttons",
    "download_button_url_attr_count", "download_button_click_only_count",
    "file_card_candidates_count", "file_card_href_count", "file_card_button_count",
    "download_link_https_count", "download_link_blob_count", "download_link_sandbox_count",
    "download_link_relative_count", "download_link_other_count", "download_link_explicit_count",
    "file_preview_control_count", "file_preview_xlsx_count", "file_preview_pptx_count",
    "file_preview_json_count", "file_preview_zip_count",
    "file_preview_conversation_link_count", "file_preview_click_only_count",
    "share_buttons", "composer_found", "generating", "page_ready",
    "new_image_in_current_turn", "candidate_after_user", "candidate_unattributed",
    "image_transfer_attempts", "image_transfer_successes",
    "media_handoff_observed", "network_terminal_proof", "capture_cost_ms",
    "selector_main_count", "selector_assistant_count", "selector_image_count",
    "selector_chat_thread_count", "request_elapsed_ms", "pending_media",
    "network_stream_seen", "network_stream_chunks", "network_stream_bytes",
    "network_stream_terminal_seen", "network_stream_unverified_end",
    "network_stream_last_age_ms", "network_terminal_chunks",
    "model_picker_count", "reasoning_selector_count",
    "tool_selector_count", "file_upload_selector_count",
    "image_editor_count", "selector_composer_count",
    "selector_send_button_count", "selector_stop_button_count",
    "scene_rich_block_count", "scene_changed_block_count", "scene_independent_block_count",
    "scene_structural_scan_nodes", "scene_structural_scan_overflow",
    "scene_short_semantic_blocks",
    "scene_uncovered_block_count", "scene_uncovered_block_chars",
    "scene_rich_max_chars", "scene_rich_total_chars", "scene_candidate_chars",
    "scene_capture_chars", "scene_network_candidate_chars", "scene_network_terminal_chars",
    "scene_network_transport_error", "scene_network_clean_terminal",
    "scene_markdown_table_count", "scene_markdown_row_count", "scene_code_block_count",
    "scene_inline_code_count", "scene_math_node_count", "scene_heading_count",
    "scene_list_count", "scene_final_action_count", "scene_new_final_action_count", "scene_finish_after_all_blocks",
    "scene_image_count", "scene_image_large_count", "scene_picture_count",
    "scene_canvas_count", "scene_file_card_count", "scene_file_input_count",
    "scene_file_pdf_count", "scene_file_xlsx_count", "scene_file_pptx_count",
    "scene_file_zip_count", "scene_file_json_count",
    "scene_context_turn_count", "scene_context_user_role_count",
    "scene_context_assistant_role_count", "scene_context_request_has_user_anchor",
    "scene_context_prior_assistants", "scene_context_submit_confirmed",
    "scene_context_generation_active",
    "scene_delivered_chars", "scene_delivered_table_rows", "scene_delivered_code_fences",
    "scene_delivered_formula_markers", "scene_delivered_heading_count",
])
ALLOWED_CLASSIFICATIONS = frozenset([
    "baseline", "no_assistant_turn", "handoff_without_visible_media",
    "visible_media_unattributed", "media_candidate_detected", "media_transfer_unavailable",
    "waiting_for_result", "delivery_evidence_only", "completed_observed",
    "request_error_observed", "capture_unavailable",
])
MAX_EVENTS_PER_REQUEST = 32
MAX_JSON_BYTES = 12_000
MAX_FACTS = 160


def _safe_id(value: Any) -> str:
    token = str(value or "")
    if not ID_RE.fullmatch(token):
        raise ValueError("Invalid request or probe ID")
    return token


def _as_bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("enabled must be boolean")
    return value


def _sanitize_facts(value: Any) -> dict[str, str | bool | int | float | None]:
    if not isinstance(value, dict) or len(value) > MAX_FACTS:
        raise ValueError("Invalid or oversized probe facts")
    result: dict[str, str | bool | int | float | None] = {}
    for key, fact in value.items():
        if key not in ALLOWED_FACTS:
            continue
        if fact is None or type(fact) is bool:
            result[key] = fact
        elif isinstance(fact, (int, float)) and not isinstance(fact, bool):
            if -1_000_000_000 < fact < 1_000_000_000:
                result[key] = fact
        elif key in {"last_turn_role", "main_selector"} and isinstance(fact, str):
            if len(fact) <= 50 and re.fullmatch(r"[a-zA-Z0-9_#\[\]=' .-]*", fact):
                result[key] = fact
    return result


class WorkerProbeStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "worker_probes"
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy_path = self.root / "policies.json"
        self.events_root = self.root / "events"
        self.events_root.mkdir(exist_ok=True)
        self.lock = asyncio.Lock()
        self.policies: dict[str, dict[str, Any]] = {}
        self.load()
        self.prune_expired()

    def prune_expired(self, retention_days: int = 30) -> int:
        """Bound evidence retention without touching business request history."""
        from time import time
        cutoff = time() - max(1, retention_days) * 86400
        removed = 0
        for path in self.events_root.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def load(self) -> None:
        if not self.policy_path.exists():
            return
        try:
            value = json.loads(self.policy_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                self.policies = {k: v for k, v in value.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.policies = {}

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        return [{**item, "available": item["id"] in ACTIVE_TOOLS} for item in TOOL_CATALOG]

    def policy(self, scope_id: str) -> dict[str, Any]:
        if scope_id != "global" and not scope_id.startswith("worker:"):
            raise ValueError("Invalid policy scope")
        if scope_id.startswith("worker:") and not WORKER_ID_RE.fullmatch(scope_id[7:]):
            raise ValueError("Invalid Worker ID")
        return {"scope_id": scope_id, "revision": 0, "tools": {},
                **self.policies.get(scope_id, {})}

    @staticmethod
    def _validated_tools(value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict) or len(value) > len(ACTIVE_TOOLS):
            raise ValueError("Invalid tool policy")
        tools: dict[str, dict[str, Any]] = {}
        for name, options in value.items():
            if name not in ACTIVE_TOOLS:
                raise ValueError(f"Unknown or unsupported probe: {name}")
            if not isinstance(options, dict) or set(options) - {"enabled", "mode", "unresolved_after_ms", "max_snapshots"}:
                raise ValueError("Unexpected probe policy fields")
            normalized: dict[str, Any] = {}
            if "enabled" in options:
                normalized["enabled"] = _as_bool(options["enabled"])
            if "mode" in options:
                if options["mode"] not in ("on_media_handoff", "lightweight"):
                    raise ValueError("Invalid probe mode")
                normalized["mode"] = options["mode"]
            if "unresolved_after_ms" in options:
                number = options["unresolved_after_ms"]
                if type(number) is not int or not 5_000 <= number <= 120_000:
                    raise ValueError("Invalid probe threshold")
                normalized["unresolved_after_ms"] = number
            if "max_snapshots" in options:
                number = options["max_snapshots"]
                if type(number) is not int or not 1 <= number <= 8:
                    raise ValueError("Invalid snapshot cap")
                normalized["max_snapshots"] = number
            tools[name] = normalized
        return tools

    async def put_policy(self, scope_id: str, *, expected_revision: int,
                         tools: dict[str, Any]) -> dict[str, Any]:
        self.policy(scope_id)
        validated = self._validated_tools(tools)
        async with self.lock:
            current = self.policy(scope_id)
            if current["revision"] != expected_revision:
                raise RuntimeError("Policy revision conflict")
            next_value = {"scope_id": scope_id,
                          "revision": expected_revision + 1, "tools": validated,
                          "updated_at": beijing_now_iso()}
            payload = {**self.policies, scope_id: next_value}
            temp = self.policy_path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.policy_path)
            self.policies = payload
            return next_value

    def effective(self, worker_id: str) -> dict[str, Any]:
        base = self.policy("global")
        override = self.policy(f"worker:{worker_id}")
        effective: dict[str, dict[str, Any]] = {}
        for name in ACTIVE_TOOLS:
            defaults = {
                "enabled": False, "mode": "on_media_handoff" if name == "media_delivery_v1" else "lightweight",
                "unresolved_after_ms": 20_000, "max_snapshots": 8,
            }
            effective[name] = {**defaults, **base["tools"].get(name, {}),
                               **override["tools"].get(name, {})}
        revision = f'{base["revision"]}:{override["revision"]}'
        return {"schema_version": 1, "worker_id": worker_id,
                "policy_revision": revision, "tools": effective}

    def _file(self, request_id: str) -> Path:
        return self.events_root / f"{_safe_id(request_id)}.jsonl"

    async def ingest(self, *, request_id: str, worker_id: str,
                     client_id: str, message: dict[str, Any],
                     tab_id: int | None = None, window_id: int | None = None,
                     effective_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = _safe_id(request_id)
        if len(json.dumps(message, ensure_ascii=False)) > MAX_JSON_BYTES:
            raise ValueError("Probe message too large")
        tool_id = str(message.get("tool_id") or "")
        if tool_id not in ACTIVE_TOOLS:
            raise ValueError("Unsupported tool")
        # The authenticated server's dispatch-time policy is authoritative.
        # Mid-request edits must not discard evidence from an already enabled run.
        policy = effective_policy if effective_policy is not None else self.effective(worker_id)
        if policy.get("worker_id") != worker_id or policy.get("schema_version") != 1:
            raise PermissionError("Probe policy Worker mismatch")
        if not policy.get("tools", {}).get(tool_id, {}).get("enabled"):
            raise PermissionError("Probe disabled for this Worker")
        stage = str(message.get("stage") or "")
        if stage not in STAGES:
            raise ValueError("Unknown probe stage")
        seq = message.get("sequence")
        if type(seq) is not int or not 1 <= seq <= MAX_EVENTS_PER_REQUEST:
            raise ValueError("Invalid sequence")
        classification = str(message.get("classification") or "capture_unavailable")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise ValueError("Invalid classification")
        run_id = "probe_run_" + hashlib.sha256(
            f"{request_id}:{client_id}".encode("utf-8")
        ).hexdigest()[:20]
        event = {
            "event_id": f"probe_evt_{seq:04d}_{tool_id}",
            "probe_run_id": run_id,
            "tab_id": tab_id if type(tab_id) is int else None,
            "window_id": window_id if type(window_id) is int else None,
            "schema_version": 1, "request_id": request_id,
            "worker_id": worker_id, "client_id": client_id,
            "tool_id": tool_id, "tool_version": "1.0.0",
            "policy_revision": policy["policy_revision"],
            "sequence": seq, "stage": stage, "occurred_at": beijing_now_iso(),
            "capture_status": "ok", "classification": classification,
            "facts": _sanitize_facts(message.get("facts")),
        }
        path = self._file(request_id)
        async with self.lock:
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            if len(lines) >= MAX_EVENTS_PER_REQUEST:
                return {"accepted": False, "reason": "evidence-limit-reached"}
            if any((lambda item: item.get("tool_id") == tool_id and item.get("sequence") == seq)(
                json.loads(line)) for line in lines):
                return {"accepted": True, "deduplicated": True}
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return {"accepted": True}

    def events(self, request_id: str) -> list[dict[str, Any]]:
        path = self._file(request_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def summary(self, request_id: str) -> dict[str, Any]:
        events = self.events(request_id)
        tools = Counter(event["tool_id"] for event in events)
        classification = Counter(event["classification"] for event in events)
        latest = events[-1] if events else None
        return {
            "request_id": request_id,
            "capture_status": "ok" if events else "not_collected",
            "event_count": len(events), "tool_counts": dict(tools),
            "classifications": dict(classification),
            "last_stage": latest["stage"] if latest else None,
            "last_facts": latest["facts"] if latest else None,
        }

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        paths = sorted(self.events_root.glob("*.jsonl"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        return [self.summary(path.stem) for path in paths[:max(1, min(limit, 100))]]

    def archive(self, request_id: str, request_summary: dict[str, Any] | None = None) -> bytes:
        summary = self.summary(request_id)
        events = self.events(request_id)
        artifacts: dict[str, bytes] = {
            "probe-summary.json": json.dumps(summary, ensure_ascii=False, indent=2).encode(),
            "events/probe-timeline.jsonl": "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in events
            ).encode(),
        }
        if request_summary:
            allowed = ("request_id", "status", "request_type", "requested_model",
                       "client_id", "device_name", "recorded_at", "updated_at", "error")
            artifacts["request-summary.json"] = json.dumps(
                {key: request_summary.get(key) for key in allowed},
                ensure_ascii=False, indent=2,
            ).encode()
        tool_files = {
            "media_delivery_v1": "media-delivery",
            "conversation_dom_v1": "conversation-dom",
            "network_evidence_v1": "network-evidence",
            "model_tool_state_v1": "model-tool-state",
            "selector_compat_v1": "selector-compat",
            "abcd_richtext_v1": "abcd-a-richtext",
            "abcd_images_v1": "abcd-b-images",
            "abcd_files_v1": "abcd-c-files",
            "abcd_context_v1": "abcd-d-context",
            "response_boundary_v1": "response-boundary",
        }
        for tool, name in tool_files.items():
            items = [event for event in events if event["tool_id"] == tool]
            artifacts[f"probes/{name}.json"] = json.dumps(
                {"tool_id": tool, "capture_status": "ok" if items else "not_collected",
                 "events": items}, ensure_ascii=False, indent=2,
            ).encode()
        checksums = {name: hashlib.sha256(blob).hexdigest() for name, blob in artifacts.items()}
        artifacts["manifest.json"] = json.dumps({
            "schema_version": 1, "request_id": request_id,
            "generated_at": beijing_now_iso(), "redaction_version": 1,
            "capture_status": summary["capture_status"],
            "checksums": checksums,
        }, ensure_ascii=False, indent=2).encode()
        artifacts["checksums.sha256"] = "".join(
            f"{hashlib.sha256(blob).hexdigest()}  {name}\n" for name, blob in sorted(artifacts.items())
        ).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, blob in sorted(artifacts.items()):
                archive.writestr(name, blob)
        return out.getvalue()
