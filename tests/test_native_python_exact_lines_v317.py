"""Regression: exact Python CodeMirror text and DOM order are integrity facts.

A few import/def words surviving with all whitespace removed cannot prove a
faithful OpenAI native-layout sidecar. These tests are read-only: only the
server's existing finality function decides whether it is verified.
"""
from app.native_snapshot_finality_v282 import (
    native_code_coverage, native_finality_decision,
)

PROOF = {
    "native_snapshot_terminal_complete": True,
    "native_capture_revision": 7,
    "native_snapshot_received_revision": 7,
    "native_final_capture_committed": True,
    "native_chunk_complete": True,
    "native_snapshot_persisted": True,
    "native_reply_scope_verified": True,
}
SRC = "import os\nfrom pathlib import Path\n\ndef main():\n    print('hello')"
MARKDOWN = "## Sample\n\n```python\n" + SRC + "\n```\n\nText after code."


def _snapshot(*blocks):
    return {
        "version": 1, "revision": 7,
        "root": {"tag": "div", "children": [
            {"tag": "pre", "children": [
                {"tag": "code", "children": [
                    {"tag": "span", "text": part}
                    for part in block
                ]}
            ]}
            for block in blocks
        ]},
    }


def _decision(text, snapshot):
    return native_finality_decision(
        {"status": "completed", "response_text": text,
         "diagnostics": dict(PROOF)},
        snapshot,
    )


def test_exact_python_with_ordered_nested_spans_is_covered():
    snapshot = _snapshot(["import os\n", "from pathlib import Path\n",
                          "\n", "def main():\n", "    print('hello')"])
    assert native_code_coverage(
        {"response_text": MARKDOWN}, snapshot,
    ) == "covered"
    result = _decision(MARKDOWN, snapshot)
    assert result["status"] == "verified-final"
    assert result["visually_verified"] is False


def test_same_words_with_lost_newlines_cannot_claim_exact_code():
    compact = "import os from pathlib import Path def main(): print('hello')"
    result = _decision(MARKDOWN, _snapshot([compact]))
    assert result["reason"] == "missing-code-format"


def test_same_code_with_lost_python_indentation_must_reject():
    wrong = SRC.replace("    print('hello')", "print('hello')")
    result = _decision(MARKDOWN, _snapshot([wrong]))
    assert result["reason"] == "missing-code-format"


def test_python_block_missing_last_expression_rejects_even_with_imports():
    result = _decision(MARKDOWN, _snapshot([SRC[:SRC.index("    print")]]))
    assert result["reason"] == "missing-code-format"


def test_every_python_fence_needs_distinct_ordered_native_code_subtree():
    one = "import os\nprint('one')"
    two = "from pathlib import Path\nprint('two')"
    response = "```python\n" + one + "\n```\n```py\n" + two + "\n```"
    assert _decision(response, _snapshot([one]))["reason"] == "missing-code-format"
    assert _decision(response, _snapshot([two], [one]))["reason"] == "missing-code-format"
    assert _decision(response, _snapshot([one], [two]))["status"] == "verified-final"
    # Repeating a Python fence requires two independent native locations.
    duplicate = "```python\n" + one + "\n```\n```python\n" + one + "\n```"
    assert _decision(duplicate, _snapshot([one]))["reason"] == "missing-code-format"
    assert _decision(duplicate, _snapshot([one], [one]))["status"] == "verified-final"


def test_python_text_inside_fenced_plain_sample_is_not_live_code():
    text = "```text\n```python\nimport os\n```\nSome prose\n"
    assert native_code_coverage(
        {"response_text": text}, _snapshot(),
    ) == "unverified"


def test_long_code_missing_boundaries_keeps_broker_business_terminal_completed():
    row = {"status": "completed", "response_text": MARKDOWN,
           "diagnostics": dict(PROOF)}
    assert native_finality_decision(row, _snapshot([SRC.replace("\n", "")]))["status"] == "rejected-final"
    assert row["status"] == "completed"
