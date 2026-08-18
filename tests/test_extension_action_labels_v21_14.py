import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _version(source: str, name: str) -> tuple[int, int, int]:
    match = re.search(rf'{name} = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match, f"missing {name}"
    return tuple(map(int, match.groups()))


def test_extension_action_buttons_use_short_labels_without_changing_handlers():
    source = (ROOT / "app" / "admin_v20.js").read_text(encoding="utf-8")

    assert "disconnectExtensionV18" in source
    assert "enableExtensionV18" in source
    assert "deleteExtensionHistoryV18" in source

    assert ">断开</button>" in source
    assert ">连接</button>" in source
    assert ">删除</button>" in source

    assert ">断开连接</button>" not in source
    assert ">允许连接</button>" not in source
    assert ">删除记录</button>" not in source


def test_action_label_feature_runtime_floor_is_preserved():
    source = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert _version(source, "SERVER_RUNTIME_VERSION") >= (0, 21, 14)
