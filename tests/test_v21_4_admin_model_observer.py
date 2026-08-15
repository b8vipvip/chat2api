from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_realtime_model_plaza_patch_is_idempotent():
    source = (ROOT / "app" / "admin_v20_2.js").read_text(encoding="utf-8")

    # The model-plaza observer intentionally watches subtree rendering so the
    # realtime cards can be re-applied after searches/refreshes. Every write it
    # performs must therefore be conditional; unconditional innerHTML/textContent
    # writes self-trigger the observer forever and peg the browser main thread.
    assert "function setText(node, value)" in source
    assert "node.textContent !== value" in source
    assert "function setHtml(node, value)" in source
    assert "node.innerHTML !== value" in source
    assert "if (badgeNode) badgeNode.innerHTML =" not in source
    assert "if (useNode) useNode.innerHTML =" not in source
    assert "if (pre) pre.textContent =" not in source


def test_realtime_model_plaza_observer_is_coalesced():
    source = (ROOT / "app" / "admin_v20_2.js").read_text(encoding="utf-8")

    assert "let patchScheduled = false" in source
    assert "if (patchScheduled) return" in source
    assert "setTimeout(() =>" in source
    assert "patchModelPlaza();" in source
