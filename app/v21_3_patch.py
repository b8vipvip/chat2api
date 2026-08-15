from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.21.3"
BUNDLE_ASSET = "/assets/chat2api-admin-latest.js"
LEGACY_SCRIPT_RE = re.compile(r'<script\s+src="/assets/chat2api-v[^\"]+\.js"></script>')
ADMIN_SCRIPT_ORDER = [
    "admin_v6.js",
    "admin_v7.js",
    "admin_v8.js",
    "admin_v9.js",
    "admin_v10.js",
    "admin_v11.js",
    "admin_v12.js",
    "admin_v13.js",
    "admin_v14.js",
    "admin_v15.js",
    "admin_v16.js",
    "admin_v17.js",
    "admin_v17_1.js",
    "admin_v18.js",
    "admin_v20.js",
    "admin_v20_1.js",
    "admin_v20_2.js",
    "admin_v20_3.js",
    "admin_v21.js",
    "admin_v21_1.js",
]


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _bundle_source() -> str:
    root = Path(__file__).parent
    fallback = (root / "admin_v21_3.js").read_text(encoding="utf-8")
    parts = [
        'window.__chat2apiAdminPatchErrors = window.__chat2apiAdminPatchErrors || [];',
        'console.info("[chat2api] loading ordered admin bundle v0.21.3");',
    ]
    for filename in ADMIN_SCRIPT_ORDER:
        path = root / filename
        if not path.exists():
            parts.append(
                f'window.__chat2apiAdminPatchErrors.push({{file:{json.dumps(filename)},error:"missing"}});'
            )
            continue
        source = path.read_text(encoding="utf-8")
        parts.extend([
            f'\n/* BEGIN {filename} */',
            'try {',
            source,
            '} catch (error) {',
            f'  console.error("[chat2api admin] {filename} failed", error);',
            f'  window.__chat2apiAdminPatchErrors.push({{file:{json.dumps(filename)},error:String(error?.stack || error)}});',
            '}',
            f'/* END {filename} */\n',
        ])
        # v17 owns the administrator-session migration. If it fails at runtime,
        # install the standalone recovery layer before later patches execute.
        if filename == "admin_v17.js":
            parts.extend([
                '/* v21.3 admin auth checkpoint */',
                'try {',
                fallback,
                '} catch (error) { console.error("[chat2api admin] auth checkpoint failed", error); }',
            ])

    # Final pass guarantees the current version label and retries the recovery
    # layer if a later historical patch changed the login DOM.
    parts.extend([
        '/* v21.3 final admin recovery pass */',
        'try {',
        fallback,
        '} catch (error) { console.error("[chat2api admin] final recovery failed", error); }',
        'console.info("[chat2api] admin bundle ready", {version:"0.21.3", errors:window.__chat2apiAdminPatchErrors});',
    ])
    return "\n".join(parts)


def install_v21_3_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION

    @app.get(BUNDLE_ASSET)
    async def admin_latest_bundle() -> Response:
        return Response(
            _bundle_source(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def v21_3_single_admin_bundle(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            text = LEGACY_SCRIPT_RE.sub("", text)
            marker = f'<script src="{BUNDLE_ASSET}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview", "/api/admin/concurrency"}
            or path.startswith("/api/admin/")
        ):
            raw = await _response_bytes(response)
            try:
                payload: Any = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        return response

    return app
