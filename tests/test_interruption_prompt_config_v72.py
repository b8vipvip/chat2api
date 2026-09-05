from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.prompt_config import PromptConfigStore, SYSTEM_DEFAULT_PREFIX


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_config_applies_editable_system_prefix_custom_prefix_suffix_and_redaction(tmp_path: Path) -> None:
    store = PromptConfigStore(tmp_path)
    saved = store.save(
        {
            "system_default_prefix": "CUSTOM SYSTEM PREFIX",
            "prefix": "CUSTOM PREFIX",
            "suffix": "CUSTOM SUFFIX",
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
    assert final == "CUSTOM SYSTEM PREFIX\n\nCUSTOM PREFIX\n\nUser says [MASKED]\n\nCUSTOM SUFFIX"
    assert meta["system_default_prefix_applied"] is True
    assert meta["system_default_prefix_chars"] == len("CUSTOM SYSTEM PREFIX")
    assert meta["system_default_prefix_recommended"] is False
    assert meta["redaction_count"] == 1
    assert saved["system_default_prefix"] == "CUSTOM SYSTEM PREFIX"
    assert saved["system_default_prefix_readonly"] is False
    assert saved["recommended"]["system_default_prefix"] == SYSTEM_DEFAULT_PREFIX
    assert saved["audit_final_prompt"] is True
    assert (tmp_path / "prompt_config.json").exists()


def test_existing_prompt_config_without_system_field_migrates_to_recommended_default(tmp_path: Path) -> None:
    (tmp_path / "prompt_config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "prefix": "legacy prefix",
                "suffix": "",
                "redaction_enabled": False,
                "rules": [],
                "audit_final_prompt": True,
                "revision": 7,
            }
        ),
        encoding="utf-8",
    )
    store = PromptConfigStore(tmp_path)
    assert store.config["system_default_prefix"] == SYSTEM_DEFAULT_PREFIX
    assert store.config["prefix"] == "legacy prefix"
    assert store.config["revision"] == 7


def test_system_default_prefix_matches_external_account_execution_rule() -> None:
    assert SYSTEM_DEFAULT_PREFIX.startswith("[chat2api API execution rule]")
    assert "External account-connected apps, plugins, connectors, actions, integrations" in SYSTEM_DEFAULT_PREFIX
    assert "Do not call, open, connect, reconnect, enable, authorize, install, select" in SYSTEM_DEFAULT_PREFIX
    assert "normal ChatGPT model capabilities" in SYSTEM_DEFAULT_PREFIX


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


def test_worker_tool_isolation_respects_server_managed_prompt_and_falls_back_for_old_server() -> None:
    source = (ROOT / "chrome_extension" / "background_tool_isolation_v48.js").read_text(encoding="utf-8")
    assert "hasSystemPolicy" in source
    assert "serverManagesPromptPolicy" in source
    assert "server_prompt_policy_managed" in source
    assert 'tool_policy_source: alreadyPresent ? "server-system-default-prefix" : "worker-compat-fallback"' in source
    assert "prompt: alreadyPresent ? original : `${POLICY}\\n\\n${original}`" in source
    assert "tool_policy_injected: !alreadyPresent" in source
    assert "fallback_injections" in source
    assert "server_owned_prefixes" in source


def test_prompt_config_patch_audits_exact_worker_prompt_and_marks_server_policy_owner() -> None:
    source = (ROOT / "app" / "prompt_config_v72_patch.py").read_text(encoding="utf-8")
    assert 'payload.get("type") == "chat.request"' in source
    assert '"final_prompt": prompt' in source
    assert '"server_prompt_policy_managed": True' in source
    assert '"server_system_default_prefix_recommended": system_default_prefix == SYSTEM_DEFAULT_PREFIX' in source
    assert 'row.pop("final_prompt", None)' in source
    assert '@app.get("/api/admin/prompt-config")' in source
    assert '@app.put("/api/admin/prompt-config")' in source
    assert '@app.post("/api/admin/prompt-config/preview")' in source
    assert "main_module.build_prompt = configured_build_prompt" in source


def test_admin_assets_have_editable_system_prompt_inline_save_defaults_and_copy_modal() -> None:
    legacy = (ROOT / "app" / "admin_prompt_config_v72.js").read_text(encoding="utf-8")
    overlay = (ROOT / "app" / "admin_prompt_config_v75.js").read_text(encoding="utf-8")
    assert "提示词配置" in legacy
    assert "系统默认前置提示词" in legacy
    assert 'id="pcSystemDefaultPrefix"' in legacy
    assert "自定义前置提示词" in legacy
    assert "自定义后置提示词" in legacy
    assert "脱敏配置" in legacy
    assert "最终完整提示词" in legacy
    assert "复制提示词" in legacy
    assert "/api/admin/prompt-config" in legacy
    assert "/api/admin/requests/" in legacy
    assert 'document.getElementById("rqBody")' not in legacy
    assert '$("rqBody")' not in legacy
    assert "window.loadRequests" not in legacy
    assert 'system.removeAttribute("readonly")' in overlay
    assert 'addPromptActionBar("pcSystemDefaultPrefix", "system_default_prefix")' in overlay
    assert 'addPromptActionBar("pcPrefix", "prefix")' in overlay
    assert 'addPromptActionBar("pcSuffix", "suffix")' in overlay
    assert "默认推荐" in overlay
    assert "保存" in overlay
    assert '$("pcSave")?.remove();' in overlay
    assert "saveRedaction" in overlay
    assert "MutationObserver" not in overlay
    assert 'document.getElementById("rqBody")' not in overlay
    assert '$("rqBody")' not in overlay


def test_entry_installs_prompt_config_before_request_history_final_owner() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .prompt_config_v72_patch import install_prompt_config_v72_patch" in source
    assert "from .request_history_v94_patch import install_request_history_v94_patch" in source
    assert source.index("install_prompt_config_v72_patch(app)") < source.index("install_request_history_v94_patch(app)")
    assert source.rstrip().endswith("install_request_history_v94_patch(app)")


def test_new_javascript_syntax() -> None:
    for relative in (
        "chrome_extension/content_interruption_guard_v72.js",
        "chrome_extension/content_request_interruption_bridge_v72.js",
        "chrome_extension/background_tool_isolation_v48.js",
        "app/admin_prompt_config_v72.js",
        "app/admin_prompt_config_v75.js",
    ):
        subprocess.run(["node", "--check", str(ROOT / relative)], check=True, capture_output=True, text=True)
