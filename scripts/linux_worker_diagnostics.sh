#!/usr/bin/env bash
set -euo pipefail

# Root-only, fixed-scope diagnostic collector for the Linux Worker Agent.
# It accepts no arguments so a compromised unprivileged Agent cannot turn it
# into an arbitrary journal reader or command runner.

UNITS=(
  chat2api-worker-agent.service
  chat2api-chrome.service
  chat2api-xray.service
  chat2api-xvfb.service
  chat2api-worker-watchdog.service
  chat2api-extension-autoreload.service
)
EXTENSION_DIR=/opt/chat2api-worker/chrome_extension
STATE_DIR=/var/lib/chat2api-worker
WORKER_PYTHON=/opt/chat2api-worker-venv/bin/python

redact() {
  sed -E \
    -e 's/wbind_[A-Za-z0-9._-]+/wbind_[REDACTED]/g' \
    -e 's/(worker_token["=: ]+)[A-Za-z0-9._-]+/\1[REDACTED]/Ig' \
    -e 's/(client_token["=: ]+)[A-Za-z0-9._-]+/\1[REDACTED]/Ig' \
    -e 's/(authorization:[[:space:]]*(bearer|basic)[[:space:]]+)[A-Za-z0-9+\/_=.-]+/\1[REDACTED]/Ig' \
    -e 's/([?&](token|code|key|api_key)=)[^&[:space:]]+/\1[REDACTED]/Ig'
}

run_extension_runtime_probe() {
  if [[ ! -x "${WORKER_PYTHON}" ]]; then
    printf 'probe_status=skipped worker_python_missing=%s\n' "${WORKER_PYTHON}"
    return 0
  fi
  "${WORKER_PYTHON}" <<'PY'
import asyncio
import json
import sys
import urllib.request

try:
    import websockets
except Exception as exc:
    print(f"probe_status=skipped websockets_import_error={exc}")
    raise SystemExit(0)

BASE = "http://127.0.0.1:9222"


def http_json(path):
    with urllib.request.urlopen(BASE + path, timeout=4) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def safe_target(value):
    return {
        "id": str(value.get("id") or value.get("targetId") or ""),
        "type": str(value.get("type") or ""),
        "title": str(value.get("title") or "")[:240],
        "url": str(value.get("url") or "")[:500],
    }


EXPRESSION = r'''(async () => {
  const v35 = globalThis.__CHAT2API_CAPACITY_CONTROL_V35__ || null;
  const v36 = globalThis.__CHAT2API_CAPACITY_CONTROL_V36__ || null;
  const v37 = globalThis.__CHAT2API_CAPACITY_CAPABILITY_V37__ || null;
  let storage = {};
  let storageError = "";
  try {
    storage = await chrome.storage.local.get([
      "socketState", "socketError", "socketUpdatedAt", "clientId", "boundTabId",
      "chatgptRuntimeLastError", "modelDiscoveryError"
    ]);
    if (storage.clientId) storage.clientId = String(storage.clientId);
  } catch (error) {
    storageError = String(error?.stack || error?.message || error);
  }
  let socketInfo = {};
  try {
    socketInfo = {
      socketReady: typeof socketReady === "function" ? Boolean(socketReady()) : null,
      readyState: typeof socket !== "undefined" && socket ? Number(socket.readyState) : null,
      sendSocketType: typeof sendSocket,
      trySendSocketType: typeof trySendSocket,
    };
  } catch (error) {
    socketInfo = {error: String(error?.stack || error?.message || error)};
  }
  return {
    href: globalThis.location?.href || "",
    v35: v35 ? {
      installed: true,
      handle: typeof v35.handle,
      snapshot: typeof v35.snapshot,
      resize: typeof v35.resize,
      lastResult: v35.lastResult || null,
    } : {installed:false},
    v36: v36 ? {
      installed: true,
      version: v36.version || null,
      ready: v36.ready === true,
      installed_at: v36.installed_at || null,
      last_dispatch_at: v36.last_dispatch_at || null,
      last_error: v36.last_error || "",
    } : {installed:false},
    v37: v37 ? {
      installed: true,
      version: v37.version || null,
      ready: v37.ready === true,
      installed_at: v37.installed_at || null,
      last_report_at: v37.last_report_at || null,
      last_report_reason: v37.last_report_reason || "",
      last_report_ok: v37.last_report_ok === true,
      last_error: v37.last_error || "",
      report_count: Number(v37.report_count || 0),
    } : {installed:false},
    handler: {
      type: typeof globalThis.handleServerMessage,
      v35: globalThis.handleServerMessage?.__chat2apiCapacityControlV35 === true,
      v36: globalThis.handleServerMessage?.__chat2apiCapacityControlV36 === true,
    },
    socket: socketInfo,
    storage,
    storageError,
  };
})()'''

TRIGGER = r'''(async () => {
  const v37 = globalThis.__CHAT2API_CAPACITY_CAPABILITY_V37__;
  if (!v37 || typeof v37.report !== "function") {
    return {ok:false, reason:"capacity capability v37 reporter is unavailable"};
  }
  try {
    const ok = await v37.report("diagnostics-cdp-probe");
    return {ok:Boolean(ok), state:{
      ready:v37.ready === true,
      last_report_at:v37.last_report_at || null,
      last_report_reason:v37.last_report_reason || "",
      last_report_ok:v37.last_report_ok === true,
      last_error:v37.last_error || "",
      report_count:Number(v37.report_count || 0),
    }};
  } catch (error) {
    return {ok:false, reason:String(error?.stack || error?.message || error)};
  }
})()'''


class CDP:
    def __init__(self, ws, session_id=None):
        self.ws = ws
        self.session_id = session_id
        self.next_id = 20
        self.events = []

    async def command(self, method, params=None):
        self.next_id += 1
        command_id = self.next_id
        payload = {"id": command_id, "method": method, "params": params or {}}
        if self.session_id:
            payload["sessionId"] = self.session_id
        await self.ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=7)
            message = json.loads(raw)
            if message.get("id") == command_id:
                return message
            method_name = str(message.get("method") or "")
            if method_name in {"Runtime.exceptionThrown", "Runtime.consoleAPICalled"}:
                self.events.append(message)

    async def drain_events(self, seconds=0.6):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=min(0.15, deadline - loop.time()))
            except asyncio.TimeoutError:
                continue
            try:
                message = json.loads(raw)
            except Exception:
                continue
            method_name = str(message.get("method") or "")
            if method_name in {"Runtime.exceptionThrown", "Runtime.consoleAPICalled"}:
                self.events.append(message)


