from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import subprocess

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
from app.api_key_console_v68_patch import ADMIN_ASSET, PATCH_REVISION, install_api_key_console_v68_patch
from app.api_keys import ApiKeyStore


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_multimodal_v68_is_loaded_before_legacy_v4_and_owns_same_runtime_key():
    manifest = read("chrome_extension/manifest.json")
    source = read("chrome_extension/content_multimodal_v68.js")
    assert manifest.index('"content_multimodal_v68.js"') < manifest.index('"content_multimodal_v4.js"')
    assert 'const KEY = "__CHAT2API_MULTIMODAL_V4__"' in source
    assert 'const CONTROLLER = "multimodal-v4-r68"' in source
    assert "const REVISION = 68" in source
    assert 'strategy: "file-input"' in source
    assert 'strategy: "composer-paste"' in source
    assert 'strategy: "composer-drop"' in source
    assert "fresh-after-upload-menu" in source
    assert "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, \"files\")" in source
    assert 'attachment_strategy_fallback_v68: true' in source
    # Production's failure had no chip, preview, input consumption or mutations.
    # A zero-signal direct file-input attempt must therefore fall through to a
    # genuinely distinct composer injection strategy instead of waiting 45s again.
    assert "zeroSignal" in source
    assert "uploadBusy()" in source
    assert "dispatchPaste(file)" in source
    assert "dispatchDrop(file)" in source


def test_routed_dispatch_failure_delegates_lifecycle_to_v30_router_authority():
    source = read("chrome_extension/conversation_dispatch.js")
    assert "async function publishRoutedDispatchFailure" in source
    assert "const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__" in source
    assert 'typeof router?.failRequest === "function"' in source
    assert "await router.failRequest(requestId" in source
    assert "route_retired_by_authority_v30: routeRetired" in source
    assert "routed_dispatch_terminal_v58: true" in source
    assert "const sent = await trySendSocket(event)" in source
    assert "state.requestTabs.delete(requestId)" in source
    # Transport no longer owns route/window mutation.
    assert "route.inflight_request_id = null" not in source
    assert "activeRequests.delete(id)" not in source
    assert "chrome.windows.remove" not in source



def test_responses_terminal_reconciliation_remains_inside_request_v6_authority():
    request = read("chrome_extension/content_request_v6.js")
    network = read("chrome_extension/content_network_stream_recovery_v55.js")
    assert "async function reconcileResponsesTerminal(active, networkText)" in request
    assert "candidateState.isNew && candidateState.latest" in request
    assert "completeResponsesBridgeDocument(candidate)" in request
    assert 'source: "validated-same-turn-dom-v163"' in request
    assert 'type: "chat.completed"' in request
    assert 'type: "chat.completed"' not in network



def build_api_key_app(tmp_path: Path) -> tuple[FastAPI, ApiKeyStore]:
    app = FastAPI()
    store = ApiKeyStore(tmp_path, "test-data-secret")
    app.state.api_keys = store
    app.state.settings = SimpleNamespace(api_key="master-secret")

    class Sessions:
        def authenticate(self, token):
            return token == "session-ok"

    app.state.admin_sessions = Sessions()

    @app.get("/admin")
    async def admin():
        return HTMLResponse("<html><body>console</body></html>")

    install_api_key_console_v68_patch(app)
    return app, store


