from pathlib import Path
import re


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"missing marker for {label}")
    return text.replace(old, new, 1)


def re_sub_once(text, pattern, replacement, label, flags=re.S):
    out, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"expected one {label}, found {count}")
    return out


# 1) Device Worker Manager: wider responsive dialog, no secondary identity line,
# and non-wrapping wider action buttons.
path = "app/admin_worker_identity_v131.js"
text = read(path)
text = text.replace("const state = { version: 132, observer: null };", "const state = { version: 138, observer: null };", 1)
old = '''  function decorateLinuxWorkerManager() {
    const box = document.getElementById("managerWorkersV124");'''
new = '''  function decorateLinuxWorkerManager() {
    const dialog = document.getElementById("linuxWorkerManagerV124");
    if (dialog) {
      dialog.style.width = "min(1280px, calc(100vw - 28px))";
      dialog.style.maxWidth = "none";
    }
    const refresh = document.getElementById("refreshManagerV124");
    const addWorker = document.getElementById("openAddWorkerV124");
    for (const button of [refresh, addWorker]) {
      if (!button) continue;
      button.style.whiteSpace = "nowrap";
      button.style.minWidth = button === addWorker ? "108px" : "82px";
      button.style.flex = "0 0 auto";
    }
    const actionWrap = refresh?.parentElement;
    if (actionWrap && actionWrap === addWorker?.parentElement) actionWrap.style.flexWrap = "nowrap";
    const box = document.getElementById("managerWorkersV124");
    if (box) box.style.overflowX = "auto";'''
text = replace_once(text, old, new, "manager dialog layout")
text = replace_once(
    text,
    'const title = "对应 Worker 管理列表中的 Worker ID；下方保留设备内 Worker 序号和中心 Worker ID";',
    'const title = "对应 Worker 管理列表中的 Worker ID（Extension client ID）";',
    "manager worker id header title",
)
old = '''      const controllerId = String(worker.worker_id || "").trim();
      const slot = Math.max(1, Number(worker.worker_slot || index + 1));
      const version = String(worker.agent_version || "-");
      const primary = extensionId || "Extension 待连接";
      const html = `<b>${esc(primary)}</b><div class="v124-muted">设备 Worker ${slot} · 中心 ${esc(controllerId || "-")} · v${esc(version)} · 独立 Profile</div>`;'''
new = '''      const primary = extensionId || "Extension 待连接";
      const html = `<b>${esc(primary)}</b>`;'''
text = replace_once(text, old, new, "remove manager secondary worker metadata")
pattern = r'''  function decoratePersistentWindowCopy\(\) \{.*?\n  \}\n\n  function decorate\(\)'''
replacement = '''  function decoratePersistentWindowCopy() {
    const header = document.querySelector('#extensionDeviceBody')?.closest('table')?.querySelector('thead [data-chat2api-column-key="worker_settings"]');
    if (header) {
      header.title = "并发=同时执行的请求上限；窗口=该 Worker 登录后持续维持的 ChatGPT 物理窗口总数。点击编辑按钮修改。";
    }
  }

  function decorate()'''
text = re_sub_once(text, pattern, replacement, "persistent window copy decorator")
text = text.replace('document.documentElement.dataset.chat2apiWorkerIdentityRevision = "132";', 'document.documentElement.dataset.chat2apiWorkerIdentityRevision = "138";', 1)
write(path, text)

# 2) Worker list: compact N/N summary + edit icon; editor opens as a small popover.
path = "app/admin_worker_limits_clipboard_v121.js"
text = read(path)
pattern = r'''  function limitEditor\(row\) \{.*?\n  \}\n\n  async function renderLimits'''
replacement = '''  function limitEditor(row) {
    const id = String(row?.client_id || "");
    const concurrency = Math.max(1, Math.min(32, Number(row?.max_concurrency || row?.capacity?.limit_units || 1)));
    const windows = Math.max(concurrency, Math.min(32, Number(row?.max_windows || concurrency)));
    return `<div data-v121-worker-limits="${esc(id)}" style="position:relative;display:inline-flex;align-items:center;gap:7px;white-space:nowrap;min-width:78px">
      <strong data-v121-limit-summary style="font-variant-numeric:tabular-nums">${concurrency}/${windows}</strong>
      <button class="action" type="button" data-v121-edit-limits title="编辑并发 / 窗口" aria-label="编辑并发 / 窗口" style="padding:4px 7px;min-width:30px">✎</button>
      <div data-v121-limit-popover hidden style="position:absolute;right:0;top:calc(100% + 7px);z-index:80;min-width:250px;padding:12px;border:1px solid #334155;border-radius:10px;background:#111827;box-shadow:0 14px 34px rgba(0,0,0,.38);white-space:normal">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <label style="display:grid;gap:5px;font-size:12px">并发<input data-v121-concurrency type="number" min="1" max="32" value="${concurrency}" style="width:100%;padding:7px 8px"></label>
          <label style="display:grid;gap:5px;font-size:12px">窗口<input data-v121-windows type="number" min="1" max="32" value="${windows}" style="width:100%;padding:7px 8px"></label>
        </div>
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:7px;margin-top:10px">
          <span class="muted" data-v121-limit-note style="font-size:11px;margin-right:auto"></span>
          <button class="action" type="button" data-v121-cancel-limits>取消</button>
          <button class="action good" type="button" data-v121-save-limits>保存</button>
        </div>
      </div>
    </div>`;
  }

  async function renderLimits'''
