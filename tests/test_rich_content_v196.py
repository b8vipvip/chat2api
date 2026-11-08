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


def test_v299_real_asset_sha_is_attached_only_to_its_exact_markdown_position():
    sha_a = "a" * 64
    sha_b = "b" * 64
    url_a = "https://chat2api.example/api/media/med_A/tokA"
    url_b = "https://chat2api.example/api/media/med_B/tokB"
    source = (f"## 购买建议\\n\\n![图B]({url_b})\\n\\n"
              f"配套文字以及表格\\n\\n![图A]({url_a})\\n\\n"
              f"再展示同一张图B\\n\\n![复用]({url_b})")
    blocks = manifest(source, [
        {"id": "med_A", "owned_url": url_a, "sha256": sha_a},
        {"id": "med_B", "owned_url": url_b, "sha256": sha_b},
    ], [])["blocks"]
    images = [b for b in blocks if b["type"] == "image"]
    assert [b["archive_sha256"] for b in images] == [sha_b, sha_a, sha_b]
    assert [source[b["start"]:b["end"]] for b in images] == [
        f"![图B]({url_b})", f"![图A]({url_a})", f"![复用]({url_b})",
    ]
    assert all(b["position_proof"] == "canonical-markdown-source-span" for b in images)
    assert "".join(source[b["start"]:b["end"]] for b in blocks) == source


def test_v299_unknown_media_hash_cannot_be_labeled_cryptographically_verified():
    url = "https://chat2api.example/api/media/med_Z/tok"
    block = next(b for b in manifest(
        f"![图]({url})", [{"id": "med_Z", "url": url,
                           "sha256": "not-a-digest"}], []
    )["blocks"] if b["type"] == "image")
    assert "archive_sha256" not in block
    assert "position_proof" not in block
