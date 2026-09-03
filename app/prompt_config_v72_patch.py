from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .prompt_config import PromptConfigStore, SYSTEM_DEFAULT_PREFIX


PATCH_ID = "prompt-config-v75"
ASSET_PATH = "/assets/chat2api-prompt-config-v72.js"
ASSET_V75_PATH = "/assets/chat2api-prompt-config-v75.js"
PROMPT_THEME_MARKER = "data-chat2api-prompt-theme-v74"
PROMPT_THEME_V74 = f'''<style {PROMPT_THEME_MARKER}="1">
#pcSystemDefaultPrefix {{
  background: var(--panel2) !important;
  color: var(--text) !important;
  border-color: var(--line) !important;
  -webkit-text-fill-color: var(--text) !important;
  opacity: 1 !important;
  line-height: 1.55;
}}
#pcSystemDefaultPrefix:focus {{
  outline: 1px solid var(--accent);
  outline-offset: 1px;
}}
#pcSystemDefaultPrefix::selection {{
  background: #315d9b;
  color: #ffffff;
  -webkit-text-fill-color: #ffffff;
}}
#view-prompt-config .pc-inline-actions {{
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: 8px 0 4px;
}}
#view-prompt-config button {{
  border: 1px solid #2f7d5b !important;
  background: #154634 !important;
  color: #f1fff8 !important;
  border-radius: 9px;
  padding: 8px 12px;
  cursor: pointer;
}}
#view-prompt-config button:hover {{
  background: #1b5b43 !important;
  border-color: #39d6a1 !important;
}}
#view-prompt-config button:disabled {{
  opacity: .62;
  cursor: wait;
}}
</style>'''


def install_prompt_config_v72_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "prompt_config_v72_installed", False):
        return app

    settings = app.state.settings
    telemetry = app.state.telemetry
    registry = app.state.registry
    store = PromptConfigStore(settings.data_dir)
    app.state.prompt_config = store

    # app.main imported build_prompt into its module namespace. Decorate that exact
    # runtime symbol so token accounting and the Worker payload see the same final
    # prompt. Tests/importers that use app.prompting directly remain deterministic.
    from . import main as main_module

    base_build_prompt = main_module.build_prompt

    def configured_build_prompt(messages, mode="last_user"):
        prompt = base_build_prompt(messages, mode)
        final, _meta = store.apply(prompt)
        return final

    main_module.build_prompt = configured_build_prompt

    # Capture the exact chat.request prompt at the final server->Worker boundary.
    # Also mark the prompt policy as server-owned. New Workers use this marker to
    # respect an administrator-edited/empty system prompt instead of re-injecting
    # the historical compatibility policy. Old servers still get Worker fallback.
    base_send = registry.send

    async def send_with_prompt_audit(client_id: str, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict) and payload.get("type") == "chat.request":
            system_default_prefix = str(store.config.get("system_default_prefix") or "")
            options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
            diagnostics = (
                options.get("chat2api_diagnostics")
                if isinstance(options.get("chat2api_diagnostics"), dict)
                else {}
            )
            payload["options"] = {
                **options,
                "chat2api_diagnostics": {
                    **diagnostics,
                    "server_prompt_policy_managed": True,
                    "server_system_default_prefix_chars": len(system_default_prefix),
                    "server_system_default_prefix_recommended": system_default_prefix == SYSTEM_DEFAULT_PREFIX,
                    "prompt_config_revision": int(store.config.get("revision") or 1),
                },
            }
            request_id = str(payload.get("request_id") or "").strip()
            prompt = str(payload.get("prompt") or "")
            if request_id and prompt and bool(store.config.get("audit_final_prompt", True)):
                await telemetry.upsert(
                    {
                        "request_id": request_id,
                        "final_prompt": prompt,
                        "final_prompt_chars": len(prompt),
                        "prompt_config_revision": int(store.config.get("revision") or 1),
                        "prompt_redaction_enabled": bool(store.config.get("redaction_enabled")),
                    }
                )
        await base_send(client_id, payload)

    registry.send = send_with_prompt_audit

    # Keep full prompts out of the paginated request-list payload. The detail API
    # remains authoritative and is fetched only when the administrator clicks the
    # new 提示词 column. TelemetryStore.query is intentionally synchronous and the
    # /api/admin/requests endpoint spreads its returned mapping immediately, so the
    # decorator must preserve that synchronous contract.
    base_query = telemetry.query

    def query_without_full_prompt(*args, **kwargs):
        result = base_query(*args, **kwargs)
        rows = result.get("data") if isinstance(result, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row["final_prompt_available"] = bool(row.get("final_prompt"))
                row.pop("final_prompt", None)
        return result

    telemetry.query = query_without_full_prompt

    @app.get("/api/admin/prompt-config")
    async def get_prompt_config() -> dict[str, Any]:
        return {"ok": True, "config": store.snapshot(), "patch": PATCH_ID}

    @app.put("/api/admin/prompt-config")
    async def put_prompt_config(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
            config = store.save(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Unable to persist prompt configuration: {exc}") from exc
        return {"ok": True, "config": config, "patch": PATCH_ID}

    @app.post("/api/admin/prompt-config/preview")
    async def preview_prompt_config(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        sample = str(payload.get("prompt") or "") if isinstance(payload, dict) else ""
        final, meta = store.apply(sample)
        return {"ok": True, "input": sample, "output": final, "meta": meta, "patch": PATCH_ID}

    @app.get(ASSET_PATH)
    async def prompt_config_asset() -> Response:
        path = Path(__file__).with_name("admin_prompt_config_v72.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(ASSET_V75_PATH)
    async def prompt_config_v75_asset() -> Response:
        path = Path(__file__).with_name("admin_prompt_config_v75.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    # app.main imports the admin_response function, not ADMIN_HTML itself. Mutate
    # the canonical app.admin module so every future /admin or /developers response
    # produced by admin_response() includes the prompt assets and theme-safe fields.
    from . import admin as admin_module

    if PROMPT_THEME_MARKER not in admin_module.ADMIN_HTML:
        admin_module.ADMIN_HTML = admin_module.ADMIN_HTML.replace("</head>", PROMPT_THEME_V74 + "</head>")

    marker = f'<script src="{ASSET_PATH}"></script>'
    if marker not in admin_module.ADMIN_HTML:
        admin_module.ADMIN_HTML = admin_module.ADMIN_HTML.replace("</body>", marker + "</body>")

    marker_v75 = f'<script src="{ASSET_V75_PATH}"></script>'
    if marker_v75 not in admin_module.ADMIN_HTML:
        admin_module.ADMIN_HTML = admin_module.ADMIN_HTML.replace("</body>", marker_v75 + "</body>")

    app.state.prompt_config_v72_installed = True
    return app