text = re_sub_once(text, pattern, replacement, "compact worker limit editor")
text = text.replace(
    'headerCell.title = "并发=同时执行的请求上限；窗口=此 Worker 最多保留的按需 ChatGPT 路由窗口。窗口不会预开。";',
    'headerCell.title = "并发=同时执行的请求上限；窗口=该 Worker 登录后持续维持的物理窗口总数。点击编辑按钮修改。";',
    1,
)
anchor = '''  function installWorkerHooks() {'''
helpers = '''  function closeLimitPopovers(except = null) {
    document.querySelectorAll("[data-v121-limit-popover]").forEach(node => {
      if (node !== except) node.hidden = true;
    });
  }

  function toggleLimitPopover(button) {
    const editor = button.closest("[data-v121-worker-limits]");
    const popover = editor?.querySelector("[data-v121-limit-popover]");
    if (!popover) return;
    const opening = popover.hidden;
    closeLimitPopovers(opening ? popover : null);
    popover.hidden = !opening;
    if (opening) editor.querySelector("[data-v121-concurrency]")?.focus();
  }

'''
text = replace_once(text, anchor, helpers + anchor, "limit popover helpers")
old = '''  document.addEventListener("click", event => {
    const button = event.target?.closest?.("[data-v121-save-limits]");
    if (!button) return;
    event.preventDefault();
    saveLimits(button);
  });'''
new = '''  document.addEventListener("click", event => {
    const edit = event.target?.closest?.("[data-v121-edit-limits]");
    if (edit) {
      event.preventDefault();
      event.stopPropagation();
      toggleLimitPopover(edit);
      return;
    }
    const cancel = event.target?.closest?.("[data-v121-cancel-limits]");
    if (cancel) {
      event.preventDefault();
      event.stopPropagation();
      const popover = cancel.closest("[data-v121-limit-popover]");
      if (popover) popover.hidden = true;
      return;
    }
    const save = event.target?.closest?.("[data-v121-save-limits]");
    if (save) {
      event.preventDefault();
      event.stopPropagation();
      saveLimits(save);
      return;
    }
    if (!event.target?.closest?.("[data-v121-worker-limits]")) closeLimitPopovers();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeLimitPopovers();
  });'''
text = replace_once(text, old, new, "worker limit click delegation")
write(path, text)

# 3) Window Manager: Worker ID tail 4 + local window number.
path = "app/admin_window_manager_v88.js"
text = read(path)
text = text.replace("<th>窗口编号</th>", "<th>窗口标识</th>")
marker = '''  function rowHtml(row, closed = false) {'''
formatter = '''  function windowIdentity(clientId, windowNo) {
    const workerTail = String(clientId || "").trim().slice(-4) || "????";
    const localNo = Number(windowNo);
    return Number.isFinite(localNo) && localNo > 0 ? `${workerTail}#${localNo}` : "-";
  }

'''
text = replace_once(text, marker, formatter + marker, "window identity formatter")
text = replace_once(text, '<td>#${esc(row.window_no || "-")}</td>', '<td>${esc(windowIdentity(clientId, row.window_no))}</td>', "window identity cell")
write(path, text)

