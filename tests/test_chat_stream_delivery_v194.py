"""2026-10-24 TX03 A: provisional reasoning must not steal final SSE."""
from pathlib import Path

from app.chat_stream_delivery_v194 import (
    FINAL_CHUNK_CHARS,
    MAX_PREVIEW_CHARS,
    StagedStreamText,
    final_text_chunks,
)

ROOT = Path(__file__).resolve().parents[1]


def test_preview_is_not_final_answer_and_is_bounded():
    staging = StagedStreamText()
    assert staging.preview_delta("页面正在生成", enabled=False) == ""
    assert staging.preview_delta("我会先分析思路", enabled=True) == "我会先分析思路"
    assert staging.preview_delta("字" * 5000, enabled=True) == "字" * (MAX_PREVIEW_CHARS - len("我会先分析思路"))
    assert staging.preview_delta("后续", enabled=True) == ""


def test_authoritative_terminal_fully_delivered_when_preview_diverges():
    # Real failure: chat2api recorded 5,247 characters yet the client got
    # only a 1-2 paragraph provisional synopsis and then [DONE].
    provisional = "我会我会 Center V2 负责结构化知识检索、本地直答和知识迭代。"
    final = "# qnbot 千牛 AI 客服项目架构与优化分析\n" + "正式正文及表格、代码。\n" * 380
    assert not final.startswith(provisional)
    staging = StagedStreamText()
    assert staging.preview_delta(provisional, enabled=True) == provisional
    delivered = "".join(final_text_chunks(final))
    assert delivered == final
    assert len(delivered) == len(final)


def test_final_chunks_have_complete_order_and_no_silent_truncation():
    text = "中文、Markdown **strong**\n" * 3000
    chunks = list(final_text_chunks(text))
    assert chunks
    assert all(0 < len(c) <= FINAL_CHUNK_CHARS for c in chunks)
    assert "".join(chunks) == text
    assert list(final_text_chunks("")) == []


def test_main_stream_does_not_use_unretractable_prefix_reconciliation():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    start = source.index("    async def stream_request(")
    end = source.index('    @app.post("/v1/chat/completions")', start)
    stream = source[start:end]
    assert "final_text.startswith(sent_text)" not in stream
    assert 'chunk_payload(response_id, model, {"reasoning": preview})' in stream
    assert 'for delta in final_text_chunks(final_text):' in stream
    assert 'chunk_payload(response_id, model, {"content": delta})' in stream
    assert 'chunk_payload(response_id, model, {}, "stop", usage, meta)' in stream
