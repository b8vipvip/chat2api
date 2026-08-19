from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import __version__ as PACKAGE_VERSION
from .live_voice_patch import LIVE_PROTOCOL_VERSION


# These values describe different compatibility surfaces on purpose. Do not
# collapse them into a single version number: package releases, the layered
# server runtime/console, the Chrome Bridge, and the realtime wire protocol can
# evolve independently.
SERVER_RUNTIME_VERSION = "0.22.5"
CHROME_BRIDGE_VERSION = "0.8.1"
PRODUCTION_ENTRYPOINT = "app.entry:app"
VERSION_CONTRACT_VERSION = 1
ADMIN_VERSION_ASSET = "/assets/chat2api-runtime-version.js"
ADMIN_EXTENSION_COLUMNS_ASSET = "/assets/chat2api-extension-columns.js"
ADMIN_LINUX_WORKERS_ASSET = "/assets/chat2api-linux-workers.js"


def version_contract_payload(app: FastAPI) -> dict[str, Any]:
    runtime_version = str(getattr(app, "version", "") or SERVER_RUNTIME_VERSION)
    return {
        "object": "chat2api.version",
        "contract_version": VERSION_CONTRACT_VERSION,
        "server": {
            "package_version": PACKAGE_VERSION,
            "runtime_version": runtime_version,
            "expected_runtime_version": SERVER_RUNTIME_VERSION,
            "entrypoint": PRODUCTION_ENTRYPOINT,
            "runtime_aligned": runtime_version == SERVER_RUNTIME_VERSION,
        },
        "chrome_bridge": {
            "version": CHROME_BRIDGE_VERSION,
        },
        "protocols": {
            "realtime_voice": LIVE_PROTOCOL_VERSION,
        },
    }


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


def _admin_version_script() -> str:
    version = json.dumps(SERVER_RUNTIME_VERSION)
    return f'''(() => {{
  const VERSION = {version};
  const LABEL = `v${{VERSION}}`;
  const BRAND = `Server Console · ${{LABEL}}`;
  const VERSION_ONLY = /^v\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.-]+)?$/;

  function patchBrand() {{
    const node = document.querySelector(".brand small");
    if (node && node.textContent !== BRAND) node.textContent = BRAND;
  }}

  function patchStatusVersion() {{
    const node = document.getElementById("status");
    if (!node) return;
    const value = String(node.textContent || "").trim();
    if (VERSION_ONLY.test(value) && value !== LABEL) node.textContent = LABEL;
  }}

  function patchVersion() {{
    document.documentElement.dataset.chat2apiRuntimeVersion = VERSION;
    patchBrand();
    patchStatusVersion();
  }}

  const baseShow = typeof globalThis.show === "function" ? globalThis.show : null;
  if (baseShow && !baseShow.__chat2apiRuntimeVersionOwner) {{
    const wrappedShow = async (...args) => {{
      const result = await baseShow(...args);
      patchVersion();
      return result;
    }};
    wrappedShow.__chat2apiRuntimeVersionOwner = true;
    globalThis.show = wrappedShow;
  }}

  const observeVersionNode = node => {{
    if (!node || typeof MutationObserver !== "function") return;
    new MutationObserver(() => patchVersion()).observe(node, {{
      childList: true,
      characterData: true,
      subtree: true,
    }});
  }}

  patchVersion();
  observeVersionNode(document.querySelector(".brand small"));
  observeVersionNode(document.getElementById("status"));
  setTimeout(patchVersion, 150);
  setTimeout(patchVersion, 650);
}})();\n'''


def install_runtime_contract(app: FastAPI) -> FastAPI:
    if getattr(app.state, "runtime_contract_installed", False):
        return app

    # The runtime contract is installed last by app.entry and is the canonical
    # owner of the current server/console version. Historical feature patches
    # keep their own internal patch versions but must not win the final public
    # version.
    app.state.runtime_contract_installed = True
    app.version = SERVER_RUNTIME_VERSION

    @app.get("/version", include_in_schema=False)
    async def runtime_version() -> JSONResponse:
        return JSONResponse(version_contract_payload(app), headers={"Cache-Control": "no-store"})

    @app.get(ADMIN_VERSION_ASSET, include_in_schema=False)
    async def admin_version_asset() -> Response:
        return Response(_admin_version_script(), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def runtime_contract_admin_html(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or response.status_code != 200:
            return response
        content_type = str(response.headers.get("content-type") or "")
        if "text/html" not in content_type:
            return response
        body = (await _response_bytes(response)).decode("utf-8", errors="replace")
        marker = f'<script src="{ADMIN_VERSION_ASSET}"></script>'
        if marker not in body:
            needle = "</body>"
            body = body.replace(needle, f"{marker}{needle}", 1) if needle in body else body + marker
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-encoding", "etag"}}
        headers["Cache-Control"] = "no-store"
        return Response(body, status_code=response.status_code, media_type="text/html", headers=headers)

    return app