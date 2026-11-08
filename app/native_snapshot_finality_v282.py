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
import re

TERMINAL = frozenset({"completed", "error", "cancelled", "stalled"})
FAILURE_FIELDS = ("native_capture_failure", "native_structural_failure",
                  "native_transport_ack_error")


_CODE_LINE = re.compile(
    r"(?m)^(?:import [A-Za-z_][A-Za-z_0-9.]*(?:\s+as\s+\w+)?"
    r"|from [A-Za-z_][A-Za-z_0-9.]* import [A-Za-z_0-9*, ]+"
    r"|(?:async\s+)?def [A-Za-z_]\w*\([^\n]{0,160}\)\s*:)"
)


def native_code_coverage(row: dict[str, Any] | None,
                         snapshot: dict[str, Any] | None) -> str:
    """Conservative content-level gate for code that the native tree omitted.

    The 2026-11-07 A-scene carried 17,080 Markdown characters but the native
    DOM tree had only 11,574 raw text characters. A visible Python heading
    survived, while the entire editor code body (imports/functions) was absent.
    A structurally complete/ACKed snapshot is not necessarily content complete.
    Return no-claim when we lack enough distinct executable code signatures.
    """
    if not isinstance(row, dict) or not isinstance(snapshot, dict):
        return "unverified"
    response = row.get("response_text")
    if not isinstance(response, str) or not 1 <= len(response) <= 300000:
        return "unverified"
    signatures = list(dict.fromkeys(_CODE_LINE.findall(response)))[:32]
    if len(signatures) < 2:
        return "unverified"
    nodes = [snapshot.get("root")]
    pre_text: list[str] = []
    visited = 0
    while nodes and visited < 30000:
        node = nodes.pop()
        if not isinstance(node, dict):
            continue
        visited += 1
        if node.get("tag") == "pre":
            text_nodes = [node]
            while text_nodes:
                sub = text_nodes.pop()
                if not isinstance(sub, dict):
                    continue
                if isinstance(sub.get("text"), str):
                    pre_text.append(sub["text"])
                text_nodes.extend(sub.get("children") or [])
        else:
            nodes.extend(node.get("children") or [])
    if visited >= 30000:
        return "unverified"
    rendered_code = re.sub(r"\s+", "", "".join(pre_text))
    if not rendered_code:
        return "missing-code-block"
    missing = [
        line for line in signatures
        if re.sub(r"\s+", "", line) not in rendered_code
    ]
    return "missing-code-lines" if missing else "covered"


def native_finality_decision(
    row: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """ONLY server-side authority for optional native reply publication.

    Returns a stable decision AND its first blocking reason. Every route and
    forensic exporter must call this same pure function; nobody may combine
    independent booleans to override a rejected finality. Never changes the
    Broker request terminal, Worker emission, SSE or archive state.
    """
    def decide(status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status, "reason": reason,
            "authority": "server-native-finality-v298",
            "scope": "native-sidecar-only",
            "visually_verified": False,
        }

    if not isinstance(row, dict) or row.get("status") not in TERMINAL:
        return decide("live-provisional", "broker-request-not-terminal")
    if row.get("status") != "completed":
        return decide("rejected-final", "broker-request-not-completed")
    if not isinstance(snapshot, dict):
        return decide("rejected-final", "final-native-snapshot-missing")
    diag = row.get("diagnostics")
    if not isinstance(diag, dict):
        return decide("unverified-final", "native-finality-proof-missing")

    coverage = native_code_coverage(row, snapshot)
    if coverage in {"missing-code-block", "missing-code-lines"}:
        return decide("rejected-final", coverage)

    # Private page DOM is evidence only. Comparing structures is allowed
    # only when its request ownership AND capture completeness are proven.
    comparison = diag.get("native_capture_comparison")
    if isinstance(comparison, dict) and (
        comparison.get("comparable") is True and
        comparison.get("private_capture_complete") is True
    ):
        private_tables = comparison.get("private_tables")
        native_tables = comparison.get("native_tables")
        if (type(private_tables) is int and type(native_tables) is int
                and private_tables > native_tables >= 0):
            return decide("rejected-final", "same-request-table-loss")

    layout = diag.get("native_layout_summary")
    if (isinstance(layout, dict)
            and type(layout.get("archived_assets")) is int
            and layout["archived_assets"] > 0):
        count = layout["archived_assets"]
        if layout.get("media_mapping_complete") is not True:
            return decide("rejected-final", "unmapped-archived-media")
        # A sequential media_index array is *not* a SHA256 identity proof.
        # An ordinal/dimension-only match must never qualify as 1:1.
        if (diag.get("native_image_identity_status") != "verified-sha256"
                or type(diag.get("native_identity_matched_count")) is not int
                or diag["native_identity_matched_count"] != count):
            return decide("rejected-final", "unverified-media-byte-identity")

    # CodeMirror editor probe provides EVIDENCE, not an alternative finality.
    editors = diag.get("native_code_editor_state_candidates")
    resolved = diag.get("native_code_editor_state_resolved")
    if type(editors) is int and editors > 0:
        if type(resolved) is not int or resolved != editors:
            return decide("rejected-final", "incomplete-editor-state")
        # Resolving editor state is only input evidence, not proof that the
        # source reached the canonical OpenAI text. request-v6 is the sole
        # canonical output owner; its post-splice count must match *all*
        # request-owned editors. Missing/older diagnostics fail closed for
        # optional native finality, without changing API business completion.
        integrated = diag.get("native_canonical_code_integrated")
        if type(integrated) is not int or integrated != editors:
            return decide("rejected-final", "canonical-editor-code-not-delivered")

    if diag.get("native_snapshot_terminal_complete") is False:
        return decide("rejected-final", "final-capture-incomplete")
    for key in FAILURE_FIELDS:
        if isinstance(diag.get(key), str) and diag[key].strip():
            return decide("rejected-final", "capture-error-" + key)
    for key in (
        "native_final_capture_committed", "native_chunk_complete",
        "native_snapshot_persisted", "native_reply_scope_verified",
    ):
        if key in diag and diag[key] is False:
            return decide("rejected-final", "failed-proof-" + key)

    revision = snapshot.get("revision")
    for name in ("native_capture_revision", "native_snapshot_received_revision"):
        value = diag.get(name)
        if value is not None and (type(value) is not int or value != revision):
            return decide("rejected-final", "revision-conflict-" + name)

    if diag.get("native_snapshot_terminal_complete") is True:
        return decide("verified-final", "verified-terminal-revision")
    # Old releases may predate the aggregate flag. Their positive, identical
    # revision ACKs remain a compatibility path through THIS one function.
    if (diag.get("native_final_capture_committed") is True
            and diag.get("native_chunk_complete") is True
            and diag.get("native_snapshot_persisted") is True
            and diag.get("native_capture_revision") == revision):
        return decide("verified-final", "legacy-same-revision-proof")
    return decide("unverified-final", "positive-finality-proof-missing")


def native_snapshot_finality(row: dict[str, Any] | None,
                             snapshot: dict[str, Any] | None) -> str:
    """Backward-compatible projection of the SINGLE native authority."""
    return native_finality_decision(row, snapshot)["status"]


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
