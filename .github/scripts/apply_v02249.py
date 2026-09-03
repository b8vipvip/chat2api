from pathlib import Path
import json


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, got {count}: {old[:140]!r}")
    write(path, text.replace(old, new, 1))


# Linux: session files are already cleared. The CDP helper must never race the
# extension reserve/request supervisor and close newly-created live windows.
replace_once(
    "scripts/linux_worker_chrome_launcher.sh",
    '''# Belt-and-suspenders cleanup: if Chrome restores page targets from another
# profile journal format, prune duplicate ChatGPT targets through loopback CDP
# during the first seconds of this Chrome process. This is intentionally
# one-shot; steady-state capacity remains owned by the Extension supervisor.
if [[ -f "$TAB_INIT_HELPER" ]]; then
  python3 "$TAB_INIT_HELPER" --debug-url http://127.0.0.1:9222 --keep 1 --wait 45 &
fi
''',
    '''# Session metadata above is the normal stale-window cleanup boundary. Never
# run a host-side CDP window pruner while the MV3 supervisor is creating
# reserve/routed windows: that helper cannot distinguish a restored tab from a
# live API request and used to close legitimate task windows.
# Keep the legacy helper only as an explicit one-shot recovery opt-in.
if [[ "${CHAT2API_TAB_INIT_PRUNE:-0}" == "1" && -f "$TAB_INIT_HELPER" ]]; then
  log "legacy CDP tab pruning explicitly enabled for this start"
  python3 "$TAB_INIT_HELPER" --debug-url http://127.0.0.1:9222 --keep 1 --wait 8 &
fi
''',
)

# Conversation affinity: two minutes. Reserve target counts *spares*, not all
# managed windows, so one routed request plus reserve=3 yields 4 live windows.
replace_once(
    "chrome_extension/background_reserve_pool_v29.js",
    "const ROUTE_IDLE_CLOSE_MS = 10 * 60 * 1000;",
    "const ROUTE_IDLE_CLOSE_MS = 2 * 60 * 1000;",
)
replace_once(
    "chrome_extension/background_reserve_pool_v29.js",
    '''      if (snapshot.total > state.target && state.reserveSlots.size) {
        await trimOwnReserve(Math.min(snapshot.total - state.target, state.reserveSlots.size));
        snapshot = await managedSnapshot();
      }

      if (snapshot.total < state.target && await bulkPrewarmEligible()) {
        const warmOpening = Number(globalThis[WARM_POOL_KEY]?.openingSlots?.size || 0);
        const missing = Math.max(0, state.target - snapshot.total - warmOpening);
        const batch = Math.min(CREATE_BATCH, missing);
        if (batch > 0) {
          await Promise.all(Array.from({ length: batch }, () => createReserveWindow().catch(() => null)));
          snapshot = await managedSnapshot();
          if (snapshot.total < state.target) scheduleReconcile(250);
        }
      }
''',
    '''      // reserve_window_target is a spare target, not a total-window cap.
      // Routed conversation windows remain alive for affinity and must not
      // consume a reserve slot. target=3 + routed=1 therefore means total=4.
      let spareTotal = Math.max(0, snapshot.total - snapshot.routed);
      if (spareTotal > state.target && state.reserveSlots.size) {
        await trimOwnReserve(Math.min(spareTotal - state.target, state.reserveSlots.size));
        snapshot = await managedSnapshot();
        spareTotal = Math.max(0, snapshot.total - snapshot.routed);
      }

      if (spareTotal < state.target && await bulkPrewarmEligible()) {
        const warmOpening = Number(globalThis[WARM_POOL_KEY]?.openingSlots?.size || 0);
        const missing = Math.max(0, state.target - spareTotal - warmOpening);
        const batch = Math.min(CREATE_BATCH, missing);
        if (batch > 0) {
          await Promise.all(Array.from({ length: batch }, () => createReserveWindow().catch(() => null)));
          snapshot = await managedSnapshot();
          spareTotal = Math.max(0, snapshot.total - snapshot.routed);
          if (spareTotal < state.target) scheduleReconcile(250);
        }
      }
''',
)
replace_once(
    "chrome_extension/conversation_routing.js",
    "const IDLE_CLOSE_MS = 300000;",
    "const IDLE_CLOSE_MS = 2 * 60 * 1000;",
)