def test_api_key_settings_endpoint_edits_name_and_scopes(tmp_path: Path):
    app, store = build_api_key_app(tmp_path)
    created, _token = asyncio.run(store.create("Old name"))
    key_id = created["key_id"]
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-ok")
        response = client.patch(
            f"/api/admin/keys/{key_id}/settings",
            json={"name": "  新令牌名称  ", "scopes": ["chat", "files"]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["revision"] == PATCH_REVISION == 68
        assert payload["data"]["name"] == "新令牌名称"
        assert payload["data"]["scopes"] == ["chat", "files"]
        assert store.keys[key_id].scopes == ["chat", "files"]

        empty = client.patch(f"/api/admin/keys/{key_id}/settings", json={"scopes": []})
        assert empty.status_code == 422


def test_api_key_editor_asset_is_injected_and_uses_chinese_permissions(tmp_path: Path):
    app, _store = build_api_key_app(tmp_path)
    with TestClient(app) as client:
        html = client.get("/admin").text
        assert f'<script src="{ADMIN_ASSET}"></script>' in html
        script = client.get(ADMIN_ASSET).text

    for label in ("管理员", "对话", "模型列表", "文件", "图片生成", "音频/语音"):
        assert label in script
    assert 'data-api-key-edit="${action}"' in script
    assert 'iconButton("name", row.key_id, "修改令牌名称")' in script
    assert 'iconButton("scopes", row.key_id, "编辑权限")' in script
    assert "修改令牌名称" in script
    assert "编辑权限" in script
    assert 'usable ? "可用" : row?.quota_exhausted ? "额度耗尽" : "停用"' in script
    assert 'iconButton("quota", row.key_id, "设置 Token 额度")' in script
    assert "至少保留一个权限" in script
    assert "MutationObserver" not in script
    assert "setInterval(" not in script


def test_v68_javascript_syntax():
    for path in (
        "chrome_extension/content_multimodal_v68.js",
        "chrome_extension/conversation_dispatch.js",
        "app/admin_api_key_editor_v68.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_entry_installs_api_key_console_after_worker_presentation():
    entry = read("app/entry.py")
    assert "install_api_key_console_v68_patch(app)" in entry
    assert entry.index("install_worker_presentation_v64_patch(app)") < entry.index("install_api_key_console_v68_patch(app)")



def test_v638_media_terminal_integrity_does_not_create_a_second_terminal_owner():
    source = read("chrome_extension/content_request_v6.js")
    manifest = read("chrome_extension/manifest.json")
    assert 'function mediaTerminalPayloadIntegrity(text)' in source
    assert "mediaTerminalPayloadIntegrity(captured)" in source
    assert source.index("mediaTerminalPayloadIntegrity(captured)") < source.index(
        'responseStarted = await ensureResponseStarted(active, responseStarted, "verified-media-dom-v176")'
    )
    assert 'media_terminal_integrity:"retry"' in source
    assert 'imageCandidate=""' in source
    assert 'await emit({\n              type: "chat.completed"' in source
    assert source.count('response_terminal_source: "verified-request-media-dom-v176"') == 1
    assert manifest.index('"content_native_content_v1.js"') < manifest.index('"content_request_v6.js"')
    assert "native_capture_failure:String(active.nativeCaptureFailure || \"\")" in source


def test_v639_native_sidecar_requires_committed_server_ack_without_terminal_authority():
    background = read("chrome_extension/background.js")
    observer = read("chrome_extension/content_native_content_v1.js")
    backend = read("app/main.py")
    controller = read("chrome_extension/content_request_v6.js")
    assert "chat2api.native.ack" in background
    assert "chat.native.ack" in background
    assert "chat.native.ack" in backend
    assert 'message_type in {"chat.native.chunk", "chat.native.snapshot"}' in backend
    assert 'type:"chat2api.native.ack"' in observer
    assert "reply?.committed === true" in observer
    assert "reply?.committed !== true" in observer
    assert "native_snapshot_server_committed" in backend
    assert "native_transport_ack_error" in controller
    assert 'response_terminal_source: "verified-request-media-dom-v176"' in controller
    # This observer can neither emit a terminal nor manufacture media URLs.
    assert 'type:"chat.completed"' not in observer
    assert '"data:image/' not in observer

def test_v639_never_returns_ordinal_or_dimension_guessed_image_assets():
    backend = read("app/main.py")
    # SHA-verified mapping legitimately uses the same list comprehension;
    # only *unverified* dimensions/ordinals must be excluded.
    assert 'if candidates["status"] == "verified-sha256":' in backend
    mapping = backend.split('if candidates["status"] == "verified-sha256":', 1)[1].split(
        'result = {**snapshot', 1)[0]
    assert 'visible_assets = [assets[index] for index in candidates["indices"]]' in mapping
    assert 'elif ordinal:' not in mapping
    assert 'elif candidates["status"] == "unique-dimensions-unverified":' not in mapping
    assert mapping.count('visible_assets = []') == 2


def test_v640_native_observer_does_not_replace_request_v6_terminal_owner():
    source = read("chrome_extension/content_request_v6.js")
    broker = read("app/broker.py")
    background = read("chrome_extension/background.js")
    assert 'active.nativeCaptureAttempted = true;' in source
    assert '"native-observer-not-loaded"' in source
    assert 'native_observer_loaded:active.nativeObserverLoaded === true' in source
    assert 'native_capture_failure:String(active.nativeCaptureFailure || "")' in source
    assert '"native_capture_attempted"' in broker
    assert '"native_transport_ack_error"' in broker
    assert 'event?.snapshot?.revision : event?.revision' in background
    assert source.count('response_terminal_source: "verified-request-media-dom-v176"') == 1
    # Native observer status is a sidecar diagnostic, never a competing terminal.
    observer = read("chrome_extension/content_native_content_v1.js")
    assert 'type:"chat.completed"' not in observer



def test_v641_native_final_sidecar_has_revision_truth_not_second_terminal_owner():
    controller = read("chrome_extension/content_request_v6.js")
    observer = read("chrome_extension/content_native_content_v1.js")
    backend = read("app/main.py")
    broker = read("app/broker.py")
    assert "native_final_capture_committed:active.nativeFinalCaptureCommitted === true" in controller
    assert "active.nativeFinalCaptureCommitted = false;" in observer
    assert "if (final) active.nativeFinalCaptureCommitted = true;" in observer
    assert 'state.diagnostics["native_snapshot_terminal_complete"] = bool(' in backend
    assert 'native.get("revision") == state.diagnostics.get("native_capture_revision")' in backend
    assert '"native_final_capture_committed"' in broker
    assert '"native_structural_max_children"' in broker
    assert 'type:"chat.completed"' not in observer
    assert controller.count('response_terminal_source: "verified-request-media-dom-v176"') == 1



def test_v642_native_page_locator_is_bounded_metadata_and_not_second_terminal():
    source = read("chrome_extension/content_request_v6.js")
    broker = read("app/broker.py")
    tool = read("scripts/native_page_diagnostic_v1.py")
    controller = read("scripts/linux_worker_device_controller.py")
    assert "native_chatgpt_conversation_url:nativeConversationUrl" in source
    assert 'p?.protocol === "https:"' in source
    assert 'String(p.origin)+path' in source
    assert '"native_chatgpt_conversation_url"' in broker
    assert 'native_page_tool_control.execute(' in controller
    assert 'Page.captureScreenshot' in tool
    assert 'Page.navigate' not in tool
    assert "chat.completed" not in tool
    assert "chat.error" not in tool
    assert source.count('response_terminal_source: "verified-request-media-dom-v176"') == 1
    # The original request-v6 emit() still solely owns business completion.
    assert 'if (event?.type === "chat.completed" && active)' in source


def test_v643_native_history_tool_never_changes_business_terminal_and_has_strict_origin():
    request = read("chrome_extension/content_request_v6.js")
    script = read("scripts/native_page_diagnostic_v1.py")
    server = read("app/native_page_diagnostic_api.py")
    policy = read("app/worker_probe_ui_patch.py")
    history = read("app/request_history_v94_patch.py")
    assert "data-chat2api-native-request-id" in request
    assert "native_chatgpt_conversation_url" in request
    assert "Page.navigate" not in script
    assert "chrome_history_candidates" in script
    assert "reference_matches" in script
    assert "history_candidate_count" in script
    assert "native_page_start" in server
    assert "app.state.telemetry.get(request_id)" in server
    assert "workerProbePolicyDetails" in policy
    assert "workerToolsPolicyRows" in policy
    assert "requestHistoryContextCopy(" in history
    assert "event,requestId,r?.diagnostics?.native_chatgpt_conversation_url" in history
    assert "chat.completed" not in script
    assert request.count('response_terminal_source: "verified-request-media-dom-v176"') == 1



def test_v644_diagnosis_one_input_and_native_history_context_copy_contract():
    ui=read("app/worker_probe_ui_patch.py")
    javascript=read("app/admin_worker_tools_v1.js")
    history=read("app/request_history_v94_patch.py")
    server=read("app/native_page_diagnostic_api.py")
    collector=read("scripts/native_page_diagnostic_v1.py")
    worker=read("chrome_extension/content_request_v6.js")
    assert 'id="nativePageRequestId"' in ui
    assert 'id="nativePageConversationUrl"' not in ui
    assert 'nativePageUrlPattern.test(target)' in javascript
    assert 'body: {worker_id, target, show_tab}' in javascript
    assert 'target_type' in server and 'url-only-unbound' in server
    assert '"req_diag_"+job_id' in server
    assert '"url-only-unbound" if args.request_id.startswith("req_diag_")' in collector
    assert '复制原生 ChatGPT 地址' in history
    assert 'requestHistoryCopyNativeUrl(requestId,knownNativeUrl)' in history
    assert 'native_chatgpt_conversation_url' in history
    assert 'rememberNativeConversationUrl(active);' in worker
    assert worker.count('response_terminal_source: "verified-request-media-dom-v176"')==1


def test_v260_native_scope_binding_keeps_single_terminal_owner_and_rejects_partial_dom():
    source = read("chrome_extension/content_request_v6.js")
    assert "function bindNativeReplyScope(active, phase" in source
    assert 'bindNativeReplyScope(active, "terminal")' in source
    assert 'bindNativeReplyScope(active, "progress")' in source
    assert "active.baselineCapturedAt > active.submitStartedAt" in source
    assert "active.networkObservedAt || 0" in source
    assert "active.baselineFinishActions.has" in source
    assert "if (selectedScopes.size !== 1) return gate(" in source
    assert "roleless?.rolelessRichVerified === true" in source
    assert 'active.options?.mode === "voice"' in source
    assert 'native-reply-scope-unverified' in source
    assert "if (!requestScopeBound && !rolelessBound)" in source
    assert "native_reply_scope_verified" in source
    assert "active.nativeVerifiedReplyScope?.isConnected" in source
    assert 'type: "chat.completed"' in source
    assert "request-v6" in source

