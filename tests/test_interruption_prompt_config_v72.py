from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.prompt_config import PromptConfigStore


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_config_applies_prefix_suffix_and_redaction(tmp_path: Path) -> None:
    store = PromptConfigStore(tmp_path)
    saved = store.save(
        {
            "prefix": "SYSTEM PREFIX",
            "suffix": "SYSTEM SUFFIX",
            "redaction_enabled": True,
            "audit_final_prompt": True,
            "rules": [
                {
                    "name": "secret",
                    "enabled": True,
                    "pattern": r"secret-[0-9]+",
                    "replacement": "[MASKED]",
                    "flags": "i",
                }
            ],
        }
    )
    final, meta = store.apply("User says SECRET-123")
    assert final == "SYSTEM PREFIX\n\nUser says [MASKED]\n\nSYSTEM SUFFIX"
    assert meta["redaction_count"] == 1
    assert saved["audit_final_prompt"] is True
    assert (tmp_path / "prompt_config.json").exists()


def test_prompt_config_rejects_invalid_regex(tmp_path: Path) -> None:
    store = PromptConfigStore(tmp_path)
    try:
        store.save(
            {
                "prefix": "",
                "suffix": "",
                "redaction_enabled": True,
                "rules": [{"name": "bad", "enabled": True, "pattern": "(", "replacement": "x", "flags": ""}],
            }
        )
    except ValueError as exc:
        assert "regex is invalid" in str(exc)
    else:
        raise AssertionError("invalid regex must be rejected")


def test_manifest_loads_interruption_guard_after_v6_before_lifecycle() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    v6 = scripts.index("content_request_v6.js")
    guard = scripts.index("content_interruption_guard_v72.js")
    bridge = scripts.index("content_request_interruption_bridge_v72.js")
    lifecycle = scripts.index("content_request_lifecycle_v50.js")
    assert v6 < guard < bridge < lifecycle


def test_interruption_guard_is_safe_negative_and_first_preference_only() -> None:
    source = (ROOT / "chrome_extension" / "content_interruption_guard_v72.js").read_text(encoding="utf-8")
    assert "safe-negative-dismiss" in source
    assert "select-first-response" in source
    assert "response-preference" in source
    assert "connector-or-app-card" in source
    assert "captcha" in source.lower()
    assert "verify you are human" in source.lower()
    assert "authorize" in source.lower()
    assert "clickSafe(dismiss.button)" in source
    assert "clickSafe(preference.choice)" in source


def test_request_bridge_delays_request_until_guard_resolution() -> None:
    source = (ROOT / "chrome_extension" / "content_request_interruption_bridge_v72.js").read_text(encoding="utf-8")
    assert 'message?.type === "chat2api.request"' in source
    assert 'resolveBlockingInterruption({ force: true, phase: "before-request" })' in source
    assert ".finally(() => baseListener(message, sender, () => {}))" in source
    assert "v5.listener = listener" in source


def test_prompt_config_patch_audits_exact_worker_prompt_and_strips_list_payload() -> None:
    source = (ROOT / "app" / "prompt_config_v72_patch.py").read_text(encoding="utf-8")
    assert 'payload.get("type") == "chat.request"' in source
    assert '"final_prompt": prompt' in source
    assert 'row.pop("final_prompt", None)' in source
    assert '@app.get("/api/admin/prompt-config")' in source
    assert '@app.put("/api/admin/prompt-config")' in source
    assert '@app.post("/api/admin/prompt-config/preview")' in source
    assert "main_module.build_prompt = configured_build_prompt" in source


def test_admin_asset_has_prompt_nav_request_column_and_copy_modal() -> None:
    source = (ROOT / "app" / "admin_prompt_config_v72.js").read_text(encoding="utf-8")
    assert "提示词配置" in source
    assert "默认提示词前缀" in source
    assert "默认提示词后缀" in source
    assert "脱敏配置" in source
    assert "查看提示词" in source
    assert "最终完整提示词" in source
    assert "复制提示词" in source
    assert "/api/admin/prompt-config" in source
    assert "/api/admin/requests/" in source


def test_entry_installs_prompt_config_as_final_boundary() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .prompt_config_v72_patch import install_prompt_config_v72_patch" in source
    assert source.rstrip().endswith("install_prompt_config_v72_patch(app)")


def test_new_javascript_syntax() -> None:
    for relative in (
        "chrome_extension/content_interruption_guard_v72.js",
        "chrome_extension/content_request_interruption_bridge_v72.js",
        "app/admin_prompt_config_v72.js",
    ):
        subprocess.run(["node", "--check", str(ROOT / relative)], check=True, capture_output=True, text=True)