# Shared response capture: generation controls can disappear before Linux/Xvfb
# finishes DOM/Markdown/citation hydration. Require a second continuously-stable
# rich-node phase before emitting terminal completion.
replace_once(
    "chrome_extension/content_request_v6.js",
    '''      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1500) {
        await delay(300);
        const finalState = currentAssistantState(active);
        if (!isGenerating() && finalState.isNew && finalState.latest) {
          const final = await finalNodeText(finalState.latest);
          const finalText = final.text || finalState.text || lastText;
          lastText = await updateCapturedText(active, finalText, lastText);
          await emit({
            type: "chat.completed",
            request_id: active.requestId,
            text: finalText,
            diagnostics: {
              response_epoch_revision: 69,
              response_epoch_candidate_reason: finalState.reason,
              response_format: "markdown",
              response_image_inlined_count: final.image_inlined_count || 0,
              response_image_inlined_bytes: final.image_inlined_bytes || 0,
            },
          });
          active.completed = true;
          return;
        }
      }
''',
    '''      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1000) {
        let settleStableSince = Date.now();
        let settledText = lastText;
        let settledState = current;
        let settledFinal = { text: lastText, image_inlined_count: 0, image_inlined_bytes: 0 };
        while (!active.cancelled && Date.now() - settleStableSince < 3000) {
          if (isGenerating()) {
            settleStableSince = 0;
            break;
          }
          const candidateState = currentAssistantState(active);
          if (!candidateState.isNew || !candidateState.latest) {
            settleStableSince = 0;
            break;
          }
          const candidateFinal = await finalNodeText(candidateState.latest);
          const candidateText = candidateFinal.text || candidateState.text || settledText;
          if (candidateText && (candidateText !== settledText || candidateState.identity !== settledState.identity)) {
            settledText = candidateText;
            settledState = candidateState;
            settledFinal = candidateFinal;
            lastText = await updateCapturedText(active, candidateText, lastText);
            lastIdentity = candidateState.identity || lastIdentity;
            settleStableSince = Date.now();
          } else {
            settledState = candidateState;
            settledFinal = candidateFinal;
          }
          await delay(180);
        }
        if (settleStableSince && !isGenerating() && Date.now() - settleStableSince >= 3000) {
          const finalText = settledText || lastText;
          await emit({
            type: "chat.completed",
            request_id: active.requestId,
            text: finalText,
            diagnostics: {
              response_epoch_revision: 69,
              response_terminal_settle_revision: 81,
              response_terminal_stable_ms: Date.now() - settleStableSince,
              response_epoch_candidate_reason: settledState.reason,
              response_format: "markdown",
              response_image_inlined_count: settledFinal.image_inlined_count || 0,
              response_image_inlined_bytes: settledFinal.image_inlined_bytes || 0,
            },
          });
          active.completed = true;
          return;
        }
        stableSince = Date.now();
      }
''',
)

# Multimodal: slower Linux uploads need a longer quiet window. Preserve upload
# settlement fields in diagnostics instead of discarding them in finalize().
replace_once(
    "chrome_extension/content_multimodal_v78.js",
    "async function waitForUploadSettled(file, before, initialReason, timeoutMs = 30000, stableMs = 1600)",
    "async function waitForUploadSettled(file, before, initialReason, timeoutMs = 60000, stableMs = 3000)",
)
replace_once(
    "chrome_extension/content_multimodal_v78.js",
    '''  function finalize(file, before, after, tracker, attempts, reason, duplicate, duplicateClosed) {
    const last = attempts[attempts.length - 1] || {};
    const result = {
      file,
''',
    '''  function finalize(file, before, after, tracker, attempts, reason, duplicate, duplicateClosed, settled = {}) {
    const last = attempts[attempts.length - 1] || {};
    const result = {
      file,
      upload_settled: settled.upload_settled === true,
      upload_settle_ms: Number(settled.upload_settle_ms || 0),
      upload_settle_revision: Number(settled.upload_settle_revision || 0),
''',
)
multimodal = read("chrome_extension/content_multimodal_v78.js")
old_finalize = "return finalize(file, before, after, tracker, attempts, result.reason, result.duplicate, result.duplicateClosed);"
if old_finalize not in multimodal:
    raise SystemExit("content_multimodal_v78.js: successful finalize anchor missing")
write(
    "chrome_extension/content_multimodal_v78.js",
    multimodal.replace(
        old_finalize,
        "return finalize(file, before, after, tracker, attempts, result.reason, result.duplicate, result.duplicateClosed, result);",
    ),
)

