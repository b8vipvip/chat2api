"""Rich block manifest preserves exact Markdown and typed archived output order."""
from app.rich_content_v196 import manifest


def test_mixed_image_file_text_blocks_cover_entire_response_in_order():
    img = "https://chat2api.example/api/media/med_xyz123/tok123"
    file = "https://chat2api.example/api/generated-files/gen_xyz123/tok456"
    response = (
        "# 视频方案\n\n文本 **加粗**\n\n![画面](%s)\n\n"
        "```python\nprint(1)\n```\n\n[📎 视频脚本.py](%s)\n\n结尾"
    ) % (img, file)
    rows = manifest(response, [{"id": "med_xyz123", "url": img}],
                    [{"id": "gen_xyz123", "url": file}])
    kinds = [block["type"] for block in rows["blocks"]]
    assert kinds == ["markdown", "image", "markdown", "file", "markdown"]
    assert rows["schema_version"] == 1 and rows["format"] == "markdown"
    assert "".join(response[b["start"]:b["end"]] for b in rows["blocks"]) == response
    assert [b["asset_id"] for b in rows["blocks"] if b["type"] != "markdown"] == [
        "med_xyz123", "gen_xyz123",
    ]


def test_unknown_links_and_literal_code_are_not_promoted_to_archived_assets():
    text = "See [external](https://elsewhere.example/a.docx)\n\n```mermaid\ngraph LR;\n```"
    rows = manifest(text, [], [])
    assert rows["blocks"] == [{"type": "markdown", "start": 0, "end": len(text)}]


def test_same_generated_asset_more_than_once_keeps_document_order():
    url = "https://chat2api.example/api/media/med_A/tok"
    text = f"![first]({url}) x ![again]({url})"
    rows = manifest(text, [{"id": "med_A", "url": url}], [])
    assert sum(block["type"] == "image" for block in rows["blocks"]) == 2
    assert "".join(text[b["start"]:b["end"]] for b in rows["blocks"]) == text