# 4) Request History: persist/derive physical window identity and display same identifier.
path = "app/request_window_observability_v117_patch.py"
text = read(path)
text = replace_once(
    text,
    '''    route_key = str(
        diagnostics.get("extension_worker_route_key")
        or diagnostics.get("conversation_api_key_id")
        or ""
    ).strip() or None
    return {''',
    '''    route_key = str(
        diagnostics.get("extension_worker_route_key")
        or diagnostics.get("conversation_api_key_id")
        or ""
    ).strip() or None
    worker_client_id = str(
        diagnostics.get("worker_client_id")
        or diagnostics.get("client_id")
        or diagnostics.get("extension_client_id")
        or ""
    ).strip() or None
    return {''',
    "diagnostic worker client id",
)
text = replace_once(
    text,
    '''        "window_route_key": route_key,
    }


def _patch_admin_html()''',
    '''        "window_route_key": route_key,
        "worker_client_id": worker_client_id,
    }


def _registry_window_fields(registry: Any, request_id: str) -> dict[str, Any]:
    request_id = str(request_id or "").strip()
    if not request_id:
        return {}
    for summary in registry.summaries():
        client_id = str(summary.get("client_id") or "").strip() or None
        metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
        snapshot = metadata.get("window_manager_v88") if isinstance(metadata.get("window_manager_v88"), dict) else {}
        for bucket in (snapshot.get("active") or [], snapshot.get("closed") or []):
            if not isinstance(bucket, dict) or str(bucket.get("request_id") or "").strip() != request_id:
                continue
            return {
                "window_number": _int_or_none(bucket.get("window_no") or bucket.get("window_number")),
                "window_id": _int_or_none(bucket.get("window_id")),
                "tab_id": _int_or_none(bucket.get("tab_id")),
                "window_route_key": str(bucket.get("route_key") or bucket.get("api_key_id") or "").strip() or None,
                "worker_client_id": client_id,
            }
    return {}


def _patch_admin_html()''',
    "registry window resolver",
)
text = text.replace(">窗口编号</th>", ">窗口标识</th>", 1)
old = '''      const windowNumber=r?.window_number??requestDiagnostics?.window_number??requestDiagnostics?.extension_window_number??requestDiagnostics?.extension_worker_index??null;
      const windowCell=requestHistoryCell(tr,windowNumber??'-');
      const rawWindowId=r?.window_id??requestDiagnostics?.routed_window_id??'-';'''
new = '''      const windowNumber=r?.window_number??requestDiagnostics?.window_number??requestDiagnostics?.extension_window_number??requestDiagnostics?.extension_worker_index??null;
      const windowWorkerId=r?.worker_client_id??r?.client_id??requestDiagnostics?.worker_client_id??requestDiagnostics?.client_id??requestDiagnostics?.extension_client_id??'';
      const workerTail=String(windowWorkerId||'').trim().slice(-4)||'????';
      const windowIdentity=windowNumber?`${workerTail}#${windowNumber}`:'-';
      const windowCell=requestHistoryCell(tr,windowIdentity);
      const rawWindowId=r?.window_id??requestDiagnostics?.routed_window_id??'-';'''
text = replace_once(text, old, new, "request history window identity renderer")
text = replace_once(text, 'windowCell.title=windowNumber?`窗口 ${windowNumber} · window_id=', 'windowCell.title=windowNumber?`窗口标识 ${windowIdentity} · window_id=', "request history window title")
text = replace_once(text, '''    telemetry = app.state.telemetry
    base_publish = broker.publish''', '''    telemetry = app.state.telemetry
    registry = app.state.registry
    base_publish = broker.publish''', "registry binding")
pattern = r'''    async def publish_with_window_observability\(request_id: str, event: dict\[str, Any\]\) -> bool:.*?\n        return published\n'''
replacement = '''    async def publish_with_window_observability(request_id: str, event: dict[str, Any]) -> bool:
        diagnostics = event.get("diagnostics") if isinstance(event, dict) else None
        fields = _window_fields(diagnostics) if isinstance(diagnostics, dict) else {
            "window_number": None,
            "window_id": None,
            "tab_id": None,
            "window_route_key": None,
            "worker_client_id": None,
        }
        registry_fields = _registry_window_fields(registry, request_id)
        for key, value in registry_fields.items():
            if fields.get(key) is None and value is not None:
                fields[key] = value

        if isinstance(diagnostics, dict):
            if fields["window_number"] is not None:
                diagnostics.setdefault("window_number", fields["window_number"])
                diagnostics.setdefault("extension_window_number", fields["window_number"])
            if fields["window_id"] is not None:
                diagnostics.setdefault("window_id", fields["window_id"])
            if fields["tab_id"] is not None:
                diagnostics.setdefault("tab_id", fields["tab_id"])
            if fields["worker_client_id"] is not None:
                diagnostics.setdefault("worker_client_id", fields["worker_client_id"])

        published = await base_publish(request_id, event)
        if published:
            if fields.get("window_number") is None or fields.get("worker_client_id") is None:
                later = _registry_window_fields(registry, request_id)
                for key, value in later.items():
                    if fields.get(key) is None and value is not None:
                        fields[key] = value
            if any(value is not None for value in fields.values()):
                await telemetry.upsert({"request_id": request_id, **fields})
        return published
'''
text = re_sub_once(text, pattern, replacement, "request window publish wrapper")
write(path, text)

# 5) Formal server-only release 0.22.88; Worker bundle remains 0.8.37.
path = "app/runtime_contract.py"
text = read(path)
for old, new in [
    ('SERVER_RUNTIME_VERSION = "0.22.87"', 'SERVER_RUNTIME_VERSION = "0.22.88"'),
    ('PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.86"', 'PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.87"'),
    ('PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.85"', 'PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.86"'),
    ('PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.84"', 'PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.85"'),
]:
    text = replace_once(text, old, new, old)