# Worker occupancy: denominator is current managed browser windows when available;
# concurrency is still independently capped/configured at its original value.
presentation = read("app/admin_worker_presentation_v66.js")
old_occupancy = '''    const used = Number(capacity.used_units ?? row.active_api_calls ?? 0);
    const limit = Number(capacity.limit_units ?? row.max_concurrency ?? 0);
    const active = Array.isArray(row.active_request_details) ? row.active_request_details.length : used;
    if (!(limit > 0)) return { text: `${Math.max(0, active)}`, title: `活跃请求 ${Math.max(0, active)}` };
    return { text: `${Math.max(0, used)}/${limit}`, title: `活跃请求 ${Math.max(0, active)}；并发占用 ${Math.max(0, used)}/${limit}` };
'''
new_occupancy = '''    const used = Number(capacity.used_units ?? row.active_api_calls ?? 0);
    const limit = Number(capacity.limit_units ?? row.max_concurrency ?? 0);
    const physical = Number(row.metadata?.reserve_window_total ?? 0);
    const denominator = physical > 0 ? physical : limit;
    const active = Array.isArray(row.active_request_details) ? row.active_request_details.length : used;
    if (!(denominator > 0)) return { text: `${Math.max(0, active)}`, title: `活跃请求 ${Math.max(0, active)}` };
    return {
      text: `${Math.max(0, used)}/${denominator}`,
      title: `活跃请求 ${Math.max(0, active)}；当前受管窗口 ${Math.max(0, physical || denominator)}；并发上限 ${Math.max(0, limit)}`,
    };
'''
if presentation.count(old_occupancy) != 1:
    raise SystemExit("admin_worker_presentation_v66.js: occupancy anchor missing")
write("app/admin_worker_presentation_v66.js", presentation.replace(old_occupancy, new_occupancy, 1))

# Playground timestamps/copy actions.
replace_once(
    "app/admin_playground_chat_v69.js",
    ".pgChatMeta{font-size:11px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}.pgChatAttachment",
    ".pgChatMeta{font-size:11px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}.pgChatActions{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:8px}.pgChatCopy{border:0;background:transparent;color:var(--muted);padding:2px 5px;border-radius:6px;cursor:pointer;line-height:1}.pgChatCopy:hover{background:#17263d;color:var(--text)}.pgChatAttachment",
)
replace_once(
    "app/admin_playground_chat_v69.js",
    "  function renderMessages() {\n",
    '''  function formatMessageTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date);
  }
  async function copyMessage(index, button) {
    const item = messages[index];
    if (!item) return;
    const value = String(item.content || "");
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const area = document.createElement("textarea");
        area.value = value; area.style.position = "fixed"; area.style.opacity = "0";
        document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove();
      }
      const before = button.textContent;
      button.textContent = "✓"; button.title = "已复制";
      setTimeout(() => { button.textContent = before; button.title = "复制消息"; }, 900);
    } catch (_) { button.title = "复制失败"; }
  }
  function renderMessages() {
''',
)
replace_once(
    "app/admin_playground_chat_v69.js",
    '''      return `<div class="pgChatRow ${item.role}" data-chat-index="${index}"><div class="pgChatRole">${item.role === "user" ? "你" : "模型"}</div><div class="pgChatBubble${errorClass}${richClass}">${content}</div>${(attachments || item.meta) ? `<div class="pgChatMeta">${attachments}${item.meta ? `<span>${escHtml(item.meta.request_id || "")}</span><span>${escHtml(item.meta.model || "")}</span><span>${item.meta.total_ms != null ? `${Math.round(item.meta.total_ms)} ms` : ""}</span>` : ""}</div>` : ""}</div>`;
''',
    '''      const messageTime = formatMessageTime(item.created_at);
      return `<div class="pgChatRow ${item.role}" data-chat-index="${index}"><div class="pgChatRole">${item.role === "user" ? "你" : "模型"}</div><div class="pgChatBubble${errorClass}${richClass}">${content}</div>${(attachments || item.meta) ? `<div class="pgChatMeta">${attachments}${item.meta ? `<span>${escHtml(item.meta.request_id || "")}</span><span>${escHtml(item.meta.model || "")}</span><span>${item.meta.total_ms != null ? `${Math.round(item.meta.total_ms)} ms` : ""}</span>` : ""}</div>` : ""}<div class="pgChatActions"><span>${escHtml(messageTime)}</span><button type="button" class="pgChatCopy" data-copy-index="${index}" title="复制消息" aria-label="复制消息">⧉</button></div></div>`;
''',
)
replace_once(
    "app/admin_playground_chat_v69.js",
    'messages.push({role:"user",content:text,attachment_names:userFiles.map(file=>file.name),include_in_context:true});',
    'messages.push({role:"user",content:text,attachment_names:userFiles.map(file=>file.name),include_in_context:true,created_at:new Date().toISOString()});',
)
replace_once(
    "app/admin_playground_chat_v69.js",
    'messages.push({role:"assistant",content:assistantText,include_in_context:true,meta:{request_id:requestId,model,total_ms:totalMs,first_token_ms:firstTokenMs,response_format:String(diagnostics?.diagnostics?.response_format||"markdown")}});',
    'messages.push({role:"assistant",content:assistantText,include_in_context:true,created_at:new Date().toISOString(),meta:{request_id:requestId,model,total_ms:totalMs,first_token_ms:firstTokenMs,response_format:String(diagnostics?.diagnostics?.response_format||"markdown")}});',
)
replace_once(
    "app/admin_playground_chat_v69.js",
    'messages.push({role:"assistant",content:`请求失败：${errorText}`,include_in_context:false,error:true,meta:{request_id:requestId,model,total_ms:totalMs}});',
    'messages.push({role:"assistant",content:`请求失败：${errorText}`,include_in_context:false,error:true,created_at:new Date().toISOString(),meta:{request_id:requestId,model,total_ms:totalMs}});',
)
replace_once(
    "app/admin_playground_chat_v69.js",
    '  $chat("pgChatSend").addEventListener("click", sendMessage); $chat("pgChatNew").addEventListener("click", newConversation);',
    '  $chat("pgChatSend").addEventListener("click", sendMessage); $chat("pgChatNew").addEventListener("click", newConversation); $chat("pgChatMessages").addEventListener("click", event => { const button = event.target.closest?.("[data-copy-index]"); if (!button) return; copyMessage(Number(button.dataset.copyIndex), button); });',
)

