from pathlib import Path

from app.prompt_config_v72_patch import PROMPT_THEME_MARKER, PROMPT_THEME_V74


ROOT = Path(__file__).resolve().parents[1]


def test_system_default_prompt_theme_matches_dark_console() -> None:
    assert PROMPT_THEME_MARKER == "data-chat2api-prompt-theme-v74"
    assert "#pcSystemDefaultPrefix" in PROMPT_THEME_V74
    assert "background: var(--panel2) !important" in PROMPT_THEME_V74
    assert "color: var(--text) !important" in PROMPT_THEME_V74
    assert "border-color: var(--line) !important" in PROMPT_THEME_V74
    assert "-webkit-text-fill-color: var(--text) !important" in PROMPT_THEME_V74
    assert "opacity: 1 !important" in PROMPT_THEME_V74


def test_prompt_page_buttons_use_green_action_palette() -> None:
    assert "#view-prompt-config button" in PROMPT_THEME_V74
    assert "background: #154634 !important" in PROMPT_THEME_V74
    assert "border: 1px solid #2f7d5b !important" in PROMPT_THEME_V74
    assert "background: #1b5b43 !important" in PROMPT_THEME_V74
    assert "border-color: #39d6a1 !important" in PROMPT_THEME_V74


def test_prompt_patch_injects_theme_before_head_close() -> None:
    source = (ROOT / "app" / "prompt_config_v72_patch.py").read_text(encoding="utf-8")
    assert 'if PROMPT_THEME_MARKER not in admin_module.ADMIN_HTML:' in source
    assert 'replace("</head>", PROMPT_THEME_V74 + "</head>")' in source
    assert "ASSET_V75_PATH" in source
    assert "admin_prompt_config_v75.js" in source


def test_light_fallback_is_not_used_by_theme_override() -> None:
    assert "#f6f7f9" not in PROMPT_THEME_V74
    assert "--surface-2" not in PROMPT_THEME_V74