feature_suffix = "-admin-worker-layout-v138-window-identity-v138-worker-limit-popover-v138-release-v02288"
lines = text.splitlines()
for i, line in enumerate(lines):
    if line.startswith("RUNTIME_FEATURE_REVISION = "):
        if "release-v02288" not in line:
            lines[i] = line[:-1] + feature_suffix + '"'
        break
text = "\n".join(lines) + "\n"
marker = '            "window_manager_login_ready_filter_v137": True,\n'
addition = marker + '            "admin_worker_manager_layout_v138": True,\n            "window_identity_v138": True,\n            "worker_limit_popover_v138": True,\n'
text = replace_once(text, marker, addition, "v138 runtime flags")
write(path, text)

for test in Path("tests").rglob("*.py"):
    source = test.read_text(encoding="utf-8")
    updated = source.replace('SERVER_RUNTIME_VERSION == "0.22.87"', 'SERVER_RUNTIME_VERSION == "0.22.88"')
    updated = updated.replace('SERVER_RUNTIME_VERSION = "0.22.87"', 'SERVER_RUNTIME_VERSION = "0.22.88"')
    if updated != source:
        test.write_text(updated, encoding="utf-8")

path = ".github/workflows/production-image-smoke.yml"
text = read(path)
text = text.replace("payload['server']['runtime_version'] == '0.22.87'", "payload['server']['runtime_version'] == '0.22.88'", 1)
marker = "          assert payload['features']['window_manager_login_ready_filter_v137'] is True\n"
addition = marker + "          assert payload['features']['admin_worker_manager_layout_v138'] is True\n          assert payload['features']['window_identity_v138'] is True\n          assert payload['features']['worker_limit_popover_v138'] is True\n"
text = replace_once(text, marker, addition, "production smoke v138 flags")
write(path, text)

Path("tests/test_admin_ui_polish_v138_release_v02288.py").write_text(r'''from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from app.request_window_observability_v117_patch import _registry_window_fields, _window_fields

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02288_server_only_release_contract() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert SERVER_RUNTIME_VERSION == "0.22.88"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.37"
    assert payload["server"]["runtime_aligned"] is True
    assert payload["features"]["admin_worker_manager_layout_v138"] is True
    assert payload["features"]["window_identity_v138"] is True
    assert payload["features"]["worker_limit_popover_v138"] is True
    assert "release-v02288" in payload["server"]["feature_revision"]


def test_worker_manager_is_wide_and_secondary_metadata_is_removed() -> None:
    source = read("app/admin_worker_identity_v131.js")
    assert "min(1280px, calc(100vw - 28px))" in source
    assert 'button.style.whiteSpace = "nowrap"' in source
    assert 'const html = `<b>${esc(primary)}</b>`;' in source
    assert "设备 Worker ${slot} · 中心" not in source
    assert "常驻窗口池跟随并发" not in source
    assert "常驻窗口池独立设置" not in source


def test_worker_limit_cell_is_compact_summary_with_popover_editor() -> None:
    source = read("app/admin_worker_limits_clipboard_v121.js")
    assert "data-v121-limit-summary" in source
    assert "${concurrency}/${windows}" in source
    assert "data-v121-edit-limits" in source
    assert "data-v121-limit-popover hidden" in source
    assert "data-v121-cancel-limits" in source
    assert "窗口跟随并发" not in source
    assert "窗口独立设置" not in source


def test_window_manager_uses_worker_tail_plus_local_window_number() -> None:
    source = read("app/admin_window_manager_v88.js")
    assert source.count("<th>窗口标识</th>") == 2
    assert 'String(clientId || "").trim().slice(-4)' in source
    assert "`${workerTail}#${localNo}`" in source
    assert '<td>${esc(windowIdentity(clientId, row.window_no))}</td>' in source


def test_request_history_uses_same_window_identity_and_registry_fallback() -> None:
    source = read("app/request_window_observability_v117_patch.py")
    assert ">窗口标识</th>" in source
    assert "`${workerTail}#${windowNumber}`" in source
    fields = _window_fields({"window_number": 11, "client_id": "ext_pCzTxpdSpaur"})
    assert fields["window_number"] == 11
    assert fields["worker_client_id"] == "ext_pCzTxpdSpaur"

    class Registry:
        def summaries(self):
            return [{
                "client_id": "ext_pCzTxpdSpaur",
                "metadata": {"window_manager_v88": {"active": [{"request_id": "req_demo", "window_no": 12, "window_id": 72}]}}
            }]

    resolved = _registry_window_fields(Registry(), "req_demo")
    assert resolved["worker_client_id"] == "ext_pCzTxpdSpaur"
    assert resolved["window_number"] == 12
    assert resolved["window_id"] == 72
''', encoding="utf-8")
