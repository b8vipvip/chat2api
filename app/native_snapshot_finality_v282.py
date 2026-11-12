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


def _canonical_python_fences(response: str) -> list[str]:
    """Ordered, explicit Python fences from canonical OpenAI Markdown.

    Never mistake an English paragraph mentioning Python or a fenced text
    sample containing backticks for a source code artifact. Whitespace and
    newlines inside each code fence are semantically significant.
    """
    sources: list[str] = []
    fence_char, fence_len = "", 0
    python_source = False
    body: list[str] = []
    for line in response.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        marker = _MARKDOWN_FENCE.fullmatch(line)
        if not fence_char:
            if marker:
                run, suffix = marker.groups()
                fence_char, fence_len = run[0], len(run)
                python_source = bool(re.match(
                    r"(?i)^(?:python(?:3(?:\.\d+)?)?|py)(?:\s|$)",
                    suffix.strip(),
                ))
                body = []
            continue
        if (marker and marker.group(1)[0] == fence_char
                and len(marker.group(1)) >= fence_len
                and not marker.group(2).strip()):
            if python_source:
                sources.append("\n".join(body))
            fence_char, fence_len = "", 0
            python_source = False
            body = []
            continue
        if python_source:
            body.append(line)
    # An unclosed source fence is not an exact canonical code artifact.
    if fence_char and python_source:
        sources.append("\n".join(body) + "\n<unclosed-python-fence>")
    return sources


