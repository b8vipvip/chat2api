from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.22.20"
BOOTSTRAP_PATH = "/bootstrap/linux-worker.sh"
STABLE_TABLE_ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _patch_bootstrap(text: str) -> str:
    """Give first Chrome-for-Testing download enough time on low-bandwidth VPSes.

    The launcher permits a single download attempt to run for up to 600 seconds,
    but the previous installer declared failure after only 180 seconds. On a 4
    Mbps server the Chrome archive alone can legitimately take longer than three
    minutes, leaving systemd services active while the installer incorrectly
    reports a failed health stage.
    """

    old_loop = """BROWSER_READY=0
for attempt in $(seq 1 180); do"""
    new_loop = """BROWSER_READY=0
BROWSER_READY_TIMEOUT=\"${CHAT2API_BROWSER_READY_TIMEOUT:-900}\"
[[ \"$BROWSER_READY_TIMEOUT\" =~ ^[0-9]+$ ]] || BROWSER_READY_TIMEOUT=900
(( BROWSER_READY_TIMEOUT < 180 )) && BROWSER_READY_TIMEOUT=180
(( BROWSER_READY_TIMEOUT > 1800 )) && BROWSER_READY_TIMEOUT=1800
for attempt in $(seq 1 \"$BROWSER_READY_TIMEOUT\"); do"""
    if old_loop in text:
        text = text.replace(old_loop, new_loop, 1)

    text = text.replace(
        "if (( attempt == 1 || attempt % 10 == 0 )); then",
        "if (( attempt == 1 || attempt % 30 == 0 )); then",
        1,
    )
    text = text.replace(
        "（${attempt}/180）",
        "（${attempt}/${BROWSER_READY_TIMEOUT}）",
        1,
    )

    old_failure = 'LAST_MESSAGE="Worker 浏览器未在限定时间内就绪；失败服务：${FAILED_UNITS:-none}"'
    new_failure = """CFT_BINARY_STATE=\"未下载完成\"
  [[ -x /home/chat2api/.cache/chat2api-chrome-for-testing/chrome ]] && CFT_BINARY_STATE=\"已下载\"
  CHROME_PROCESS_STATE=\"未运行\"
  if ps -u chat2api -o args= 2>/dev/null \\
      | grep -F -- \"--user-data-dir=${PROFILE_DIR}\" \\
      | grep -E '[Cc]hrome' >/dev/null 2>&1; then
    CHROME_PROCESS_STATE=\"已运行\"
  fi
  LAST_MESSAGE=\"Worker 浏览器未在 ${BROWSER_READY_TIMEOUT} 秒内就绪；Chrome for Testing：${CFT_BINARY_STATE}；浏览器进程：${CHROME_PROCESS_STATE}；失败服务：${FAILED_UNITS:-none}\""""
    if old_failure in text:
        text = text.replace(old_failure, new_failure, 1)
    return text


def _patch_stable_table_js(text: str) -> str:
    """Add one-click copying for the expanded installation progress details."""

    old_details = 'return `<details><summary>${summary}</summary><div style="min-width:300px;max-width:560px;white-space:normal;line-height:1.55;margin-top:6px">${details}</div></details>`;'
    new_details = 'return `<details><summary>${summary}</summary><div data-install-progress-detail-v2220="1" style="min-width:300px;max-width:560px;white-space:normal;line-height:1.55;margin-top:6px">${details}</div><div style="margin-top:8px"><button class="action" type="button" data-copy-install-progress-v2220="1">复制详情</button></div></details>`;'
    if old_details in text:
        text = text.replace(old_details, new_details, 1)

    listener = 'tbody.addEventListener("click", event => {'
    enhanced_listener = '''tbody.addEventListener("click", event => {
      const copyProgress = event.target.closest?.("[data-copy-install-progress-v2220]");
      if (copyProgress) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const details = copyProgress.closest("details");
        const body = details?.querySelector("[data-install-progress-detail-v2220]");
        const summary = String(details?.querySelector("summary")?.textContent || "").trim();
        const lines = Array.from(body?.children || []).map(node => String(node.textContent || "").trim()).filter(Boolean);
        const text = [summary ? `安装进度：${summary}` : "", ...lines].filter(Boolean).join("\\n");
        if (!text) return;
        const original = copyProgress.textContent;
        const markCopied = () => {
          copyProgress.textContent = "已复制";
          setTimeout(() => { if (copyProgress.isConnected) copyProgress.textContent = original; }, 1200);
        };
        if (navigator.clipboard?.writeText) {
          navigator.clipboard.writeText(text).then(markCopied).catch(() => alert("复制失败，请确认浏览器允许剪贴板访问。"));
        } else {
          const temporary = document.createElement("textarea");
          temporary.value = text;
          temporary.style.cssText = "position:fixed;left:-9999px;top:0";
          document.body.appendChild(temporary);
          temporary.select();
          try { document.execCommand("copy"); markCopied(); } catch (_) { alert("复制失败，请手动选择详情文本。" ); }
          temporary.remove();
        }
        return;
      }'''
    if listener in text and "data-copy-install-progress-v2220" in text and "const copyProgress" not in text:
        text = text.replace(listener, enhanced_listener, 1)
    return text


def install_linux_worker_install_ux_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_install_ux_patch_installed", False):
        return app
    app.state.linux_worker_install_ux_patch_installed = True

    @app.middleware("http")
    async def linux_worker_install_ux(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path not in {BOOTSTRAP_PATH, STABLE_TABLE_ASSET}:
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        if path == BOOTSTRAP_PATH:
            text = _patch_bootstrap(text)
        else:
            text = _patch_stable_table_js(text)

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        media_type = "application/javascript" if path == STABLE_TABLE_ASSET else response.media_type or "text/plain"
        return Response(text, status_code=response.status_code, media_type=media_type, headers=headers)

    return app
