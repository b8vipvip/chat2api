from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.20.3"
TEXT_MODELS = {"gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"}


def _normalize_reasoning(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {"low", "minimal", "none", "fast", "instant", "极速"}:
        return "instant"
    if raw in {"medium", "中", "中等"}:
        return "medium"
    if raw in {"high", "xhigh", "高"}:
        return "high"
    return None


def _row_combo(row: dict[str, Any]) -> tuple[str, str | None] | None:
    model = str(row.get("requested_model") or "").strip().lower()
    if model not in TEXT_MODELS:
        return None
    if model == "gpt-5.5-mini":
        return model, None

    diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
    reasoning = None
    for value in (
        row.get("reasoning_effort"),
        row.get("reasoning_level"),
        diagnostics.get("requested_reasoning"),
        diagnostics.get("logical_requested_reasoning"),
        diagnostics.get("effective_reasoning"),
        diagnostics.get("actual_reasoning"),
    ):
        reasoning = _normalize_reasoning(value)
        if reasoning:
            break
    return model, reasoning


def _top_affinity(rows: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str | None]] = Counter()
    newest_index: dict[tuple[str, str | None], int] = {}
    # TelemetryStore.recent() is newest-first, so the smallest index is the
    # most recent occurrence and is a useful tie-break when frequencies match.
    for index, row in enumerate(rows):
        combo = _row_combo(row)
        if not combo:
            continue
        counts[combo] += 1
        newest_index.setdefault(combo, index)

    ranked = sorted(
        counts,
        key=lambda combo: (-counts[combo], newest_index.get(combo, 10**9), combo[0], combo[1] or ""),
    )[: max(1, min(int(limit), 2))]
    return [
        {
            "rank": index + 1,
            "model": model,
            "reasoning": reasoning,
            "count": counts[(model, reasoning)],
            "key": f"{model}:{reasoning or 'auto'}",
        }
        for index, (model, reasoning) in enumerate(ranked)
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


def install_v20_3_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    telemetry = app.state.telemetry
    app.version = PATCH_VERSION

    @app.get("/api/extensions/model-affinity")
    async def extension_model_affinity(
        x_extension_client_id: str | None = Header(default=None),
        x_extension_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        client_id = str(x_extension_client_id or "").strip()
        token = str(x_extension_token or "").strip()
        if not client_id or not token or not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension credentials")

        rows = telemetry.recent(200)
        presets = _top_affinity(rows, 2)
        return {
            "object": "extension.model_affinity",
            "interval_seconds": 600,
            "history_limit": 200,
            "sample_size": sum(int(item["count"]) for item in presets),
            "presets": presets,
            "version": PATCH_VERSION,
        }

    @app.get("/assets/chat2api-v20-3.js")
    async def admin_v20_3_js() -> Response:
        path = Path(__file__).with_name("admin_v20_3.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def v20_3_version_metadata(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview"}
            or path.startswith("/api/admin/")
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["model_affinity_warm_pool"] = True
                        capabilities["model_affinity_interval_seconds"] = 600
                        capabilities["model_affinity_slots"] = 2
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v20-3.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
