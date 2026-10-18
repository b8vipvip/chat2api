"""Bounded, newest-first reader for append-only JSONL histories.

Legacy startup readers materialized every byte of request_history.jsonl and
server_events.jsonl. On busy installations those files can exceed 600 MB each,
temporarily consuming multiple gigabytes and triggering the host OOM killer.

This reader only seeks over a bounded tail and caps individual and total
decoded record bytes. Original files remain unchanged on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def _tail_lines(
    path: Path,
    *,
    scan_limit_bytes: int,
    line_limit_bytes: int,
    block_size: int = 64 * 1024,
) -> Iterator[bytes]:
    """Yield complete lines from newest to oldest, without reading the full file.

    If the scan limit cuts through a line, the incomplete oldest line is
    discarded. Likewise, any single line exceeding line_limit_bytes is
    discarded rather than accumulated in memory.
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        scanned = 0
        pending = b""
        pending_too_large = False

        while position > 0 and scanned < scan_limit_bytes:
            count = min(block_size, position, scan_limit_bytes - scanned)
            position -= count
            handle.seek(position)
            block = handle.read(count)
            scanned += len(block)
            parts = block.split(b"\n")

            if len(parts) == 1:
                if not pending_too_large:
                    if len(parts[0]) + len(pending) <= line_limit_bytes:
                        pending = parts[0] + pending
                    else:
                        pending = b""
                        pending_too_large = True
                continue

            newest = parts[-1]
            if not pending_too_large and len(newest) + len(pending) <= line_limit_bytes:
                yield newest + pending
            for complete in reversed(parts[1:-1]):
                if len(complete) <= line_limit_bytes:
                    yield complete

            oldest = parts[0]
            pending_too_large = len(oldest) > line_limit_bytes
            pending = b"" if pending_too_large else oldest

        # Only a scan that reaches the file beginning has a complete oldest line.
        if position == 0 and not pending_too_large:
            yield pending


def iter_recent_jsonl(
    path: Path,
    *,
    scan_limit_bytes: int = 64 * 1024 * 1024,
    line_limit_bytes: int = 4 * 1024 * 1024,
    retained_limit_bytes: int = 24 * 1024 * 1024,
) -> Iterator[dict[str, Any]]:
    """Read recent JSON objects newest-first with bounded startup memory.

    At most scan_limit_bytes bytes of the file tail are examined, with a
    per-line bound and a cap on the total successfully parsed record bytes.
    Invalid and incomplete JSONL records are ignored. Archives and older
    records stay on disk but may not be represented in the in-memory window.
    """
    if min(scan_limit_bytes, line_limit_bytes, retained_limit_bytes) <= 0:
        raise ValueError("JSONL read limits must be positive")

    retained_bytes = 0
    for raw in _tail_lines(
        path,
        scan_limit_bytes=scan_limit_bytes,
        line_limit_bytes=line_limit_bytes,
    ):
        if not raw.strip():
            continue
        if retained_bytes + len(raw) > retained_limit_bytes:
            break
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        retained_bytes += len(raw)
        yield item