def native_code_coverage(row: dict[str, Any] | None,
                         snapshot: dict[str, Any] | None) -> str:
    """Check canonical Python source against actual native pre/code line order.

    An ACKed native tree is not code-complete merely because it contains the
    right import/def tokens after removing all whitespace. In the actual
    CodeMirror failure the visible editor could omit indentation/newlines
    while legacy token matching still reported covered.
    """
    if not isinstance(row, dict) or not isinstance(snapshot, dict):
        return "unverified"
    response = row.get("response_text")
    if not isinstance(response, str) or not 1 <= len(response) <= 300000:
        return "unverified"
    signatures = list(dict.fromkeys(_CODE_LINE.findall(response)))[:32]
    python_sources = _canonical_python_fences(response)
    if len(signatures) < 2 and not python_sources:
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
            # Keep children in original DOM order. An ordinary LIFO walk
            # reverses sibling spans and cannot prove exact code placement.
            descendants = [node]
            pieces: list[str] = []
            while descendants and visited < 30000:
                sub = descendants.pop()
                if not isinstance(sub, dict):
                    continue
                visited += 1
                if isinstance(sub.get("text"), str):
                    pieces.append(sub["text"])
                descendants.extend(reversed(sub.get("children") or []))
            pre_text.append("".join(pieces).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n"))
        else:
            nodes.extend(reversed(node.get("children") or []))
    if visited >= 30000:
        return "unverified"
    if python_sources:
        # Each distinct fence requires a distinct, correctly ordered native
        # pre subtree. Never let a single similar prefix/token set stand in
        # for the full CodeMirror state or an additional missing card.
        next_pre = 0
        for source in python_sources:
            exact = source.rstrip("\n")
            if not exact:
                return "missing-code-block"
            found = next((i for i in range(next_pre, len(pre_text))
                          if pre_text[i] == exact), None)
            if found is None:
                return "missing-code-format"
            next_pre = found + 1
        return "covered"

    # Legacy, unlabeled source: keep the historical signature-only check
    # explicitly distinct from a proved exact fenced source. It must not
    # override the strict Python-fence path above.
    rendered_code = re.sub(r"\s+", "", "".join(pre_text))
    if not rendered_code:
        return "missing-code-block"
    missing = [
        line for line in signatures
        if re.sub(r"\s+", "", line) not in rendered_code
    ]
    return "missing-code-lines" if missing else "covered"

# A real 2026-11-09 request produced an approximately 768×789 rendered
# Mermaid visual inside the native assistant reply, but standard Markdown
# contained only the standalone string "Mermaid diagram" between paragraphs.
# The native sidecar ACK and equal 19-table counts were TRUE, not proof that
# an opaque embedded diagram was delivered. Never call this verified-final
# merely because a visual placeholder survived the text serializer.
_MERMAID_PLACEHOLDER = re.compile(r"^ {0,3}Mermaid diagram[ \t]*$")
_MARKDOWN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$")


def native_diagram_coverage(row: dict[str, Any] | None) -> str:
    """Detect EACH missing diagram slot, not whether another diagram exists.

    Real req_d4d49 lost a rendered 768x789 diagram as a standalone
    "Mermaid diagram" label. A subsequent actual fenced Mermaid chart in
    the same reply is a *different slot*; its source cannot prove that the
    preceding opaque figure was delivered. The prior v310 "no fence anywhere"
    rule falsely passed such partial multi-diagram answers.

    Scan only Markdown OUTSIDE code fences. Code examples containing the
    literal label, and indented 4-space code blocks, are not missing diagrams.
    Dynamic request-local evidence only; no prescribed chart count and no
    fabricated Mermaid code, SVG pixels or second Broker terminal.
    """
    if not isinstance(row, dict):
        return "unverified"
    response = row.get("response_text")
    if not isinstance(response, str) or not response:
        return "unverified"
    fence_char = ""
    fence_len = 0
    for line in response.splitlines():
        marker = _MARKDOWN_FENCE.fullmatch(line)
        if marker:
            run, suffix = marker.groups()
            if not fence_char:
                fence_char, fence_len = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_len and not suffix.strip():
                fence_char, fence_len = "", 0
            continue
        if not fence_char and _MERMAID_PLACEHOLDER.fullmatch(line):
            return "mermaid-placeholder-without-source"
    return "no-proven-loss"


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

    # The optional native layout's terminal ACK is not evidence that an
    # opaque embedded Mermaid diagram survived in OpenAI Markdown/SSE.
    # Keep the standard API completion intact; reject only false sidecar
    # "verified-final" when the chart has been replaced by UI placeholder.
    if native_diagram_coverage(row) == "mermaid-placeholder-without-source":
        return decide("rejected-final", "mermaid-diagram-source-omitted")

    coverage = native_code_coverage(row, snapshot)
    if coverage in {"missing-code-block", "missing-code-lines", "missing-code-format"}:
        return decide("rejected-final", coverage)

    # content_native_content_v1.js explicitly discards Canvas, iframes and
    # unsupported SVG elements while counting the omissions in the same
    # request's Worker terminal diagnostics. A terminal DOM ACK can therefore
    # succeed and table/code checks can pass although a rendered chart was
    # dropped. Those counters are evidence for THIS existing sole finality
    # authority, not a second Worker or Broker terminal gate.
    for counter, reason in (
        ("native_canvas_omitted", "canvas-source-omitted"),
        ("native_graphics_unsupported", "native-graphics-unsupported"),
    ):
        value = diag.get(counter)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 30000:
                return decide("rejected-final", "invalid-graphics-coverage-counter")
            if value > 0:
                return decide("rejected-final", reason)

    # A large, request-owned SVG may have been observed but dropped while
    # serializing (e.g. an empty/unsupported subtree). The terminal sidecar
    # cannot be certified until its own safe tree contains each captured SVG
    # root; tiny toolbar icons are not counted as chart roots by the Worker.
    captured_svg = diag.get("native_svg_charts_captured")
    if captured_svg is not None:
        if type(captured_svg) is not int or not 0 <= captured_svg <= 30000:
            return decide("rejected-final", "invalid-graphics-coverage-counter")
        if captured_svg > 0:
            nodes = [snapshot.get("root")]
            observed_svg = 0
            visited = 0
            while nodes and visited < 30000:
                node = nodes.pop()
                if not isinstance(node, dict):
                    continue
                visited += 1
                if node.get("tag") == "svg":
                    observed_svg += 1
                nodes.extend(reversed(node.get("children") or ()))
            if visited >= 30000 or observed_svg < captured_svg:
                return decide("rejected-final", "svg-chart-capture-gap")

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
                and private_tables >= 0 and native_tables >= 0):
            # The expected number is sampled from *this exact response*,
            # never copied from the preceding ABCD request or template.
            if private_tables > native_tables:
                return decide("rejected-final", "same-request-table-loss")
            if private_tables < native_tables:
                return decide("rejected-final", "same-request-table-duplication")
        large = comparison.get("private_images_displayed_large")
        delivered = comparison.get("native_images")
        missing = comparison.get("possible_missing_large_images")
        if (type(missing) is int and missing > 0
                or type(large) is int and type(delivered) is int
                and large > delivered >= 0):
            return decide("rejected-final", "same-request-large-image-loss")

    layout = diag.get("native_layout_summary")
    if isinstance(layout, dict):
        # A final ACK cannot validate counters from a previous native revision.
        # The snapshot is request-owned; the layout summary must belong to
        # that exact revision whenever it includes a revision field.
        if ("revision" in layout
                and (type(layout["revision"]) is not int
                     or layout["revision"] != snapshot.get("revision"))):
            return decide("rejected-final", "revision-conflict-native-layout-summary")
        slots = layout.get("images")
        archived = layout.get("archived_assets")
        # Images can render in native DOM before originals have been archived.
        # A zero-item archive must NEVER make a positive native finality claim
        # about a response containing image slots; absent bytes cannot prove
        # image identity. Leave Broker/stream success and optional archive
        # progress untouched.
        if type(slots) is int and slots > 0 and archived == 0:
            return decide("rejected-final", "native-images-without-archived-originals")
    if (isinstance(layout, dict)
            and type(layout.get("archived_assets")) is int
            and layout["archived_assets"] > 0):
        count = layout["archived_assets"]
        # Structural media_index coverage is only an ordinal hint. One
        # original image can occupy several distinct request-owned DOM slots
        # while the archive contains just one SHA256-verified original. The
        # old images==archived_assets check rejected such valid duplication
        # even when the *byte identity* of every slot was proven.
        slots = layout.get("images")
        if type(slots) is not int or slots < 0:
            slots = count  # Legacy telemetry without an explicit slot count.
        identity_verified = (
            diag.get("native_image_identity_status") == "verified-sha256"
            and type(diag.get("native_identity_matched_count")) is int
            and diag["native_identity_matched_count"] == slots
            and (type(diag.get("native_identity_dom_image_count")) is not int
                 or diag["native_identity_dom_image_count"] == slots)
            and (type(diag.get("native_identity_archived_count")) is not int
                 or diag["native_identity_archived_count"] == count)
        )
        # The server-side SHA mapper guarantees all DOM positions match exact
        # original bytes AND every distinct archived SHA is represented.
        # It also rejects missing, out-of-order or guessed media_index slots.
        # Never upgrade a dimensions-only or partial hash candidate.
        if layout.get("media_mapping_complete") is not True and not identity_verified:
            return decide("rejected-final", "unmapped-archived-media")
        if not identity_verified:
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
    # Matching only Worker identity is insufficient: one Worker may handle
    # another request immediately after the first, and its late state must
    # never authorize a different reply's native-finality projection.
    row_request_id = row.get("request_id")
    if (not isinstance(row_request_id, str) or not row_request_id
            or getattr(broker_state, "request_id", None) != row_request_id):
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
    business_status = {
        "chat.completed": "completed",
        "chat.error": "error",
        "chat.cancelled": "cancelled",
    }[terminal]
    return {**row, "status": business_status, "diagnostics": merged}
