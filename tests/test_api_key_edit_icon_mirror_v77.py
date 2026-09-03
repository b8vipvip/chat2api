from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_key_edit_pencil_icons_are_horizontally_mirrored() -> None:
    source = (ROOT / "app" / "api_key_console_v68_patch.py").read_text(encoding="utf-8")
    assert 'EDIT_ICON_MIRROR_MARKER = "data-chat2api-api-key-edit-icon-mirror-v77"' in source
    assert "button[data-api-key-edit]" in source
    assert "transform: scaleX(-1)" in source
    assert 'text.replace("</head>", EDIT_ICON_MIRROR_STYLE + "</head>")' in source
