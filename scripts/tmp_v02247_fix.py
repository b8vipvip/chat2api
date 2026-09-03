from pathlib import Path


def require_replace(text: str, old: str, new: str, name: str) -> str:
    if old not in text:
        raise SystemExit(f"{name}: missing expected target: {old[:100]!r}")
    return text.replace(old, new, 1)


# 1) Server-side active-request disable lease.
path = Path("app/worker_disable_authority_patch.py")
text = path.read_text(encoding="utf-8")
text = require_replace(text, 'PATCH_VERSION = "0.22.42"', 'PATCH_VERSION = "0.22.47"', str(path))
anchor = '    async def collapse_managed_windows(client_id: str) -> dict[str, Any]:\n'
insert = '''    def active_request_ids(client_id: str) -> list[str]:
        broker = getattr(app.state, "broker", None)
        if broker is None:
            return []
        active_by_client = getattr(broker, "client_active_requests", None)
        if isinstance(active_by_client, dict):
            active = active_by_client.get(client_id)
            if isinstance(active, dict):
                return [str(request_id) for request_id in active.keys()]
            if isinstance(active, (set, list, tuple)):
                return [str(request_id) for request_id in active]
        request_map = getattr(broker, "client_requests", None)
        request_id = request_map.get(client_id) if isinstance(request_map, dict) else None
        if request_id and request_id in getattr(broker, "requests", {}):
            return [str(request_id)]
        return []

    def require_disable_lease(client_id: str) -> None:
        active = active_request_ids(client_id)
        if not active:
            return
        logger.warning(
            "Worker disable blocked by active-request lease client=%s active_requests=%s",
            client_id,
            active,
        )
        raise HTTPException(
            409,
            {
                "message": "Worker still has active requests; disable is blocked until every request reaches a terminal event",
                "code": "worker_active_request_lease",
                "active_request_count": len(active),
                "active_request_ids": active[:20],
                "retryable": True,
            },
        )

    async def collapse_managed_windows(client_id: str) -> dict[str, Any]:
        require_disable_lease(client_id)
'''
text = require_replace(text, anchor, insert, str(path))
old = '''    async def disable_client(client_id: str) -> dict[str, Any]:
        client_id = str(client_id or "")
        if client_id not in registry.clients:
            raise HTTPException(404, "Unknown extension ID")
        control = await collapse_managed_windows(client_id)
'''
new = '''    async def disable_client(client_id: str) -> dict[str, Any]:
        client_id = str(client_id or "")
        if client_id not in registry.clients:
            raise HTTPException(404, "Unknown extension ID")
        require_disable_lease(client_id)
        control = await collapse_managed_windows(client_id)
'''
text = require_replace(text, old, new, str(path))
text = require_replace(
    text,
    '        "persist_worker_switch": persist_worker_switch,\n',
    '        "persist_worker_switch": persist_worker_switch,\n        "active_request_ids": active_request_ids,\n        "require_disable_lease": require_disable_lease,\n',
    str(path),
)
path.write_text(text, encoding="utf-8")


# 2) Extension-side defence in depth: reject worker.disable while routed requests exist.
path = Path("chrome_extension/background_worker_master_switch_v61.js")
text = path.read_text(encoding="utf-8")
text = require_replace(
    text,
    '  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";\n  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";\n',
    '  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";\n  const DISPATCH_KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";\n  const WARM_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";\n',
    str(path),
)
anchor = '  async function handleDisable(message) {\n    try {\n'
insert = '''  function activeRequestLease() {
    const dispatch = globalThis[DISPATCH_KEY];
    const requestTabs = dispatch?.requestTabs;
    if (!(requestTabs instanceof Map) || requestTabs.size <= 0) return { count: 0, ids: [] };
    return { count: requestTabs.size, ids: [...requestTabs.keys()].map(String).slice(0, 20) };
  }

  async function handleDisable(message) {
    try {
      const lease = activeRequestLease();
      if (lease.count > 0) {
        return emitResult(message, false, {
          blocked: true,
          retryable: true,
          active_request_count: lease.count,
          active_request_ids: lease.ids,
          lease_revision: 79,
        }, "Worker has active requests; disable is blocked until terminal completion");
      }
'''
text = require_replace(text, anchor, insert, str(path))
text = require_replace(
    text,
    '        worker_master_switch_revision: 62,\n',
    '        worker_master_switch_revision: 62,\n        active_request_disable_lease_revision: 79,\n',
    str(path),
)
path.write_text(text, encoding="utf-8")


