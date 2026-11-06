"""Finality guard for the non-authoritative native-layout sidecar.

A prior incremental revision can be valid and persisted even if the final
request-owned capture fails. This is visible in historical req_3a882bb5:
revision 12, 333 nodes, structural-max-children, chunk_complete=False,
10 archived images and no bound native image slots. The canonical OpenAI
terminal remains completed, but the old layout is NOT a final native reply.

Do not suppress an in-progress stream's provisional revision. After terminal,
reject a *proven* failed/stale final snapshot from public native delivery while
keeping it available in the authenticated administrator forensic endpoint.
Legacy records with no finality evidence remain explicitly unverified instead
of falsely marked verified; public backward compatibility is preserved.
"""
from __future__ import annotations

from typing import Any

TERMINAL = frozenset({"completed", "error", "cancelled", "stalled"})
FAILURE_FIELDS = ("native_capture_failure", "native_structural_failure",
                  "native_transport_ack_error")


def native_snapshot_finality(row: dict[str, Any] | None,
                             snapshot: dict[str, Any] | None) -> str:
    """One of live-provisional, verified-final, rejected-final, unverified-final.

    This is presentation truth only: it must never change the Broker terminal,
    durable native snapshot, request history, or generated-file state.
    """
    if not isinstance(row, dict) or row.get("status") not in TERMINAL:
        return "live-provisional"
    if row.get("status") != "completed" or not isinstance(snapshot, dict):
        return "rejected-final"

    diag = row.get("diagnostics")
    if not isinstance(diag, dict):
        return "unverified-final"

    if diag.get("native_snapshot_terminal_complete") is False:
        return "rejected-final"
    if any(isinstance(diag.get(field), str) and diag[field].strip()
           for field in FAILURE_FIELDS):
        return "rejected-final"
    for field in ("native_final_capture_committed", "native_chunk_complete",
                  "native_snapshot_persisted", "native_reply_scope_verified"):
        if field in diag and diag[field] is False:
            return "rejected-final"

    revision = snapshot.get("revision")
    captured_revision = diag.get("native_capture_revision")
    if captured_revision is not None and (
        type(captured_revision) is not int or captured_revision != revision
    ):
        return "rejected-final"
    received_revision = diag.get("native_snapshot_received_revision")
    if received_revision is not None and (
        type(received_revision) is not int or received_revision != revision
    ):
        return "rejected-final"

    if diag.get("native_snapshot_terminal_complete") is True:
        return "verified-final"
    if (diag.get("native_final_capture_committed") is True
            and diag.get("native_chunk_complete") is True
            and diag.get("native_snapshot_persisted") is True
            and diag.get("native_capture_revision") == revision):
        # Older Server versions may predate the aggregate finality flag but
        # still carry all positive same-revision transport evidence.
        return "verified-final"
    return "unverified-final"


def native_terminal_view(row: dict[str, Any], broker_state: Any) -> dict[str, Any]:
    """Use a request-owned Broker terminal even before telemetry is finalized.

    A Worker terminal may precede 180 seconds of file archival while the row
    still says running. Only correlate a Broker state when its client id
    *exactly* equals the existing telemetry row's Worker client id. Never
    allow an unrelated/new same-header request to override an older row.
    """
    if not isinstance(row, dict) or row.get("status") in TERMINAL:
        return row
    if broker_state is None:
        return row
    terminal = getattr(broker_state, "terminal_event_type", None)
    if terminal not in {"chat.completed", "chat.error", "chat.cancelled"}:
        return row
    client_id = row.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        return row
    if getattr(broker_state, "client_id", None) != client_id:
        return row

    observed = getattr(broker_state, "diagnostics", None)
    prior = row.get("diagnostics")
    merged = dict(prior) if isinstance(prior, dict) else {}
    if isinstance(observed, dict):
        merged.update(observed)
    return {**row, "status": "completed" if terminal == "chat.completed" else "error",
            "diagnostics": merged}
