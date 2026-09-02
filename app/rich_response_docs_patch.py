from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_ID = "rich-response-docs-v70"
DOC_MARKER = 'data-chat2api-doc="rich-response-v70"'

RICH_RESPONSE_DOC_HTML = r'''
<section data-chat2api-doc="rich-response-v70">
<h2>模型原始格式 / 富文本回复</h2>
<p><b>不需要 chat2api 专用请求参数。</b> 对 <code>POST /v1/chat/completions</code>，当前浏览器侧会把 ChatGPT 最终回复序列化为 Markdown，并通过 OpenAI-compatible 的 <code>choices[0].message.content</code> 返回。段落、标题、列表、代码块、表格、引用、粗体/斜体/删除线、链接、Emoji/Unicode 符号，以及回复中的图片引用都会保存在这个字符串里。</p>
<p>如果外部应用只需要“收到原内容”，普通 OpenAI Chat Completions 客户端即可；如果需要“按原格式显示”，调用方还必须把 <code>message.content</code> 当作 Markdown 渲染。OpenAI SDK 负责协议和字段解析，不负责把 Markdown 自动渲染成 UI。</p>
<h3>推荐：完整最终格式（stream=false）</h3>
<div class="codebox">curl https://YOUR_HOST/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-5.5-mini",
    "messages":[{"role":"user","content":"请用标题、列表、代码块和表格回答"}],
    "stream":false
  }'</div>
<p>读取 <code>choices[0].message.content</code>。这是一个 Markdown 字符串，例如 <code>## 标题</code>、<code>- 列表</code>、围栏代码块、Markdown 表格和 <code>![图片](...)</code> 都直接位于该字段中。要求最终格式完整一致时，推荐使用 <code>stream=false</code>。</p>
<h3>OpenAI Python SDK</h3>
<div class="codebox">from openai import OpenAI

client = OpenAI(
    base_url="https://YOUR_HOST/v1",
    api_key="YOUR_MANAGED_API_KEY",
)

response = client.chat.completions.create(
    model="gpt-5.5-mini",
    messages=[{"role": "user", "content": "请按原格式回答"}],
    stream=False,
)

markdown = response.choices[0].message.content or ""
print(markdown)  # 原始 Markdown；UI 需要自行用 Markdown renderer 渲染</div>
<h3>流式调用</h3>
<div class="codebox">stream = client.chat.completions.create(
    model="gpt-5.5-mini",
    messages=[{"role": "user", "content": "请按原格式回答"}],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="")</div>
<p><code>stream=true</code> 仍使用标准 Chat Completions SSE / <code>delta.content</code>。但 ChatGPT Web 在生成结束时可能才补齐部分 DOM 格式，因此需要“最终 Markdown 与页面格式尽量一致”的业务应优先使用 <code>stream=false</code>；流式模式更适合实时展示文本增量。</p>
<h3>图片与安全边界</h3>
<ul>
<li>回复图片在 Chat Completions 中作为 Markdown 图片语法返回：<code>![alt](https://...)</code> 或 <code>![alt](data:image/...)</code>；它不是额外的 OpenAI structured image content part。</li>
<li>Worker 可读取的 <code>blob:</code> 图片会尽量转换为 <code>data:image/...</code> 后写入 Markdown；当前最多内联 4 张、单张约 4 MiB、合计约 6 MiB。</li>
<li>外部 UI 若要显示 <code>data:image</code>，其 Markdown renderer / CSP 必须允许 data image URI；否则仍能收到字符串，但图片可能不会显示。</li>
<li>渲染来自模型的 Markdown 时应启用常规 XSS/危险 URL 过滤；不要为了显示 Markdown 而直接信任任意 HTML。</li>
</ul>
<p><b>结论：</b>协议层无需额外配置；显示层需要 Markdown renderer。需要最完整的最终格式时使用标准参数 <code>stream=false</code>。</p>
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


def install_rich_response_docs_patch(app: FastAPI) -> FastAPI:
    """Add the rich-response calling contract to the console developer docs."""
    if getattr(app.state, "rich_response_docs_patch_installed", False):
        return app
    app.state.rich_response_docs_patch_installed = True

    @app.middleware("http")
    async def rich_response_console_docs(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/admin", "/developers"}:
            return response
        if "text/html" not in response.headers.get("content-type", ""):
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        if DOC_MARKER not in text:
            anchor = "<h2>上传文件</h2>"
            if anchor in text:
                text = text.replace(anchor, RICH_RESPONSE_DOC_HTML + anchor, 1)
            else:
                text = text.replace("</div></section>", RICH_RESPONSE_DOC_HTML + "</div></section>", 1)

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
