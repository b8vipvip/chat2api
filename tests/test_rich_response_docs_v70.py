from pathlib import Path

from app.rich_response_docs_patch import DOC_MARKER, PATCH_ID, RICH_RESPONSE_DOC_HTML


ROOT = Path(__file__).resolve().parents[1]


def test_rich_response_docs_describe_openai_compatible_contract() -> None:
    assert PATCH_ID == "rich-response-docs-v70"
    assert DOC_MARKER in RICH_RESPONSE_DOC_HTML
    assert "choices[0].message.content" in RICH_RESPONSE_DOC_HTML
    assert "Markdown" in RICH_RESPONSE_DOC_HTML
    assert "stream=false" in RICH_RESPONSE_DOC_HTML
    assert "stream=true" in RICH_RESPONSE_DOC_HTML
    assert "delta.content" in RICH_RESPONSE_DOC_HTML
    assert "data:image" in RICH_RESPONSE_DOC_HTML
    assert "不需要 chat2api 专用请求参数" in RICH_RESPONSE_DOC_HTML


def test_entry_installs_rich_response_docs_before_final_prompt_boundary() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .rich_response_docs_patch import install_rich_response_docs_patch" in entry
    assert "from .prompt_config_v72_patch import install_prompt_config_v72_patch" in entry
    assert entry.index("install_rich_response_docs_patch(app)") < entry.index("install_prompt_config_v72_patch(app)")
    assert entry.rstrip().endswith("install_prompt_config_v72_patch(app)")
