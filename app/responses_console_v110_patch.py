from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from .user_commerce import USER_SESSION_COOKIE


PATCH_REVISION = 110
USER_ASSET = "/assets/chat2api-responses-console-v110.js"
USER_SCRIPT_MARKER = 'data-chat2api-responses-console="v110"'
DOC_MARKER = 'data-chat2api-doc="responses-v110"'


class ResponsesPlaygroundBody(BaseModel):
    protocol: Literal["responses"]
    key_id: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20000)
    tool: Literal["none", "web_search"] = "none"


RESPONSES_DOC_HTML = r'''
<section data-chat2api-doc="responses-v110">
<h2>Responses API（推荐）</h2>
<p><b>v0.22.69 起新增 <code>POST /v1/responses</code>，原 <code>/v1/chat/completions</code> 继续兼容。</b> 普通聊天可继续使用旧接口；新 Agent、Codex/FDEX、网页搜索、函数/命名空间/自定义工具和工具结果续接应优先使用 Responses。</p>
<h3>基础调用</h3>
<div class="codebox">curl https://YOUR_HOST/v1/responses \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-5.6-sol",
    "input":"你好",
    "stream":false
  }'</div>
<p>非流式结果读取顶层 <code>output_text</code>，完整结构化结果位于 <code>output[]</code>。</p>
<h3>原生 Web Search</h3>
<div class="codebox">curl https://YOUR_HOST/v1/responses \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-5.6-sol",
    "input":"搜索 OpenAI 今天的更新并总结",
    "tools":[{"type":"web_search"}],
    "tool_choice":"required",
    "stream":false
  }'</div>
<p>Web Search 使用 ChatGPT Web 原生结构化工具生命周期，并映射为 Responses 的 <code>web_search_call</code> output item。</p>
<h3>Function / Namespace / Custom / Tool Search</h3>
<div class="codebox">POST /v1/responses
{
  "model":"gpt-5.6-sol",
  "input":"读取项目状态并判断下一步",
  "tools":[{
    "type":"function",
    "name":"read_project_status",
    "description":"Read project status",
    "parameters":{"type":"object","properties":{},"additionalProperties":false}
  }]
}</div>
<p>当响应的 <code>output[]</code> 出现 <code>function_call</code> / <code>custom_tool_call</code> / <code>tool_search_call</code> 时，<b>工具由调用方执行</b>。chat2api 不会伪造 shell、apply_patch 或 MCP 的执行结果。</p>
<h3>工具结果续接</h3>
<div class="codebox">POST /v1/responses
{
  "model":"gpt-5.6-sol",
  "previous_response_id":"resp_...",
  "input":[{
    "type":"function_call_output",
    "call_id":"call_...",
    "output":"{\"status\":\"ok\"}"
  }],
  "tools":[{
    "type":"function",
    "name":"read_project_status",
    "parameters":{"type":"object","properties":{}}
  }]
}</div>
<p>也支持 <code>custom_tool_call_output</code>、<code>mcp_tool_call_output</code> 和 <code>tool_search_output</code>。调用方应保留 <code>call_id</code>，并用 <code>previous_response_id</code> 继续同一工具循环。</p>
<h3>兼容策略</h3>
<ul>
<li><code>/v1/chat/completions</code>、<code>messages[]</code>、标准 Chat Completions SSE 继续可用，不要求老客户端迁移。</li>
<li>Responses 使用 <code>input</code> / <code>output[]</code> / <code>output_text</code>，工具调用不会自动转换成旧 Chat Completions 的 tool_calls。</li>
<li>FDEX/Codex 或其他 Agent 客户端需要完整工具闭环时应配置 <code>/v1/responses</code>。</li>
</ul>
</section>
'''.strip()


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


def _clone_html(response: Response, text: str) -> Response:
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)


def _public_error(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            text = str(error.get("message") or "").strip()
            if text:
                return text[:500]
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:500]
    return fallback


def install_responses_console_v110_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_console_v110_installed", False):
        return app
    app.state.responses_console_v110_installed = True

    settings = app.state.settings
    accounts = app.state.user_accounts
    pricing = app.state.user_pricing
    api_keys = app.state.api_keys

    @app.get(USER_ASSET, include_in_schema=False)
    async def responses_console_v110_js() -> Response:
        return Response(
            Path(__file__).with_name("responses_console_v110.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def responses_console_v110(request: Request, call_next):
        path = request.url.path

        # Keep the historical /api/user/playground contract intact when protocol
        # is omitted or set to Chat Completions. Only Responses requests are
        # intercepted here, so old browser/client code remains backward compatible.
        if path == "/api/user/playground" and request.method == "POST":
            try:
                raw = await request.body()
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError, TypeError):
                parsed = {}
            if isinstance(parsed, dict) and str(parsed.get("protocol") or "") == "responses":
                try:
                    body = ResponsesPlaygroundBody.model_validate(parsed)
                except ValidationError as error:
                    return JSONResponse({"detail": str(error)}, status_code=422)

                user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
                if not user:
                    return JSONResponse({"detail": "请先登录用户控制台"}, status_code=401)
                user_id = str(user.get("user_id") or "")
                if not accounts.owns_key(user_id, body.key_id):
                    return JSONResponse({"detail": "请选择自己的 API Key"}, status_code=404)
                if not pricing.model(body.model):
                    return JSONResponse({"detail": "该模型不在当前模型广场中"}, status_code=400)
                try:
                    secret = api_keys.reveal(body.key_id)
                except (KeyError, ValueError):
                    return JSONResponse({"detail": "API Key 当前不可用于测试"}, status_code=409)

                payload: dict[str, Any] = {
                    "model": body.model,
                    "input": body.prompt,
                    "stream": False,
                }
                if body.tool == "web_search":
                    payload["tools"] = [{"type": "web_search"}]
                    payload["tool_choice"] = "required"

                base = str(request.base_url).rstrip("/")
                try:
                    async with httpx.AsyncClient(timeout=max(120.0, float(getattr(settings, "request_timeout_seconds", 120)))) as client:
                        upstream = await client.post(
                            f"{base}/v1/responses",
                            headers={"Authorization": f"Bearer {secret}"},
                            json=payload,
                        )
                except httpx.HTTPError:
                    return JSONResponse({"detail": "Responses 测试请求暂时无法完成"}, status_code=502)
                try:
                    result: Any = upstream.json()
                except ValueError:
                    result = {"error": {"message": "API 返回了无法解析的响应"}}
                if upstream.status_code >= 400:
                    return JSONResponse(
                        {"detail": _public_error(result, "Responses 测试请求失败")},
                        status_code=upstream.status_code,
                    )
                return JSONResponse({"ok": True, "protocol": "responses", "response": result})

        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response

        if path == "/console":
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            if USER_SCRIPT_MARKER not in text:
                marker = f'<script {USER_SCRIPT_MARKER} src="{USER_ASSET}"></script>'
                text = text.replace("</body>", marker + "</body>", 1)
            return _clone_html(response, text)

        if path == "/developers":
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            if DOC_MARKER not in text:
                anchor = '<section data-chat2api-doc="rich-response-v70">'
                if anchor in text:
                    text = text.replace(anchor, RESPONSES_DOC_HTML + anchor, 1)
                else:
                    anchor = "<h2>上传文件</h2>"
                    if anchor in text:
                        text = text.replace(anchor, RESPONSES_DOC_HTML + anchor, 1)
                    else:
                        text = text.replace("</div></section>", RESPONSES_DOC_HTML + "</div></section>", 1)
            return _clone_html(response, text)

        return response

    return app