async def evaluate(cdp, expression):
    response = await cdp.command("Runtime.evaluate", {
        "expression": expression,
        "awaitPromise": True,
        "returnByValue": True,
        "generatePreview": True,
    })
    result = response.get("result") or {}
    if result.get("exceptionDetails"):
        return {"exceptionDetails": result.get("exceptionDetails")}
    remote = result.get("result") or {}
    return remote.get("value") if "value" in remote else remote


async def probe_direct(target):
    ws_url = str(target.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return False
    async with websockets.connect(ws_url, open_timeout=5, close_timeout=1, max_size=4 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.command("Runtime.enable")
        print("probe_transport=direct-target")
        print("runtime_before=" + json.dumps(await evaluate(cdp, EXPRESSION), ensure_ascii=False, default=str))
        print("report_trigger=" + json.dumps(await evaluate(cdp, TRIGGER), ensure_ascii=False, default=str))
        await cdp.drain_events()
        print("runtime_after=" + json.dumps(await evaluate(cdp, EXPRESSION), ensure_ascii=False, default=str))
        if cdp.events:
            print("runtime_events=" + json.dumps(cdp.events, ensure_ascii=False, default=str))
        else:
            print("runtime_events=[]")
    return True


async def probe_browser(browser_ws):
    async with websockets.connect(browser_ws, open_timeout=5, close_timeout=1, max_size=4 * 1024 * 1024) as ws:
        browser = CDP(ws)
        targets_response = await browser.command("Target.getTargets")
        infos = ((targets_response.get("result") or {}).get("targetInfos") or [])
        candidates = [
            item for item in infos
            if str(item.get("url") or "").startswith("chrome-extension://")
            and str(item.get("type") or "") in {"service_worker", "background_page", "worker", "other"}
        ]
        if not candidates:
            print("probe_status=no-extension-runtime-target")
            print("target_types=" + json.dumps(sorted({str(item.get('type') or '') for item in infos}), ensure_ascii=False))
            return False
        target = sorted(candidates, key=lambda item: (str(item.get("type")) != "service_worker", str(item.get("url"))))[0]
        print("runtime_target=" + json.dumps(safe_target(target), ensure_ascii=False))
        attached = await browser.command("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        session_id = str(((attached.get("result") or {}).get("sessionId") or ""))
        if not session_id:
            print("probe_status=target-attach-failed")
            return False
        cdp = CDP(ws, session_id=session_id)
        await cdp.command("Runtime.enable")
        print("probe_transport=browser-target-session")
        print("runtime_before=" + json.dumps(await evaluate(cdp, EXPRESSION), ensure_ascii=False, default=str))
        print("report_trigger=" + json.dumps(await evaluate(cdp, TRIGGER), ensure_ascii=False, default=str))
        await cdp.drain_events()
        print("runtime_after=" + json.dumps(await evaluate(cdp, EXPRESSION), ensure_ascii=False, default=str))
        if cdp.events:
            print("runtime_events=" + json.dumps(cdp.events, ensure_ascii=False, default=str))
        else:
            print("runtime_events=[]")
        return True


async def main():
    try:
        targets = http_json("/json/list")
    except Exception as exc:
        print(f"probe_status=cdp-list-failed error={exc}")
        return
    direct = [
        item for item in targets
        if str(item.get("url") or "").startswith("chrome-extension://")
        and str(item.get("type") or "") in {"service_worker", "background_page", "worker", "other"}
    ]
    if direct:
        target = sorted(direct, key=lambda item: (str(item.get("type")) != "service_worker", str(item.get("url"))))[0]
        print("runtime_target=" + json.dumps(safe_target(target), ensure_ascii=False))
        try:
            if await probe_direct(target):
                print("probe_status=ok")
                return
        except Exception as exc:
            print("direct_probe_error=" + repr(exc))

    try:
        version = http_json("/json/version")
        browser_ws = str(version.get("webSocketDebuggerUrl") or "")
        if not browser_ws:
            print("probe_status=browser-websocket-missing")
            return
        ok = await probe_browser(browser_ws)
        print("probe_status=ok" if ok else "probe_status=runtime-target-unavailable")
    except Exception as exc:
        print("probe_status=browser-probe-failed error=" + repr(exc))


try:
    asyncio.run(main())
except Exception as exc:
    print("probe_status=unexpected-failure error=" + repr(exc))
PY
}

printf 'chat2api Linux Worker diagnostic bundle\n'
printf 'generated_at=%s\n' "$(date -Is)"
printf 'hostname=%s\n' "$(hostname 2>/dev/null || true)"
printf 'kernel=%s\n' "$(uname -srmo 2>/dev/null || true)"
printf '\n===== service state =====\n'
for unit in "${UNITS[@]}" chat2api-worker-watchdog.timer chat2api-extension-autoreload.timer; do
  printf '%s: ' "$unit"
  systemctl is-active "$unit" 2>/dev/null || true
done

printf '\n===== service properties =====\n'
for unit in chat2api-worker-agent.service chat2api-chrome.service chat2api-xray.service chat2api-xvfb.service; do
  printf '\n--- %s ---\n' "$unit"
  systemctl show "$unit" \
    -p ActiveState -p SubState -p MainPID -p ExecMainPID -p ExecMainStatus \
    -p NRestarts -p ActiveEnterTimestamp -p FragmentPath --no-pager 2>/dev/null || true
done

printf '\n===== browser / extension =====\n'
if [[ -L /home/chat2api/.cache/chat2api-chrome-for-testing/chrome ]]; then
  printf 'chrome_link=%s\n' "$(readlink -f /home/chat2api/.cache/chat2api-chrome-for-testing/chrome 2>/dev/null || true)"
fi
if [[ -r "${EXTENSION_DIR}/manifest.json" ]]; then
  printf 'extension_version=%s\n' "$(jq -r '.version // ""' "${EXTENSION_DIR}/manifest.json" 2>/dev/null || true)"
fi
printf 'capacity_control_v35_source=%s\n' "$([[ -r "${EXTENSION_DIR}/background_capacity_control_v35.js" ]] && echo present || echo missing)"
printf 'capacity_control_v36_source=%s\n' "$([[ -r "${EXTENSION_DIR}/background_capacity_control_v36.js" ]] && echo present || echo missing)"
printf 'capacity_capability_v37_source=%s\n' "$([[ -r "${EXTENSION_DIR}/background_capacity_capability_v37.js" ]] && echo present || echo missing)"
printf 'capacity_control_v35_loaded_by_entry=%s\n' "$([[ -r "${EXTENSION_DIR}/background_entry.js" ]] && grep -q 'background_capacity_control_v35.js' "${EXTENSION_DIR}/background_entry.js" && echo yes || echo no)"
printf 'capacity_control_v36_loaded_by_entry=%s\n' "$([[ -r "${EXTENSION_DIR}/background_entry.js" ]] && grep -q 'background_capacity_control_v36.js' "${EXTENSION_DIR}/background_entry.js" && echo yes || echo no)"
printf 'capacity_capability_v37_loaded_by_entry=%s\n' "$([[ -r "${EXTENSION_DIR}/background_entry.js" ]] && grep -q 'background_capacity_capability_v37.js' "${EXTENSION_DIR}/background_entry.js" && echo yes || echo no)"
if [[ -r "${STATE_DIR}/extension-state.env" ]]; then
  printf '%s\n' '--- extension-state.env ---'
  grep -E '^(EXTENSION_VERSION|EXTENSION_FINGERPRINT|GIT_COMMIT|CENTRAL_BUNDLE_SHA256|APPLIED_AT)=' "${STATE_DIR}/extension-state.env" 2>/dev/null || true
fi
for marker in extension-applied.sha256 extension-failed.sha256 extension-central-bundle.sha256; do
  if [[ -r "${STATE_DIR}/${marker}" ]]; then
    printf '%s=%s\n' "${marker}" "$(head -c 80 "${STATE_DIR}/${marker}" 2>/dev/null || true)"
  fi
done
printf 'chrome_processes=%s\n' "$(pgrep -u chat2api -fc 'chrome' 2>/dev/null || true)"
printf 'cdp_9222='; ss -lnt 2>/dev/null | awk '$4 ~ /127\.0\.0\.1:9222$/ {found=1} END {print found ? "listening" : "not_listening"}'

printf '\n===== extension service worker runtime / CDP probe =====\n'
run_extension_runtime_probe 2>&1 | redact || true

printf '\n===== recent journal (last 90 minutes, max 600 lines per unit) =====\n'
for unit in "${UNITS[@]}"; do
  printf '\n--- %s ---\n' "$unit"
  journalctl -u "$unit" --since '-90 min' -n 600 --no-pager -o short-iso 2>&1 || true
done | redact