# 3) Submission liveness: composer clear alone cannot confirm submission.
path = Path("chrome_extension/content_request_v6.js")
text = path.read_text(encoding="utf-8")
old = '''    const user = currentUserTurn(active);
    if (user) return { reason: "current-user-turn-visible", composerCleared: composerText(findComposer()).length === 0, generating: false, current };
    if (!composerText(findComposer())) return { reason: "composer-cleared", composerCleared: true, generating: false, current };
    return null;
'''
new = '''    const user = currentUserTurn(active);
    if (user) return { reason: "current-user-turn-visible", composerCleared: composerText(findComposer()).length === 0, generating: false, current };
    // A cleared composer is only weak evidence: ChatGPT may consume the draft
    // while an attachment is still settling or while submission is rejected.
    // Never promote that UI mutation to a successful API submission by itself.
    return null;
'''
text = require_replace(text, old, new, str(path))
old = '''    const confirmed = await waitAfterSend(active, "late", 20000);
    if (confirmed) return confirmed;
    throw new Error("ChatGPT prompt left the composer after send, but submission could not be confirmed; duplicate send was suppressed");
'''
new = '''    const lateBudget = active.attachmentCount > 0 ? 45000 : 20000;
    const confirmed = await waitAfterSend(active, "late", lateBudget);
    if (confirmed) return confirmed;
    throw new Error("ChatGPT cleared the composer but did not expose an accepted user turn, generation state, or response; duplicate send was suppressed");
'''
text = require_replace(text, old, new, str(path))
text = require_replace(
    text,
    '      options: message.options || {},\n      cancelled: false,\n',
    '      options: message.options || {},\n      attachmentCount: Array.isArray(message.attachments) ? message.attachments.length : 0,\n      cancelled: false,\n',
    str(path),
)
text = require_replace(
    text,
    '          historical_hydration_ignored: true,\n',
    '          historical_hydration_ignored: true,\n          submission_liveness_revision: 79,\n          submission_attachment_count: active.attachmentCount,\n',
    str(path),
)
path.write_text(text, encoding="utf-8")


# 4) Multimodal settlement: preview/chip is not upload completion.
path = Path("chrome_extension/content_multimodal_v78.js")
text = path.read_text(encoding="utf-8")
anchor = '  async function waitForUpload(file, before, tracker, timeoutMs, strongSignal = false) {\n'
helper = '''  async function waitForUploadSettled(file, before, initialReason, timeoutMs = 30000, stableMs = 1600) {
    const deadline = Date.now() + timeoutMs;
    let stableSince = 0;
    let lastSeen = before;
    while (Date.now() < deadline) {
      const error = uploadErrorFor(file.name);
      if (error) throw new Error(`${file.name}: ${error}`);
      const seen = fileVisible(file, before);
      lastSeen = seen.now;
      if (!seen.ok || uploadBusy()) {
        stableSince = 0;
        await delay(150);
        continue;
      }
      if (!stableSince) stableSince = Date.now();
      if (Date.now() - stableSince >= stableMs) {
        return {
          ok: true,
          reason: initialReason || seen.reason,
          lastSeen,
          upload_settled: true,
          upload_settle_ms: Date.now() - stableSince,
          upload_settle_revision: 79,
        };
      }
      await delay(150);
    }
    throw new Error(`${file.name}: attachment appeared in ChatGPT but upload/processing did not settle before timeout`);
  }

  async function waitForUpload(file, before, tracker, timeoutMs, strongSignal = false) {
'''
text = require_replace(text, anchor, helper, str(path))
old = '''      if (seen.ok) {
        await delay(550);
        const late = uploadErrorFor(file.name);
        if (late) throw new Error(`${file.name}: ${late}`);
        return { ok: true, reason: seen.reason, lastSeen, duplicate, duplicateClosed, signal: true };
      }
'''
new = '''      if (seen.ok) {
        const settled = await waitForUploadSettled(file, before, seen.reason);
        return { ...settled, duplicate, duplicateClosed, signal: true };
      }
'''
text = require_replace(text, old, new, str(path))
path.write_text(text, encoding="utf-8")


