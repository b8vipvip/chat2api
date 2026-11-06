"""Failure-atomic composition for the two post-terminal media handoff stages.

Worker/Broker, not the archive layer, owns completion. Image writes are already
atomic within a single markdown rewrite; deferred generated-file validation may
still fail *after* image publication. The API then has no successful response
containing those image URLs. Compensate only the newly created, same-owner,
same-request image records in that case.
"""
from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)


def archive_mixed_reply(
    text: str, owner: str, request_id: str, base_url: str,
    *, media_archive: Any, deferred_generated_files: Any,
):
    rewritten, media = media_archive.rewrite_markdown(
        text, owner, request_id, base_url
    )
    try:
        # Image rewrite must finish completely before starting asynchronous
        # file staging; otherwise pending file jobs could run for a reply that
        # must ultimately be rejected because of leftover image bytes.
        if "data:image/" in rewritten:
            raise ValueError("Incomplete generated attachment payload")
        rewritten, files = deferred_generated_files.rewrite(
            rewritten, owner, request_id, base_url
        )
        if "data:image/" in rewritten or "![📎 " in rewritten:
            raise ValueError("Incomplete generated attachment payload")
        return rewritten, media, files
    except Exception:
        # Only media returned by THIS successful image-rewrite call are
        # eligible. The independent image rewrite already handles its own
        # partial failures. Never sweep a request-wide namespace, since
        # another terminal or previously delivered response may share it.
        for record in reversed(media):
            media_id = record.get("id") if isinstance(record, dict) else None
            if not isinstance(media_id, str) or not media_id.startswith("med_"):
                continue
            try:
                identity = media_archive.describe(media_id, owner)
                if identity.get("request_id") != request_id:
                    LOG.error("Mixed-reply image rollback owner/request mismatch")
                    continue
                media_archive.delete(media_id)
            except KeyError:
                # Already removed; do not mask the original archive failure.
                continue
            except Exception:
                LOG.exception("Failed to roll back unpublished mixed-reply image")
        raise
