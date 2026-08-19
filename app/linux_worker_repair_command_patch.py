from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.22.21"
ADMIN_LINUX_WORKERS_ASSET = "/assets/chat2api-linux-workers.js"


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


def _patch_admin_linux_workers_js(text: str) -> str:
    """Keep failed/consumed install records safely copyable for same-Worker repair.

    An install record is intentionally disabled after terminal failure so its
    enrollment code cannot register a new Worker. If that record is already
    linked to a Worker, however, rerunning the same command on that same host is
    a valid idempotent repair path because bootstrap reuses worker.json and does
    not enroll again. The old UI disabled the copy button together with the
    enrollment record, making that supported recovery path impossible to use.
    """

    old_help = (
        "生成安装命令后会立即出现在 Worker 列表。安装命令不按时间过期；"
        "安装完成或失败后自动停用。安装阶段与结果由目标服务器实时回传。"
    )
    new_help = (
        "生成安装命令后会立即出现在 Worker 列表。完成或失败后会停止该注册码用于新 Worker 注册；"
        "若已经绑定 Worker，失败记录仍可复制原命令在原服务器上进行幂等修复安装。安装阶段与结果由目标服务器实时回传。"
    )
    text = text.replace(old_help, new_help, 1)

    old_command = '''  const commandHtml = row => {
    if (row.record_type !== "installation" || !row.install_command) return "-";
    const disabled = !row.install_enabled;
    return `<details><summary>${disabled ? "已停用" : "安装命令"}</summary><div style="min-width:360px;max-width:620px;white-space:normal;word-break:break-all"><code>${esc(row.install_command)}</code><div style="margin-top:6px"><button class="action" data-copy-install="${esc(row.install_id)}" ${disabled ? "disabled" : ""}>复制</button></div></div></details>`;
  };'''
    new_command = '''  const commandHtml = row => {
    if (row.record_type !== "installation" || !row.install_command) return "-";
    const inactive = !row.install_enabled;
    const failed = String(row.install_state || "").toLowerCase() === "failed";
    const repairable = inactive && failed && Boolean(String(row.worker_id || "").trim());
    const blocked = inactive && !repairable;
    const summary = repairable ? "修复安装" : inactive ? "注册已停用" : "安装命令";
    const note = repairable
      ? `<div style="margin-top:7px;color:#94a3b8;font-size:11px;line-height:1.5">注册码已停止用于新 Worker 注册；此命令仅用于当前已注册 Worker 的原服务器幂等修复。</div>`
      : inactive
        ? `<div style="margin-top:7px;color:#94a3b8;font-size:11px;line-height:1.5">该注册码已停用，不能用于注册新的 Worker。</div>`
        : "";
    const label = repairable ? "复制修复命令" : "复制";
    return `<details><summary>${summary}</summary><div style="min-width:360px;max-width:620px;white-space:normal;word-break:break-all"><code>${esc(row.install_command)}</code>${note}<div style="margin-top:6px"><button class="action" data-copy-install="${esc(row.install_id)}" ${blocked ? "disabled" : ""}>${label}</button></div></div></details>`;
  };'''
    if old_command in text:
        text = text.replace(old_command, new_command, 1)

    old_copy = '''    const copyId = target.dataset.copyInstall;
    if (copyId) { const row=latestRows.find(item=>item.install_id===copyId); if(row?.install_command) await navigator.clipboard.writeText(row.install_command); return; }'''
    new_copy = '''    const copyId = target.dataset.copyInstall;
    if (copyId) {
      const row = latestRows.find(item => item.install_id === copyId);
      const command = String(row?.install_command || "");
      if (!command) { alert("没有可复制的安装命令。"); return; }
      let copied = false;
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        try { await navigator.clipboard.writeText(command); copied = true; } catch (_) {}
      }
      if (!copied) {
        const temporary = document.createElement("textarea");
        temporary.value = command;
        temporary.setAttribute("readonly", "");
        temporary.style.cssText = "position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0";
        document.body.appendChild(temporary);
        temporary.focus();
        temporary.select();
        try { copied = Boolean(document.execCommand("copy")); } catch (_) { copied = false; }
        temporary.remove();
      }
      if (!copied) { alert("复制失败，请展开安装命令后手动选择复制。"); return; }
      const original = target.textContent;
      target.textContent = "已复制";
      target.title = "安装命令已复制到剪贴板";
      setTimeout(() => { if (target.isConnected) { target.textContent = original; target.title = ""; } }, 1400);
      return;
    }'''
    if old_copy in text:
        text = text.replace(old_copy, new_copy, 1)

    return text


def install_linux_worker_repair_command_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_repair_command_patch_installed", False):
        return app
    app.state.linux_worker_repair_command_patch_installed = True

    @app.middleware("http")
    async def linux_worker_repair_command_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != ADMIN_LINUX_WORKERS_ASSET:
            return response

        raw = await _response_bytes(response)
        text = _patch_admin_linux_workers_js(raw.decode("utf-8", errors="replace"))
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="application/javascript", headers=headers)

    return app
