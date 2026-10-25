"""ABCD Worker tools are metadata-only and never become terminal owners."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile

from app.worker_probe_store import WorkerProbeStore, ACTIVE_TOOLS, TOOL_CATALOG, _sanitize_facts


SCENE_TOOLS = (
    "abcd_richtext_v1", "abcd_images_v1", "abcd_files_v1",
    "abcd_context_v1", "response_boundary_v1",
)


def test_abcd_catalog_is_available_and_opt_in(tmp_path):
    store = WorkerProbeStore(tmp_path)
    catalog = {row["id"]: row for row in TOOL_CATALOG}
    for name in SCENE_TOOLS:
        assert name in ACTIVE_TOOLS
        assert catalog[name]["phase"] == "P2"
        assert set(catalog[name]["platforms"]) == {"linux", "win"}
        assert store.effective("ext_test001")["tools"][name]["enabled"] is False


def test_abcd_evidence_is_sanitized_and_exported_per_request(tmp_path):
    store = WorkerProbeStore(tmp_path)
    request_id = "req_abcd201abcdef12345678"
    worker_id = "ext_testworker0001"

    async def run():
        await store.put_policy("worker:" + worker_id, expected_revision=0, tools={
            name: {"enabled": True} for name in SCENE_TOOLS
        })
        policy = store.effective(worker_id)
        for seq, name in enumerate(SCENE_TOOLS, 1):
            result = await store.ingest(
                request_id=request_id, worker_id=worker_id,
                client_id="ext_abcdef123456",
                effective_policy=policy,
                message={
                    "tool_id": name, "sequence": seq, "stage": "before_terminal",
                    "classification": "completed_observed",
                    "facts": {
                        "scene_rich_max_chars": 43600,
                        "scene_candidate_chars": 3793,
                        "scene_uncovered_block_count": 8,
                        "scene_markdown_table_count": 2,
                        "scene_file_pdf_count": 1,
                        "scene_context_turn_count": 2,
                        "private_full_text": "CONFIDENTIAL FULL TEXT",
                        "download_url": "https://private.invalid/file?signature=secret",
                    },
                },
            )
            assert result["accepted"]

    asyncio.run(run())
    summary = store.summary(request_id)
    assert summary["event_count"] == len(SCENE_TOOLS)
    assert all(summary["tool_counts"][name] == 1 for name in SCENE_TOOLS)
    archive = store.archive(request_id)
    with zipfile.ZipFile(io.BytesIO(archive)) as zip_file:
        names = zip_file.namelist()
        assert "probes/abcd-a-richtext.json" in names
        assert "probes/abcd-b-images.json" in names
        assert "probes/abcd-c-files.json" in names
        assert "probes/abcd-d-context.json" in names
        assert "probes/response-boundary.json" in names
        raw = zip_file.read("events/probe-timeline.jsonl").decode()
        assert "CONFIDENTIAL FULL TEXT" not in raw
        assert "private.invalid" not in raw
        assert "3793" in raw
    assert store.summary("req_neverrun0123456789")["capture_status"] == "not_collected"


def test_short_blocks_and_overflow_are_sanitized_as_numbers_only():
    source = {
        "scene_structural_scan_nodes": 1701,
        "scene_structural_scan_overflow": True,
        "scene_short_semantic_blocks": 21,
        "dialogue_transcript": "private dialog MUST NOT BE STORED",
    }
    result = _sanitize_facts(source)
    assert result == {
        "scene_structural_scan_nodes": 1701,
        "scene_structural_scan_overflow": True,
        "scene_short_semantic_blocks": 21,
    }
