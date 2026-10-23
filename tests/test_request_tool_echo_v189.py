from app.media_handoff import is_internal_tool_handoff


def test_worker_tool_utterances_never_become_user_chat_stream():
    assert is_internal_tool_handoff('{"query":"用户要求回顾已生成的图片和文件"}')
    assert is_internal_tool_handoff(
        "bash -lc cat /home/oai/skills/pdfs/SKILL.md && printf '---DOCX---'")
    assert is_internal_tool_handoff('{"size":"1024x1536","n":3,"transparent_background":false}')
    assert is_internal_tool_handoff('{"skipped_mainline":true}')
    assert is_internal_tool_handoff('{"command":"python -c print(1)","timeout":10}')
    assert not is_internal_tool_handoff('{"summary":"分析完成","suggestions":[]}')
    assert not is_internal_tool_handoff('{"query":"可供用户使用的字段","value":42}')
    assert not is_internal_tool_handoff("# 操作教程\n\nbash -lc cat report.txt")
