"""2026-11-07 req_5692be71: DOM snapshot ACKed but Python code omitted."""
from app.native_snapshot_finality_v282 import native_code_coverage, native_snapshot_finality

GOOD = {
    "native_snapshot_terminal_complete": True,
    "native_capture_revision": 2,
    "native_snapshot_received_revision": 2,
    "native_snapshot_persisted": True,
    "native_final_capture_committed": True,
    "native_chunk_complete": True,
    "native_reply_scope_verified": True,
}

RAW_PYTHON = (
    "## 可运行的Python模型\n\nPython\n\n"
    "import math\n\ndef check(value):\n\n    return math.isfinite(value)\n"
)
CODE = "import math\ndef check(value):\n    return math.isfinite(value)"


def test_native_snapshot_rejects_missing_editor_body_but_keeps_completed_api():
    row = {"status": "completed", "response_text": RAW_PYTHON,
           "diagnostics": dict(GOOD)}
    original = {"version": 1, "revision": 2, "root": {
        "tag": "div", "children": [
            {"tag": "h2", "text": "可运行的Python模型"},
            {"tag": "div", "children": [{"tag": "span", "text": "Python"}]},
        ],
    }}
    assert native_code_coverage(row, original) == "missing-code-block"
    assert native_snapshot_finality(row, original) == "rejected-final"
    assert row["status"] == "completed", "native presentation cannot change API terminal"


def test_real_pre_code_editor_restores_content_coverage():
    row = {"status": "completed", "response_text": RAW_PYTHON,
           "diagnostics": dict(GOOD)}
    native = {"version": 1, "revision": 2, "root": {"tag": "div", "children": [
        {"tag": "pre", "children": [{"tag": "code", "children": [
            {"tag": "span", "text": CODE},
        ]}]},
    ]}}
    assert native_code_coverage(row, native) == "covered"
    assert native_snapshot_finality(row, native) == "verified-final"


def test_partially_virtualized_code_editor_is_not_a_valid_native_final():
    row = {"status": "completed", "response_text": RAW_PYTHON,
           "diagnostics": dict(GOOD)}
    partial = {"version": 1, "revision": 2, "root": {"tag": "pre",
        "children": [{"tag": "code", "text": "import math"}]}}
    assert native_code_coverage(row, partial) == "missing-code-lines"
    assert native_snapshot_finality(row, partial) == "rejected-final"


def test_no_code_claim_does_not_break_legacy_normal_reply():
    row = {"status": "completed", "response_text": "## 你好\n\n文字回复",
           "diagnostics": dict(GOOD)}
    plain = {"version": 1, "revision": 2,
             "root": {"tag": "div", "children": [{"tag": "p", "text": "文字回复"}]}}
    assert native_code_coverage(row, plain) == "unverified"
    assert native_snapshot_finality(row, plain) == "verified-final"


def test_code_match_stops_at_32_signatures_and_no_untrusted_content_gating():
    row = {"status": "completed", "diagnostics": dict(GOOD)}
    snap = {"version": 1, "revision": 2,
            "root": {"tag": "div", "text": "No code here"}}
    assert native_code_coverage(row, snap) == "unverified"
    assert native_snapshot_finality(row, snap) == "verified-final"