# 5) Formal patch release boundary.
path = Path("app/runtime_contract.py")
text = path.read_text(encoding="utf-8")
text = require_replace(text, 'SERVER_RUNTIME_VERSION = "0.22.46"', 'SERVER_RUNTIME_VERSION = "0.22.47"', str(path))
text = require_replace(text, 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.18"', 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.19"', str(path))
text = require_replace(text, 'capacity-native-v37-bundle-0818-', 'capacity-native-v37-bundle-0819-', str(path))
text = require_replace(text, '-multimodal-main-world-v78-release-v02246"', '-multimodal-main-world-v78-active-request-disable-lease-v79-multimodal-upload-settle-v79-submission-liveness-v79-release-v02247"', str(path))
text = require_replace(text, '-multimodal-main-world-v78-release-v0818"', '-multimodal-main-world-v78-active-request-disable-lease-v79-multimodal-upload-settle-v79-submission-liveness-v79-release-v0819"', str(path))
feature_anchor = '            "multimodal_main_world_v78": True,\n'
text = require_replace(text, feature_anchor, feature_anchor + '            "active_request_disable_lease_v79": True,\n            "multimodal_upload_settle_v79": True,\n            "submission_liveness_v79": True,\n', str(path))
path.write_text(text, encoding="utf-8")

replacements = {
    "chrome_extension/manifest.json": [('"version": "0.8.18"', '"version": "0.8.19"')],
    "chrome_extension/content_bundle_marker_v48.js": [('bundle: "0.8.18"', 'bundle: "0.8.19"')],
    "chrome_extension/content_bundle_marker_v71.js": [('bundle: "0.8.18"', 'bundle: "0.8.19"')],
    "chrome_extension/background_runtime_preflight_v48.js": [('REQUIRED_BUNDLE = "0.8.18"', 'REQUIRED_BUNDLE = "0.8.19"')],
    "chrome_extension/content_runtime_contract_v48.js": [('REQUIRED_BUNDLE = "0.8.18"', 'REQUIRED_BUNDLE = "0.8.19"')],
    "chrome_extension/content_runtime_contract_v71.js": [('REQUIRED_BUNDLE = "0.8.18"', 'REQUIRED_BUNDLE = "0.8.19"')],
}
for filename, pairs in replacements.items():
    p = Path(filename)
    content = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in content:
            raise SystemExit(f"{filename}: missing {old!r}")
        content = content.replace(old, new)
    p.write_text(content, encoding="utf-8")

for p in Path("tests").glob("*"):
    if not p.is_file() or p.suffix not in {".py", ".mjs", ".js"}:
        continue
    content = p.read_text(encoding="utf-8")
    content = content.replace('SERVER_RUNTIME_VERSION = "0.22.46"', 'SERVER_RUNTIME_VERSION = "0.22.47"')
    content = content.replace('SERVER_RUNTIME_VERSION == "0.22.46"', 'SERVER_RUNTIME_VERSION == "0.22.47"')
    content = content.replace('manifest["version"] == "0.8.18"', 'manifest["version"] == "0.8.19"')
    content = content.replace('CHROME_BRIDGE_BUNDLE_VERSION == "0.8.18"', 'CHROME_BRIDGE_BUNDLE_VERSION == "0.8.19"')
    content = content.replace('CHROME_BRIDGE_BUNDLE_VERSION = "0.8.18"', 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.19"')
    content = content.replace('REQUIRED_BUNDLE = "0.8.18"', 'REQUIRED_BUNDLE = "0.8.19"')
    content = content.replace('bundle: "0.8.18"', 'bundle: "0.8.19"')
    content = content.replace('"version": "0.8.18"', '"version": "0.8.19"')
    p.write_text(content, encoding="utf-8")

Path("tests/test_runtime_lifecycle_multimodal_v79.py").write_text('''from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_disable_is_blocked_while_broker_owns_active_requests() -> None:
    server = read("app/worker_disable_authority_patch.py")
    extension = read("chrome_extension/background_worker_master_switch_v61.js")
    assert "def active_request_ids(client_id: str)" in server
    assert "require_disable_lease(client_id)" in server
    assert "worker_active_request_lease" in server
    assert "activeRequestLease()" in extension
    assert "active_request_disable_lease_revision: 79" in extension
    assert "Worker has active requests; disable is blocked" in extension


def test_composer_clear_is_not_submission_confirmation() -> None:
    request = read("chrome_extension/content_request_v6.js")
    assert 'reason: "composer-cleared"' not in request
    assert "active.attachmentCount > 0 ? 45000 : 20000" in request
    assert "submission_liveness_revision: 79" in request
    assert "did not expose an accepted user turn, generation state, or response" in request


def test_multimodal_waits_for_upload_processing_to_settle() -> None:
    multimodal = read("chrome_extension/content_multimodal_v78.js")
    assert "waitForUploadSettled" in multimodal
    assert "upload_settle_revision: 79" in multimodal
    assert "upload/processing did not settle before timeout" in multimodal
    assert "await delay(550)" not in multimodal


def test_v02247_release_contract() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = read("chrome_extension/manifest.json")
    assert 'SERVER_RUNTIME_VERSION = "0.22.47"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.19"' in runtime
    assert '"version": "0.8.19"' in manifest
    assert '"active_request_disable_lease_v79": True' in runtime
    assert '"multimodal_upload_settle_v79": True' in runtime
    assert '"submission_liveness_v79": True' in runtime
''', encoding="utf-8")

Path("docs/releases/v0.22.47.md").write_text('''# v0.22.47 / Worker Bundle 0.8.19

本版本修复 0.22.46 / 0.8.18 实机日志暴露的请求生命周期问题。

- Worker 管理禁用现在持有 active-request lease：只要 Broker 仍有活动请求，就禁止关闭 Worker WebSocket 或折叠其浏览器窗口，并返回可重试的 409；扩展端也增加第二层活动请求保护。
- 多模态附件不再以“出现附件 chip/预览”直接认定上传完成，而是等待 ChatGPT 上传/处理状态消失并稳定后才允许提交。
- request-v6 不再把“composer 被清空”当成提交成功；必须观察到当前用户消息、生成状态或新助手消息。附件请求使用更长的提交存活确认预算。
- 生命周期规则同样保护视觉理解、文件理解、长文本、图片生成和语音链路：活动请求必须直到 completed/error/cancelled 终态才允许执行 Worker disable。
''', encoding="utf-8")