# Historical v21.2 still stamped nested diagnostics as 0.21.2. Runtime identity
# must be owned only by runtime_contract.
replace_once(
    "app/v21_2_patch.py",
    '''            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
''',
    '''            # Historical v21.2 owns only compatibility behavior. Runtime
            # identity belongs to runtime_contract and is not rewritten here.
''',
)

# Release boundaries.
manifest_path = Path("chrome_extension/manifest.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.8.19":
    raise SystemExit(f"unexpected extension version {manifest.get('version')}")
manifest["version"] = "0.8.20"
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
replace_once("app/runtime_contract.py", 'SERVER_RUNTIME_VERSION = "0.22.48"', 'SERVER_RUNTIME_VERSION = "0.22.49"')
replace_once("app/runtime_contract.py", 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.19"', 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.20"')
runtime = read("app/runtime_contract.py")
runtime = runtime.replace("runtime-version-observability-v80-release-v02248", "linux-window-response-lifecycle-v81-release-v02249")
runtime = runtime.replace("runtime-version-observability-v80", "linux-window-response-lifecycle-v81")
write("app/runtime_contract.py", runtime)

Path("tests/test_v02249_linux_response_lifecycle.py").write_text(r'''from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_linux_launcher_does_not_prune_extension_windows_by_default():
    source = text("scripts/linux_worker_chrome_launcher.sh")
    assert "CHAT2API_TAB_INIT_PRUNE:-0" in source
    assert "--wait 45" not in source

def test_reserve_target_counts_spares_not_routed_windows():
    source = text("chrome_extension/background_reserve_pool_v29.js")
    assert "spareTotal = Math.max(0, snapshot.total - snapshot.routed)" in source
    assert "const ROUTE_IDLE_CLOSE_MS = 2 * 60 * 1000;" in source

def test_conversation_affinity_is_two_minutes():
    assert "const IDLE_CLOSE_MS = 2 * 60 * 1000;" in text("chrome_extension/conversation_routing.js")

def test_response_requires_terminal_rich_dom_settlement():
    source = text("chrome_extension/content_request_v6.js")
    assert "response_terminal_settle_revision: 81" in source
    assert "Date.now() - settleStableSince < 3000" in source

def test_multimodal_waits_for_slow_upload_processing():
    source = text("chrome_extension/content_multimodal_v78.js")
    assert "timeoutMs = 60000, stableMs = 3000" in source
    assert "upload_settle_revision: Number(settled.upload_settle_revision || 0)" in source

def test_playground_messages_have_time_and_copy_actions():
    source = text("app/admin_playground_chat_v69.js")
    assert "created_at:new Date().toISOString()" in source
    assert "data-copy-index=" in source
    assert "formatMessageTime" in source

def test_worker_occupancy_can_show_live_physical_window_count():
    source = text("app/admin_worker_presentation_v66.js")
    assert "reserve_window_total" in source
    assert "当前受管窗口" in source

def test_historical_v212_does_not_stamp_runtime_identity():
    assert 'payload["server_version"] = PATCH_VERSION' not in text("app/v21_2_patch.py")

def test_release_versions():
    runtime = text("app/runtime_contract.py")
    manifest = text("chrome_extension/manifest.json")
    assert 'SERVER_RUNTIME_VERSION = "0.22.49"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.20"' in runtime
    assert '"version": "0.8.20"' in manifest
''', encoding="utf-8")
