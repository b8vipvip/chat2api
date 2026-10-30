from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .admin import admin_response
from .api_keys import ApiKeyStore, ApiPrincipal, UNSET
from .broker import RequestBroker, RequestState
from .chat_media_deadline_v171 import chat_timeout_window, worker_chat_timeout, long_form_text_requested
from .chat_progress_stream_v196 import status_from_diagnostics, progress_is_real
from .chat_stream_delivery_v194 import StagedStreamText, final_text_chunks, sse_heartbeat_interval
from .stream_detached_terminal_v219 import settle_detached_stream
from .config import Settings, get_settings
from .file_store import FileStore
from .media_archive_v173 import MediaArchive
from .generated_files_v196 import GeneratedFileArchive
from .generated_file_staging_v238 import GeneratedFileStaging
from .generated_file_deferred_v247 import DeferredGeneratedFiles, FileStillProcessing, FileTransferFailed
from .rich_content_v196 import manifest as rich_content_manifest
from .native_content_v1 import NativeContentStore
from .media_public_origin_v187 import media_base_for_request
from .response_history_media_v188 import history_safe_response_text
from .responses_multimodal_input_v183 import attachment_specs as canonical_attachment_specs
from .media_handoff import StreamMediaMarkerGate
from .models import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ChatCompletionRequest,
    ClientSummary,
    ExtensionRegistration,
    ExtensionRegistrationResult,
    FileUploadRequest,
    ImageGenerationRequest,
    TestRunCreate,
)
from .prompting import build_prompt
from .registry import ClientRegistry
from .telemetry import TelemetryStore, parse_request_since
from .runtime_contract import SERVER_RUNTIME_VERSION
from .runtime_code_identity import runtime_code_sha
from .worker_probe_store import WorkerProbeStore
from .test_runs import TestRunStore
from .playground_truth_v170 import reconcile_playground_runs, compact_playground_outcome, present_playground_run
from .token_usage import usage_for

logger = logging.getLogger("chat2api")
APP_VERSION = "0.5.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    registry = ClientRegistry(config.data_dir)
    broker = RequestBroker()
    # The request origin is the actual dispatch socket; disconnection of an
    # old socket must not terminate requests already sent on its replacement.
    registry.request_broker = broker
    telemetry = TelemetryStore(config.data_dir)
    worker_probes = WorkerProbeStore(config.data_dir)
    native_content = NativeContentStore(config.data_dir)
    api_keys = ApiKeyStore(config.data_dir, config.api_key)
    telemetry.usage_accounting = api_keys.account_completed_usage
    file_store = FileStore(config.data_dir)
    media_archive = MediaArchive(config.data_dir, config.api_key)
    generated_files = GeneratedFileArchive(media_archive)
    generated_staging = GeneratedFileStaging(media_archive.root)
    generated_files.staging = generated_staging
    deferred_generated_files = DeferredGeneratedFiles(generated_files)
    test_runs = TestRunStore(config.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await registry.load()
        await telemetry.load()
        await api_keys.load()
        await file_store.load()
        await test_runs.load()
        recovered_runs = await test_runs.recover_interrupted()
        if recovered_runs:
            logger.warning("Recovered %s interrupted playground test run(s) as stalled", len(recovered_runs))
        if config.api_key in {"", "change-me"}:
            logger.warning("CHAT2API_API_KEY is using an unsafe default. Change it before remote exposure.")
        if config.pairing_code in {"", "change-me-pairing"}:
            logger.warning("CHAT2API_PAIRING_CODE is using an unsafe default. Change it before remote exposure.")
        yield

    app = FastAPI(
        title="chat2api",
        version=APP_VERSION,
        description="OpenAI-compatible browser bridge for text, files, vision and ChatGPT Images.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.registry = registry
    app.state.broker = broker
    app.state.telemetry = telemetry
    app.state.worker_probes = worker_probes
    app.state.native_content = native_content
    app.state.api_keys = api_keys
    app.state.file_store = file_store
    app.state.media_archive = media_archive
    app.state.generated_files = generated_files
    app.state.generated_staging = generated_staging
    app.state.test_runs = test_runs
    # Keep detached-stream supervisors alive until their Broker terminal is persisted.
    app.state.detached_sse_tasks = set()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=config.origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def supplied_token(authorization: str | None, x_api_key: str | None) -> str:
        supplied = (x_api_key or "").strip()
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return supplied

    def master_principal() -> ApiPrincipal:
        return ApiPrincipal(
            key_id="master",
            name="CHAT2API_API_KEY",
            kind="master",
            scopes=("admin", "chat", "models", "files", "images"),
        )

    async def require_api_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        principal = await api_keys.authenticate(supplied)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid or disabled API key")
        return principal

    async def require_admin_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing administrator API key")
        if config.api_key and secrets.compare_digest(supplied, config.api_key):
            return master_principal()
        if await api_keys.authenticate(supplied):
            raise HTTPException(status_code=403, detail="Managed API keys cannot access administrator endpoints")
        raise HTTPException(status_code=401, detail="Invalid administrator API key")

    def probe_worker_id(client_id: str) -> str:
        client = registry.clients.get(client_id)
        metadata = (client.metadata if client and isinstance(client.metadata, dict) else {})
        return str(metadata.get("linux_worker_id") or metadata.get("worker_id") or client_id)

    def probe_policy(client_id: str) -> dict[str, Any]:
        return worker_probes.effective(probe_worker_id(client_id))

    def require_scope(principal: ApiPrincipal, scope: str) -> None:
        if principal.kind != "master" and scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"API key does not have {scope!r} permission")

    @app.get("/api/admin/media-storage", dependencies=[Depends(require_admin_key)])
    async def media_storage_settings() -> dict[str, object]:
        return media_archive.public_config()

    @app.put("/api/admin/media-storage", dependencies=[Depends(require_admin_key)])
    async def configure_media_storage(payload: dict[str, Any]) -> dict[str, object]:
        try:
            return media_archive.configure(payload)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/admin/media-storage/test", dependencies=[Depends(require_admin_key)])
    async def media_storage_test() -> dict[str, object]:
        try:
            return await asyncio.to_thread(media_archive.test_backend)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/api/admin/media-storage/backfill-thumbnails", dependencies=[Depends(require_admin_key)])
    async def backfill_media_thumbnails(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        # Explicit bounded maintenance; does not alter originals, owners or
        # bearer links. A large archive can be paginated via next_offset.
        return await asyncio.to_thread(media_archive.repair_thumbnails, limit, offset)

    @app.get("/api/admin/media-storage/files", dependencies=[Depends(require_admin_key)])
    async def media_storage_files(limit: int = Query(default=80, ge=1, le=200)) -> dict[str, object]:
        return {"data": media_archive.list_items(limit)}

    @app.delete("/api/admin/media-storage/files/{media_id}", dependencies=[Depends(require_admin_key)])
    async def delete_archived_media(media_id: str) -> dict[str, bool]:
        try:
            deleted = await asyncio.to_thread(media_archive.delete, media_id)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=503, detail="Cannot remove media from the storage provider") from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Unknown media")
        return {"deleted": True}

    @app.get("/v1/media/{media_id}")
    async def owned_media_metadata(
        media_id: str, request: Request, principal: ApiPrincipal = Depends(require_api_key),
    ) -> Response:
        # This is a chat2api extension endpoint, not an OpenAI standard resource.
        # Require an image/chat-capable key and resolve the owner before returning
        # bearer-capability asset URLs. Never expose the storage provider key.
        if principal.kind != "master" and not {"chat", "images"}.intersection(principal.scopes):
            raise HTTPException(status_code=403, detail="API key lacks media permission")
        try:
            item = await asyncio.to_thread(
                media_archive.describe, media_id, principal.key_id, admin=principal.kind == "master",
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown media") from None
        origin = media_archive._config().get("public_base_url") or media_base_for_request(request)
        asset = f"{origin}/api/media/{item['id']}/{item.pop('token')}"
        return JSONResponse({**item, "object": "chat2api.media", "url": asset,
                             "thumbnail_url": asset + "/thumbnail"},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/media/{media_id}/{access_token}")
    @app.get("/api/media/{media_id}/{access_token}/{variant}")
    async def media_download(media_id: str, access_token: str, variant: str = "") -> Response:
        if variant not in {"", "original", "thumbnail"}:
            raise HTTPException(status_code=404, detail="Unknown variant")
        try:
            payload, mime = await asyncio.to_thread(media_archive.load, media_id, access_token, variant == "thumbnail")
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Unknown media") from None
        return Response(payload, media_type=mime, headers={
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Content-Disposition": "inline" if mime.startswith("image/") else "attachment",
            "Cache-Control": "private, max-age=86400",
        })


    @app.get("/api/admin/media-storage/generated-files", dependencies=[Depends(require_admin_key)])
    async def generated_file_inventory(limit: int = Query(default=80, ge=1, le=200)) -> dict[str, object]:
        return {"data": generated_files.list_items(limit)}

    @app.delete("/api/admin/media-storage/generated-files/{file_id}", dependencies=[Depends(require_admin_key)])
    async def delete_generated_file(file_id: str) -> dict[str, bool]:
        try:
            deleted = await asyncio.to_thread(generated_files.delete, file_id)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=503, detail="Cannot remove generated file from storage") from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Unknown generated file")
        return {"deleted": True}

    @app.get("/v1/generated-files/{file_id}")
    async def generated_file_metadata(
        file_id: str, request: Request, principal: ApiPrincipal = Depends(require_api_key),
    ) -> Response:
        if principal.kind != "master" and "chat" not in principal.scopes:
            raise HTTPException(status_code=403, detail="API key lacks chat permission")
        try:
            record = await asyncio.to_thread(
                generated_files.describe, file_id, principal.key_id,
                admin=principal.kind == "master",
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        origin = media_archive._config().get("public_base_url") or media_base_for_request(request)
        url = f"{origin}/api/generated-files/{record['id']}/{record.pop('token')}"
        return JSONResponse({**record, "object": "chat2api.generated_file", "url": url},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/generated-files/pending/{file_id}/{access_token}")
    async def download_deferred_generated_file(file_id: str, access_token: str) -> Response:
        try:
            data, mime, filename = await asyncio.to_thread(
                deferred_generated_files.read, file_id, access_token)
        except FileStillProcessing:
            return JSONResponse(
                {"status": "processing", "message": "文件正在转存，请稍后重试"},
                status_code=202,
                headers={"Cache-Control": "no-store", "Retry-After": "3",
                         "X-Chat2API-File-Status": "processing"},
            )
        except FileTransferFailed:
            return JSONResponse(
                {"status": "failed", "message": "文件转存未完成，请重新发起生成"},
                status_code=503,
                headers={"Cache-Control": "no-store",
                         "X-Chat2API-File-Status": "failed"},
            )
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        except (ValueError, OSError):
            raise HTTPException(status_code=503, detail="Generated file storage integrity unavailable") from None
        from urllib.parse import quote
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=300",
            "X-Chat2API-File-Status": "ready",
            "X-Chat2API-File-MIME": mime,
        })

    @app.get("/api/generated-files/pending/{file_id}/{access_token}/status")
    async def status_deferred_generated_file(file_id: str, access_token: str) -> Response:
        try:
            value = await asyncio.to_thread(
                deferred_generated_files.status, file_id, access_token)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @app.get("/api/generated-files/{file_id}/{access_token}")
    async def download_generated_file(file_id: str, access_token: str) -> Response:
        try:
            data, mime, filename = await asyncio.to_thread(generated_files.read, file_id, access_token)
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        except (ValueError, OSError):
            # A missing/corrupt S3/local object must never be a successful
            # delivery; do not expose backend paths or credentials to clients.
            raise HTTPException(status_code=503, detail="Generated file storage integrity unavailable") from None
        # Always force download, including HTML, code, SVG-like contents and PDF.
        # Never interpret arbitrary model artifacts as same-origin active code.
        from urllib.parse import quote
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=86400",
            "X-Chat2API-File-MIME": mime,
        })

    # Native rich content is an independent, authenticated presentation sidecar.
    # Standard /v1/chat/completions and Responses SSE events are never replaced.
    def native_owner(request_id: str, principal: ApiPrincipal) -> str:
        if principal.kind != "master" and "chat" not in principal.scopes:
            raise HTTPException(status_code=403, detail="API key lacks chat permission")
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Unknown request")
        owner = str(row.get("api_key_id") or "")
        if not owner or (principal.kind != "master" and owner != principal.key_id):
            raise HTTPException(status_code=404, detail="Unknown request")
        return owner

    def native_response_snapshot(request_id: str, owner: str) -> dict | None:
        snapshot = native_content.get(request_id, owner)
        if not snapshot:
            return None
        row = telemetry.get(request_id) or {}
        # Reuse the *same* archived media records as the canonical Markdown.
        # Never return a ChatGPT blob URL or a model-generated remote URL.
        assets = []
        for media in (row.get("response_media") or [])[:40]:
            if not isinstance(media, dict):
                continue
            uri = str(media.get("url") or media.get("owned_url") or "")
            if re.fullmatch(
                r"https://[^/?#]+(?::[0-9]{1,5})?/api/media/[a-zA-Z0-9_-]{6,100}/[a-zA-Z0-9_-]{8,150}",
                uri,
            ):
                assets.append(uri)
        return {**snapshot, "assets": assets}

    @app.get("/v1/native-content/{request_id}")
    async def read_native_content(
        request_id: str, principal: ApiPrincipal = Depends(require_api_key),
    ) -> Response:
        owner = native_owner(request_id, principal)
        try:
            snapshot = native_response_snapshot(request_id, owner)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No native snapshot")
        return JSONResponse(snapshot, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

    @app.get("/v1/native-content/{request_id}/events")
    async def stream_native_content(
        request_id: str, principal: ApiPrincipal = Depends(require_api_key),
    ) -> StreamingResponse:
        owner = native_owner(request_id, principal)

        async def changes() -> AsyncIterator[str]:
            last = 0
            # The sidecar may arrive after the client subscribes. No terminal
            # authority or worker lease is owned by this stream.
            for tick in range(3600):
                try:
                    current = native_response_snapshot(request_id, owner)
                    if current and current["revision"] > last:
                        last = current["revision"]
                        yield "event: native.snapshot\n" + "data: " + json.dumps(current, ensure_ascii=False) + "\n\n"
                    row = telemetry.get(request_id) or {}
                    if row.get("status") in {"completed", "error", "cancelled", "stalled"}:
                        yield "event: native.end\ndata: {}\n\n"
                        return
                except ValueError:
                    return
                if tick % 40 == 0:
                    yield ": native keepalive\n\n"
                await asyncio.sleep(0.25)
        return StreamingResponse(changes(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/admin/worker-tools/catalog", dependencies=[Depends(require_admin_key)])
    async def worker_probe_catalog() -> dict[str, Any]:
        return {"tools": worker_probes.catalog(), "schema_version": 1}

    @app.get("/api/admin/worker-tools/policies", dependencies=[Depends(require_admin_key)])
    async def worker_probe_policies(scope_id: str = "global") -> dict[str, Any]:
        try:
            policy = worker_probes.policy(scope_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        workers = [
            {"client_id": item.client_id,
             "worker_id": probe_worker_id(item.client_id),
             "name": item.name,
             "online": item.client_id in registry.online_client_ids(),
             "extension_version": (item.metadata or {}).get("extension_version")}
            for item in registry.clients.values()
        ]
        return {"policy": policy,
                "effective_policy": worker_probes.effective(scope_id[7:]) if scope_id.startswith("worker:") else None,
                "workers": workers}

    @app.put("/api/admin/worker-tools/policies/{scope_id}", dependencies=[Depends(require_admin_key)])
    async def worker_probe_update_policy(scope_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            revision = body.get("expected_revision")
            if type(revision) is not int:
                raise ValueError("expected_revision must be an integer")
            return await worker_probes.put_policy(scope_id, expected_revision=revision, tools=body.get("tools"))
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/admin/worker-tools/runs", dependencies=[Depends(require_admin_key)])
    async def worker_probe_runs(limit: int = 40) -> dict[str, Any]:
        return {"data": worker_probes.runs(limit)}

    @app.get("/api/admin/requests/{request_id}/probes", dependencies=[Depends(require_admin_key)])
    async def request_probe_events(request_id: str) -> dict[str, Any]:
        if not telemetry.get(request_id):
            raise HTTPException(status_code=404, detail="Unknown request")
        try:
            return {"summary": worker_probes.summary(request_id),
                    "events": worker_probes.events(request_id)}
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/admin/requests/{request_id}/probes/download", dependencies=[Depends(require_admin_key)])
    async def request_probe_download(request_id: str) -> Response:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Unknown request")
        try:
            archive = worker_probes.archive(request_id, row)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(content=archive, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="request-probes-{request_id}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "chat2api",
            "status": "ok",
            "docs": "/docs",
            "developers": "/developers",
            "admin": "/admin",
            "version": APP_VERSION,
        }

    @app.get("/healthz")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "online_extensions": len(registry.online_client_ids()),
            "version": APP_VERSION,
        }

    @app.get("/admin")
    async def admin():
        return admin_response("overview")

    @app.get("/developers")
    async def developers():
        return admin_response("docs")

    def managed_keys_with_stats() -> list[dict[str, Any]]:
        stats = telemetry.key_stats()
        rows: list[dict[str, Any]] = [
            {
                "key_id": "master",
                "name": "CHAT2API_API_KEY",
                "kind": "master",
                "managed": False,
                "prefix": "server .env",
                "enabled": True,
                "configured_enabled": True,
                "expired": False,
                "revoked_at": None,
                "created_at": None,
                "expires_at": None,
                "last_used_at": None,
                "secret_recoverable": False,
                "scopes": ["admin", "chat", "models", "files", "images"],
                **stats.get("master", {}),
            }
        ]
        for item in api_keys.list_public():
            rows.append({**item, "kind": "managed", "managed": True, **stats.get(item["key_id"], {})})
        return rows

    @app.get("/api/admin/overview", dependencies=[Depends(require_admin_key)])
    async def admin_overview() -> dict[str, object]:
        return {
            "version": APP_VERSION,
            "health": {"online_extensions": len(registry.online_client_ids())},
            "clients": registry.summaries(),
            "models": registry.model_catalog(online_only=True),
            "api_keys": managed_keys_with_stats(),
            "telemetry": telemetry.summary(),
            "recent_requests": telemetry.recent(20),
            "recent_tests": test_runs.recent(10),
            "capabilities": {
                "text": True,
                "vision": True,
                "file_understanding": True,
                "image_generation": True,
                "voice_generation": False,
                "voice_conversation": False,
                "desktop_agent": False,
            },
        }

    @app.get("/api/admin/keys", dependencies=[Depends(require_admin_key)])
    async def admin_keys() -> dict[str, object]:
        return {"data": managed_keys_with_stats()}

    @app.post("/api/admin/keys", dependencies=[Depends(require_admin_key)])
    async def create_api_key(body: ApiKeyCreate) -> dict[str, object]:
        expires_at = None
        if body.expires_in_days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
        item, token = await api_keys.create(body.name, expires_at)
        if body.token_quota is not None:
            item = await api_keys.update(item["key_id"], token_quota=body.token_quota)
        return {"key": {**item, "kind": "managed", "managed": True}, "token": token}

    @app.get("/api/admin/keys/{key_id}/secret", dependencies=[Depends(require_admin_key)])
    async def reveal_api_key(key_id: str) -> dict[str, str]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key is only available in the server .env")
        try:
            return {"key_id": key_id, "token": api_keys.reveal(key_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def update_api_key(key_id: str, body: ApiKeyUpdate) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key is managed through CHAT2API_API_KEY in .env")
        try:
            item = await api_keys.update(key_id, name=body.name, enabled=body.enabled,
                                         token_quota=body.token_quota if "token_quota" in body.model_fields_set else UNSET)
            return {"key": {**item, "kind": "managed", "managed": True}}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/api/admin/keys/{key_id}", dependencies=[Depends(require_admin_key)])
    async def revoke_api_key(key_id: str) -> dict[str, object]:
        if key_id == "master":
            raise HTTPException(status_code=400, detail="The master key cannot be revoked from the web console")
        try:
            item = await api_keys.revoke(key_id)
            return {"key": {**item, "kind": "managed", "managed": True}}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/admin/requests", dependencies=[Depends(require_admin_key)])
    async def admin_requests(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status_filter: str | None = Query(default=None, alias="status"),
        model: str | None = Query(default=None),
        key_id: str | None = Query(default=None),
        q: str | None = Query(default=None),
        since: str | None = Query(default=None, max_length=64),
        current_version_only: bool = Query(default=False),
    ) -> dict[str, object]:
        try:
            cutoff = parse_request_since(since)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        active_commit = runtime_code_sha() if current_version_only else ""
        if current_version_only and not active_commit:
            raise HTTPException(
                status_code=409,
                detail="Current running Git commit is unverified; cannot filter by current code revision",
            )
        result = telemetry.query(
            limit=limit, offset=offset, status=status_filter, model=model,
            key_id=key_id, q=q, since=cutoff,
            current_version_only=current_version_only,
            server_version=SERVER_RUNTIME_VERSION,
            server_commit=active_commit,
        )
        return {**result, "summary": telemetry.summary(),
                "current_version_commit": active_commit if current_version_only else None,
                # Echo the *applied*, normalized UTC cutoff so the UI can
                # reject stale/unfiltered responses, not silently show old rows.
                "applied_filters": {
                    "since": cutoff.isoformat() if cutoff is not None else None,
                    "current_version_only": current_version_only,
                    "q": q, "status": status_filter, "model": model,
                }}

    @app.get("/api/admin/requests/{request_id}", dependencies=[Depends(require_admin_key)])
    async def admin_request_detail(request_id: str) -> dict[str, object]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        return row

    @app.get("/api/admin/tests", dependencies=[Depends(require_admin_key)])
    async def admin_tests(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
        await reconcile_playground_runs(test_runs, telemetry, limit=min(200, limit))
        return {"data": [present_playground_run(row) for row in test_runs.recent(limit)]}

    @app.get("/api/admin/tests/{run_id}", dependencies=[Depends(require_admin_key)])
    async def admin_test_detail(run_id: str) -> dict[str, object]:
        await reconcile_playground_runs(test_runs, telemetry, limit=200)
        row = test_runs.get(run_id)
        if not row:
            raise HTTPException(status_code=404, detail="Test report not found")
        return present_playground_run(row)

    @app.get("/api/admin/playground/requests/{request_id}/outcome", dependencies=[Depends(require_admin_key)])
    async def admin_playground_request_outcome(
        request_id: str, full_text: bool = Query(default=False),
    ) -> dict[str, object]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        return await compact_playground_outcome(
            row, telemetry, file_store, include_full_text=full_text,
        )

    @app.post("/api/admin/tests", dependencies=[Depends(require_admin_key)])
    async def save_test_report(body: TestRunCreate) -> dict[str, object]:
        row = await test_runs.upsert(body.model_dump())
        return {"report": row}

    @app.post("/api/extensions/register", response_model=ExtensionRegistrationResult)
    async def register_extension(body: ExtensionRegistration, x_pairing_code: str | None = Header(default=None)) -> ExtensionRegistrationResult:
        if not x_pairing_code or not secrets.compare_digest(x_pairing_code, config.pairing_code):
            raise HTTPException(status_code=401, detail="Invalid pairing code")
        client_id, token = await registry.register(body.name, body.browser_name, body.version, body.metadata)
        return ExtensionRegistrationResult(client_id=client_id, token=token)

    @app.get("/api/clients", response_model=list[ClientSummary], dependencies=[Depends(require_admin_key)])
    async def clients() -> list[dict[str, object]]:
        return registry.summaries()

    @app.post("/v1/files")
    async def upload_file(body: FileUploadRequest, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        try:
            item = await file_store.create(
                filename=body.filename,
                data_base64=body.data_base64,
                mime_type=body.mime_type,
                owner_key_id=principal.key_id,
                purpose=body.purpose,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return item.public()

    @app.get("/v1/files/{file_id}")
    async def file_metadata(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        item = file_store.get(file_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="File belongs to another API key")
        return item.public()

    @app.delete("/v1/files/{file_id}")
    async def delete_file(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, bool]:
        require_scope(principal, "files")
        try:
            await file_store.delete(file_id, None if principal.kind == "master" else principal.key_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return {"deleted": True}

    @app.get("/api/extensions/files/{file_id}")
    async def extension_file(file_id: str, client_id: str = Query(...), token: str = Query(...)) -> Response:
        if not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension token")
        try:
            item, payload = file_store.read(file_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        return Response(
            payload,
            media_type=item.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{item.filename.replace(chr(34), "")}"',
                "X-Chat2API-Filename": item.filename,
                "Cache-Control": "no-store",
            },
        )

    @app.get("/v1/models")
    async def models(principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "models")
        return {"object": "list", "data": registry.model_catalog(online_only=True)}

    def resolve_client_now(requested: str | None) -> str:
        try:
            return registry.resolve_client(requested)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConnectionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    async def resolve_client_with_reconnect_grace(requested: str | None) -> str:
        # Only automatic text dispatch gets a short retry when all eligible
        # Workers are temporarily reconnecting. Explicit Worker selections and
        # model-incompatibility errors still fail without rerouting.
        deadline = time.monotonic() + 8.0
        while True:
            try:
                return resolve_client_now(requested)
            except HTTPException as exc:
                reason = str(exc.detail or "")
                temporary = (
                    exc.status_code == 503 and not requested
                    and (
                        "No Chrome extension is online" in reason
                        or "No ChatGPT-ready Chrome extension" in reason
                        or "No compatible Chrome extension is available" in reason
                    )
                )
                if not temporary or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.5)

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    def accepted_request_id(supplied: str | None, prefix: str) -> str:
        value = str(supplied or "").strip()
        if not value:
            return prefix + uuid.uuid4().hex
        if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{8,120}", value):
            raise HTTPException(status_code=400, detail=f"Invalid {prefix} request ID")
        if value in broker.requests or telemetry.get(value):
            raise HTTPException(status_code=409, detail=f"Duplicate request_id: {value}")
        return value

    def standard_usage(prompt: str, completion: str) -> tuple[dict[str, int], dict[str, object]]:
        usage = usage_for(prompt, completion)
        return (
            {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens},
            {"estimated": usage.estimated, "estimator": usage.estimator},
        )

    def attachment_specs(file_ids: list[str], principal: ApiPrincipal) -> list[dict[str, object]]:
        return canonical_attachment_specs(file_store, file_ids, principal)

    async def inline_image_files(body: ChatCompletionRequest, principal: ApiPrincipal) -> list[str]:
        result: list[str] = []
        sequence = 0
        for message in body.messages:
            if not isinstance(message.content, list):
                continue
            for part in message.content:
                if not isinstance(part, dict) or part.get("type") not in {"image_url", "input_image"}:
                    continue
                value = part.get("image_url")
                if isinstance(value, dict):
                    value = value.get("url")
                if not isinstance(value, str) or not value:
                    continue
                if value.startswith("file_"):
                    result.append(value)
                    continue
                if not value.startswith("data:"):
                    raise HTTPException(
                        status_code=400,
                        detail="Remote image URLs are not fetched for security. Use a base64 data URL or upload via POST /v1/files.",
                    )
                sequence += 1
                try:
                    item = await file_store.create(
                        filename=f"inline-image-{sequence}.png",
                        data_base64=value,
                        mime_type=None,
                        owner_key_id=principal.key_id,
                        purpose="vision",
                    )
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                result.append(item.file_id)
        return result

    def chat2api_meta(state: RequestState, token_meta: dict[str, object], principal: ApiPrincipal) -> dict[str, object]:
        return {
            "client_id": state.client_id,
            "request_id": state.request_id,
            "api_key": {"key_id": principal.key_id, "name": principal.name, "kind": principal.kind},
            "diagnostics": dict(state.diagnostics),
            "timings": state.timings(),
            "token_usage": token_meta,
        }

    async def archive_verified_terminal(state: RequestState, text: str, key_id: str,
                                       request_id: str, base_url: str):
        # The Worker/Broker alone decides completion. This is a bounded
        # *post-terminal delivery step*: if archival stalls, persist an actual
        # API delivery failure instead of leaving the log in running forever.
        state.diagnostics["server_finalization_stage"] = "archive-started"
        state.diagnostics["server_finalization_started_at"] = time.time()
        await telemetry.upsert({
            "request_id": request_id,
            "diagnostics": dict(state.diagnostics),
        })
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    archive_final_markdown, text, key_id, request_id, base_url
                ),
                timeout=180.0,
            )
        except asyncio.TimeoutError as error:
            state.diagnostics["server_finalization_stage"] = "archive-timeout"
            state.diagnostics["server_finalization_elapsed_ms"] = round(
                (time.time() - state.diagnostics["server_finalization_started_at"]) * 1000
            )
            raise ValueError("Post-terminal media archival exceeded 180 seconds") from error
        except Exception:
            state.diagnostics["server_finalization_stage"] = "archive-error"
            raise
        state.diagnostics["server_finalization_stage"] = "archive-finished"
        state.diagnostics["server_finalization_elapsed_ms"] = round(
            (time.time() - state.diagnostics["server_finalization_started_at"]) * 1000
        )
        return result

    async def record_request(
        state: RequestState,
        requested_model: str,
        prompt: str,
        status_value: str,
        principal: ApiPrincipal,
        *,
        stream: bool,
        prompt_mode: str,
        response_id: str,
        final_text: str = "",
        error: str | None = None,
        request_type: str = "text",
        attachments_count: int = 0,
        media_items: list[dict[str, object]] | None = None,
    ) -> tuple[dict[str, int], dict[str, object]]:
        usage, token_meta = standard_usage(prompt, final_text)
        state.diagnostics["server_finalization_stage"] = "request-row-finalized"
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": len(final_text),
                "response_media": media_items or [],
                "response_media_count": len(media_items or []),
                # Persist only the final post-archive owned-media URL, never the
                # browser's several-megabyte raw Base64 transport payload.
                **({"response_text": history_safe_response_text(final_text),
                    "response_text_available": bool(final_text)}
                   if final_text or status_value == "completed" else {}),
                "status": status_value,
                "probe_summary": worker_probes.summary(state.request_id),
                "probe_capture_status": worker_probes.summary(state.request_id)["capture_status"],
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": dict(state.diagnostics),
                "error": error,
            }
        )
        # The Broker has now persisted its authoritative terminal; completed
        # chunks were already archived and all other partial staging bytes
        # are no longer eligible to be attached to a future request.
        await asyncio.to_thread(generated_staging.drop_request, state.request_id)
        return usage, token_meta

    async def record_request_queued(
        *, request_id: str, response_id: str, requested_model: str, prompt: str,
        principal: ApiPrincipal, stream: bool, prompt_mode: str,
        request_type: str, attachments_count: int,
    ) -> None:
        """Persist request visibility before Worker selection/capacity admission.

        A caller waiting for the Free-account queue must be searchable by its
        original request ID without pretending ChatGPT has started generating.
        Only the existing terminal path may mark a dispatched request complete.
        """
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.upsert({
            "request_id": request_id,
            "response_id": response_id,
            "client_id": None,
            "api_key_id": principal.key_id,
            "api_key_name": principal.name,
            "auth_kind": principal.kind,
            "requested_model": requested_model,
            "request_type": request_type,
            "attachments_count": attachments_count,
            "stream": stream,
            "prompt_mode": prompt_mode,
            "prompt_chars": len(prompt),
            "completion_chars": 0,
            "status": "queued",
            "usage": {**usage, **token_meta},
            "timings": {},
            "diagnostics": {"request_admission_stage": "selecting-worker"},
            "error": None,
        })
        logger.info(
            "API request queued before Worker selection request_id=%s model=%s key=%s",
            request_id, requested_model, principal.key_id,
            extra={"request_id": request_id, "admission_stage": "selecting-worker"},
        )

    async def record_request_running(
        state: RequestState,
        requested_model: str,
        prompt: str,
        principal: ApiPrincipal,
        *,
        stream: bool,
        prompt_mode: str,
        response_id: str,
        request_type: str,
        attachments_count: int,
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": "running",
                "usage": {**usage, **token_meta},
                "timings": state.timings(),
                "diagnostics": dict(state.diagnostics),
                "error": None,
            }
        )

    def failure_status(state: RequestState, event_type: str = "", message: str = "") -> str:
        diagnostics = state.diagnostics or {}
        if event_type == "chat.cancelled" or diagnostics.get("playground_cancelled") is True:
            return "cancelled"
        if any(
            diagnostics.get(name) is True
            for name in (
                "extension_dispatch_watchdog_fired",
                "extension_submit_watchdog_fired",
                "post_submit_generation_watchdog_fired",
                "generation_activity_watchdog_fired",
                "absolute_request_lease_watchdog_fired",
            )
        ):
            return "stalled"
        if "cancel" in str(message or "").lower():
            return "cancelled"
        return "error"

    async def record_early_error(
        *, request_id: str, response_id: str, requested_model: str, prompt: str,
        principal: ApiPrincipal, stream: bool, prompt_mode: str, started_mono: float,
        error: str, request_type: str = "text", attachments_count: int = 0,
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        existing = telemetry.get(request_id) or {}
        diagnostics = dict(existing.get("diagnostics") or {})
        if diagnostics.get("request_admission_stage"):
            diagnostics["request_admission_failure_stage"] = diagnostics["request_admission_stage"]
            diagnostics["request_admission_stage"] = "failed-before-worker-dispatch"
            diagnostics["request_admission_wait_ms"] = round(
                (time.perf_counter() - started_mono) * 1000, 1
            )
        await telemetry.upsert(
            {
                "request_id": request_id,
                "response_id": response_id,
                "client_id": existing.get("client_id"),
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": "error",
                "usage": {**usage, **token_meta},
                "timings": {"total_ms": round((time.perf_counter() - started_mono) * 1000, 1)},
                "diagnostics": diagnostics,
                "error": error,
            }
        )

    def chunk_payload(
        response_id: str, model: str, delta: dict[str, object], finish_reason: str | None = None,
        usage: dict[str, int] | None = None, meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            payload["usage"] = usage
        if meta is not None:
            payload["chat2api"] = meta
        return payload

    def chat_deadline_remaining(
        state: RequestState, started_at: float, timeout_seconds: int, automatic_media_extension: bool,
        long_text_requested: bool = False, stream: bool = False,
    ) -> float:
        # The broker retains sole terminal authority; the Worker may only
        # supply request-correlated media handoff evidence to adjust its deadline.
        effective = chat_timeout_window(
            timeout_seconds, automatic_extension=automatic_media_extension,
            media_handoff_proven=state.diagnostics.get("media_handoff_marker") is True,
            long_text_requested=long_text_requested,
            stream_progress_proven=stream and progress_is_real(state),
            stream=stream,
        )
        if effective > timeout_seconds:
            if state.diagnostics.get("media_handoff_marker") is True:
                state.diagnostics["media_timeout_extended_seconds"] = effective
                state.diagnostics["media_timeout_extension_authority"] = "server-chat-deadline-v171"
            elif stream and progress_is_real(state):
                state.diagnostics["stream_progress_timeout_extended_seconds"] = effective
                state.diagnostics["stream_progress_timeout_authority"] = "server-chat-deadline-v196"
            elif long_text_requested:
                state.diagnostics["long_text_timeout_extended_seconds"] = effective
                state.diagnostics["long_text_timeout_extension_authority"] = "server-chat-deadline-v171"
        state.diagnostics["server_effective_timeout_seconds"] = effective
        worker_lease = state.diagnostics.get("request_timeout_seconds")
        if worker_lease is not None:
            state.diagnostics["server_worker_lease_aligned"] = (
                abs(float(worker_lease) - float(effective)) < 0.01
            )
            state.diagnostics["server_worker_lease_diff_seconds"] = round(
                float(worker_lease) - float(effective), 3
            )
        state.diagnostics["deadline_policy_authority"] = "server-worker-one-lease-v222"
        return started_at + effective - asyncio.get_running_loop().time()


    def archive_final_markdown(text: str, owner: str, request_id: str, base_url: str):
        # Server-side archival is a presentation boundary, not a terminal arbiter.
        # Images remain handled by their established thumbnail store. Generated
        # file handoffs reuse that store's configured local/S3 backend.
        text, media = media_archive.rewrite_markdown(text, owner, request_id, base_url)
        # Verified bytes are staged before the API text is delivered. Local/S3
        # transfer proceeds independently and the bearer URL reports 202 until
        # real archive bytes have passed the existing signature/hash checks.
        text, files = deferred_generated_files.rewrite(text, owner, request_id, base_url)
        if "data:image/" in text or "![📎 " in text:
            raise ValueError("Incomplete generated attachment payload")
        return text, media, files

    async def stream_request(
        request: Request, state: RequestState, response_id: str, model: str, timeout_seconds: int,
        prompt: str, principal: ApiPrincipal, prompt_mode: str, attachments_count: int,
        automatic_media_extension: bool = False, long_text_requested: bool = False,
    ) -> AsyncIterator[str]:
        yield "data: " + json.dumps(chunk_payload(response_id, model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"
        sent_text = ""
        started_at = asyncio.get_running_loop().time()
        next_heartbeat_at = started_at + sse_heartbeat_interval(0.0)
        state.diagnostics["api_stream_keepalive_policy"] = "1s-first-30s-5s-after"
        last_status_key = None
        last_reasoning_text = ""
        recorded = False
        request_type = "multimodal" if attachments_count else "text"
        # The presentation-only gate holds generated image syntax until the
        # broker's single terminal event has finished archival.
        raw_seen = ""
        staged_text = StagedStreamText()
        # Provisional browser snapshots are not authoritative assistant content.
        # They can diverge from the verified final DOM answer (TX03 2026-10-24).
        # Standard OpenAI SSE content is append-only; never emit a speculative
        # prefix that cannot be revoked on the wire.
        media_gate = StreamMediaMarkerGate()

        try:
            while True:
                # SSE comments are invisible to Chat Completions clients.
                # A long Worker generation can have no text chunks for minutes;
                # keep intermediaries/SDK body-timeouts from aborting before
                # the authoritative Worker terminal arrives.
                now = asyncio.get_running_loop().time()
                if now >= next_heartbeat_at:
                    next_heartbeat_at = now + sse_heartbeat_interval(now - started_at)
                    state.diagnostics["api_stream_keepalive_sent"] = (
                        int(state.diagnostics.get("api_stream_keepalive_sent") or 0) + 1
                    )
                    state.diagnostics.setdefault(
                        "api_stream_first_keepalive_ms", round((now - started_at) * 1000)
                    )
                    # Send a real data frame as well as an SSE comment: some
                    # OpenAI clients ignore comments but need visible activity.
                    current_status = status_from_diagnostics(state.diagnostics)
                    if current_status:
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, {"chat2api_status": {
                                **current_status, "elapsed_seconds": int(now - started_at),
                                "active": True,
                            }}), ensure_ascii=False,
                        ) + "\n\n"
                    # Before a Worker status exists, a valid empty OpenAI delta
                    # prevents SSE proxies/SDK readers from waiting without a
                    # data frame. No assistant content or terminal is inferred.
                    if not current_status:
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, {}), ensure_ascii=False
                        ) + "\n\n"
                    yield ": chat2api-keepalive\n\n"
                if await request.is_disconnected():
                    # The API transport may vanish while ChatGPT continues.
                    # Preserve the active Broker lease; detached supervisor
                    # settles only after request-v6's Worker terminal.
                    state.diagnostics["api_stream_close_cause"] = "http-disconnect-observed"
                    break
                remaining = chat_deadline_remaining(
                    state, started_at, timeout_seconds, automatic_media_extension,
                    long_text_requested, stream=True,
                )
                if remaining <= 0:
                    await registry.send(state.client_id, {"type": "chat.cancel", "request_id": state.request_id})
                    state.completed_mono = state.completed_mono or time.perf_counter()
                    await record_request(state, model, prompt, "error", principal, stream=True, prompt_mode=prompt_mode,
                                         response_id=response_id, final_text=sent_text, error="Timed out waiting for ChatGPT",
                                         request_type=request_type, attachments_count=attachments_count)
                    recorded = True
                    yield "data: " + json.dumps({"error": {"message": "Timed out waiting for ChatGPT", "type": "timeout_error"}}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    continue
                event_type = event.get("type")
                if event_type in {"chat.diagnostics", "chat.started"}:
                    observed = status_from_diagnostics(
                        event.get("diagnostics") if event_type == "chat.diagnostics" else state.diagnostics
                    )
                    if observed:
                        signature = (observed["text"], observed["kind"], observed["source"])
                        if signature != last_status_key:
                            last_status_key = signature
                            delta = {"chat2api_status": {**observed, "active": True}}
                            # Only source-verified reasoning synopsis may enter
                            # the optional reasoning lane; a spinner never does.
                            if observed["text"] != last_reasoning_text:
                                # mv3c/OpenAI clients already render the independent
                                # reasoning lane; the status is not assistant content.
                                # It will be collapsed when final content arrives.
                                delta["reasoning"] = observed["text"]
                                last_reasoning_text = observed["text"]
                            yield "data: " + json.dumps(
                                chunk_payload(response_id, model, delta), ensure_ascii=False
                            ) + "\n\n"
                    continue
                if event_type in {"chat.delta", "chat.snapshot"}:
                    if event_type == "chat.delta":
                        delta = str(event.get("delta") or "")
                        raw_seen += delta
                    else:
                        snapshot = str(event.get("text") or "")
                        delta = snapshot[len(raw_seen):] if snapshot.startswith(raw_seen) else ""
                        if delta:
                            raw_seen = snapshot
                    if delta:
                        # The visible ChatGPT analysis/preview is optionally
                        # streamed in a separate, non-answer lane. It must not
                        # be appended to final assistant content or persisted
                        # as the canonical answer.
                        preview = staged_text.preview_delta(
                            delta,
                            enabled=state.diagnostics.get("visible_reasoning_status_kind") == "reasoning",
                        )
                        if preview:
                            yield "data: " + json.dumps(
                                chunk_payload(response_id, model, {"reasoning": preview}),
                                ensure_ascii=False,
                            ) + "\n\n"
                elif event_type == "chat.completed":
                    raw_final = str(event.get("text") or state.text)
                    try:
                        final_text, archived_media, archived_files = await archive_verified_terminal(
                            state, raw_final, principal.key_id, state.request_id,
                            media_base_for_request(request),
                        )
                    except ValueError as error:
                        await record_request(
                            state, model, prompt, "error", principal, stream=True, prompt_mode=prompt_mode,
                            response_id=response_id, final_text=sent_text, error=str(error),
                            request_type=request_type, attachments_count=attachments_count,
                        )
                        recorded = True
                        yield "data: " + json.dumps({"error": {"type": "media_storage_error", "message": str(error)}}) + "\n\n"
                        yield "data: [DONE]\n\n"
                        break
                    # Record the one genuine Worker terminal BEFORE yielding SSE
                    # content: slow or disconnected clients must not leave a
                    # completed browser request stuck in telemetry.running.
                    state.diagnostics["server_stream_terminal_stage"] = "persisting-before-sse"
                    usage, token_meta = await record_request(
                        state, model, prompt, "completed", principal, stream=True, prompt_mode=prompt_mode,
                        response_id=response_id, final_text=final_text, request_type=request_type,
                        attachments_count=attachments_count, media_items=archived_media,
                    )
                    recorded = True
                    state.diagnostics["server_stream_terminal_stage"] = "persisted-before-sse"
                    # The final same-request Worker terminal is the one owner
                    # of assistant content, regardless of provisional snapshots.
                    # Emit the *whole* authoritative text in bounded SSE chunks
                    # even if the preview is unrelated ("我会..." -> "# ...").
                    for delta in final_text_chunks(final_text):
                        sent_text += delta
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, {"content": delta}),
                            ensure_ascii=False,
                        ) + "\n\n"
                    meta = chat2api_meta(state, token_meta, principal)
                    if native_content.get(state.request_id, principal.key_id):
                        meta["native_content"] = {
                            "version": 1,
                            "url": f"/v1/native-content/{state.request_id}",
                            "events_url": f"/v1/native-content/{state.request_id}/events",
                        }
                    if archived_media:
                        meta["media_files"] = archived_media
                    if archived_files:
                        meta["generated_files"] = archived_files
                    if archived_media or archived_files:
                        meta["content_blocks"] = rich_content_manifest(final_text, archived_media, archived_files)
                    yield "data: " + json.dumps(
                        chunk_payload(response_id, model, {}, "stop", usage, meta),
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif event_type in {"chat.error", "chat.cancelled"}:
                    message = str(event.get("error") or event.get("reason") or "Browser request failed")
                    await record_request(state, model, prompt, failure_status(state, str(event_type), message), principal, stream=True, prompt_mode=prompt_mode,
                                         response_id=response_id, final_text=sent_text, error=message,
                                         request_type=request_type, attachments_count=attachments_count)
                    recorded = True
                    yield "data: " + json.dumps(
                        {"error": {"message": message, "type": "browser_error"},
                         "chat2api": {"request_id": state.request_id, "api_key": principal.as_dict(),
                                      "diagnostics": state.diagnostics, "timings": state.timings()}},
                        ensure_ascii=False,
                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
                    break
        except asyncio.CancelledError:
            # ASGI is cancelling an HTTP delivery task, not a Worker request.
            if not recorded:
                transport = request.scope.get("chat2api_sse_transport_truth")
                peer_disconnect = bool(isinstance(transport, dict)
                                       and transport.get("http_disconnect_seen"))
                state.diagnostics["api_stream_peer_disconnect_seen"] = peer_disconnect
                state.diagnostics["api_stream_close_cause"] = (
                    "http-peer-disconnected" if peer_disconnect else "asgi-stream-task-cancelled"
                )
            raise
        finally:
            if recorded:
                registry.busy_clients.discard(state.client_id)
                await broker.release(state.request_id)
            else:
                # The previous v216-v218 branch independently wrote 'cancelled',
                # sent chat.cancel and released the Broker before request-v6
                # could produce its genuine completion. Do NONE of those here.
                state.diagnostics.setdefault(
                    "api_stream_close_cause", "generator-closed-without-final-delivery"
                )
                state.diagnostics["api_stream_closed_at"] = time.time()
                state.diagnostics["api_stream_closed_before_delivery"] = True
                state.diagnostics["api_stream_closure_worker_terminal"] = (
                    state.terminal_event_type or "not-yet-received"
                )
                state.diagnostics["api_delivery_state"] = "disconnected"
                state.diagnostics["server_finalization_stage"] = (
                    "awaiting-broker-terminal-after-http-disconnect"
                )

                async def persist_broker_terminal(terminal_error: str | None) -> None:
                    terminal = str(state.terminal_event_type or "")
                    is_success = terminal == "chat.completed"
                    error_message = None if is_success else (
                        terminal_error or "Worker terminal: " + (terminal or "missing")
                    )
                    final_status = "completed" if is_success else failure_status(
                        state, terminal, error_message or ""
                    )
                    final_text = state.text if is_success else ""
                    archived_media = []
                    if is_success:
                        try:
                            final_text, archived_media, _ = await archive_verified_terminal(
                                state, final_text, principal.key_id, state.request_id,
                                media_base_for_request(request),
                            )
                        except ValueError as error:
                            final_status = "error"
                            error_message = str(error)
                            state.diagnostics["detached_stream_media_archive_error"] = error_message
                    await record_request(
                        state, model, prompt, final_status, principal,
                        stream=True, prompt_mode=prompt_mode, response_id=response_id,
                        final_text=final_text if final_status == "completed" else "",
                        error=error_message,
                        request_type=request_type, attachments_count=attachments_count,
                        media_items=archived_media,
                    )

                async def release_broker_after_terminal() -> None:
                    registry.busy_clients.discard(state.client_id)
                    await broker.release(state.request_id)

                # Wait as long as the actual Worker lease (which may exceed
                # the HTTP client's 300s deadline), plus a small cleanup grace.
                lease_seconds = max(
                    float(timeout_seconds),
                    float(state.diagnostics.get("request_timeout_seconds") or 0),
                )
                task = asyncio.create_task(settle_detached_stream(
                    state=state, broker=broker,
                    finalize=persist_broker_terminal,
                    release=release_broker_after_terminal,
                    wait_seconds=lease_seconds + 45.0,
                ))
                app.state.detached_sse_tasks.add(task)

                def detached_task_finished(done: asyncio.Task) -> None:
                    app.state.detached_sse_tasks.discard(done)
                    if not done.cancelled():
                        done.exception()  # Retrieve and log errors from supervisor.

                task.add_done_callback(detached_task_finished)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
        x_chat2api_request_id: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ):
        require_scope(principal, "chat")
        prompt = build_prompt(body.messages, body.prompt_mode)
        inline_ids = await inline_image_files(body, principal)
        file_ids = list(dict.fromkeys(body.all_file_ids() + inline_ids))
        attachments = attachment_specs(file_ids, principal)
        request_id = accepted_request_id(x_chat2api_request_id, "req_")
        response_id = completion_id()
        request_started = time.perf_counter()
        requested_client = body.client_id or x_chat2api_client
        request_type = "multimodal" if attachments else "text"

        # v220: the request exists from admission ingress, not only after a
        # capacity slot opens. The queued row remains visible in Request History.
        await record_request_queued(
            request_id=request_id, response_id=response_id, requested_model=body.model,
            prompt=prompt, principal=principal, stream=body.stream,
            prompt_mode=body.prompt_mode, request_type=request_type,
            attachments_count=len(attachments),
        )
        try:
            client_id = await resolve_client_with_reconnect_grace(requested_client)
            await telemetry.update(request_id, {
                "client_id": client_id,
                "diagnostics": {"request_admission_stage": "waiting-capacity"},
            })
            logger.info(
                "API request awaiting Broker capacity request_id=%s worker=%s",
                request_id, client_id,
                extra={"request_id": request_id, "client_id": client_id,
                       "admission_stage": "waiting-capacity"},
            )
            state = await broker.create(request_id, client_id)
        except HTTPException as error:
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error.detail), request_type=request_type,
                                     attachments_count=len(attachments))
            raise
        except RuntimeError as error:
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error), request_type=request_type,
                                     attachments_count=len(attachments))
            raise HTTPException(status_code=409, detail=str(error)) from error
        except asyncio.CancelledError:
            # No Broker allocation or browser dispatch completed. Preserve the
            # admission failure separately from request-v6's Worker terminal.
            await telemetry.update(request_id, {
                "status": "error",
                "diagnostics": {
                    "request_admission_stage": "aborted-before-worker-dispatch",
                    "request_admission_wait_ms": round(
                        (time.perf_counter() - request_started) * 1000, 1
                    ),
                },
                "error": "API transport ended before Worker admission",
            })
            raise

        state.diagnostics["request_admission_stage"] = "dispatched"
        state.diagnostics["request_admission_wait_ms"] = round(
            (time.perf_counter() - request_started) * 1000, 1
        )
        registry.busy_clients.add(client_id)
        state.probe_policy = probe_policy(client_id)
        timeout_seconds = body.timeout or config.request_timeout_seconds
        automatic_media_extension = body.timeout is None
        long_text_requested = long_form_text_requested(prompt)
        browser_timeout_seconds = worker_chat_timeout(
            timeout_seconds, automatic_extension=automatic_media_extension, stream=body.stream,
        )
        try:
            await record_request_running(
                state,
                body.model,
                prompt,
                principal,
                stream=body.stream,
                prompt_mode=body.prompt_mode,
                response_id=response_id,
                request_type=request_type,
                attachments_count=len(attachments),
            )
            await registry.send(
                client_id,
                {
                    "type": "chat.request",
                    "request_id": request_id,
                    "prompt": prompt,
                    "attachments": attachments,
                    "options": {"auto_switch_text": True, "timeout_seconds": browser_timeout_seconds, "model": body.model,
                                "probe_policy": state.probe_policy},
                },
            )
        except Exception as error:
            state.completed_mono = time.perf_counter()
            await record_request(state, body.model, prompt, "error", principal, stream=body.stream,
                                 prompt_mode=body.prompt_mode, response_id=response_id, error=str(error),
                                 request_type=request_type, attachments_count=len(attachments))
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)
            raise HTTPException(status_code=503, detail=str(error)) from error

        if body.stream:
            return StreamingResponse(
                stream_request(request, state, response_id, body.model, timeout_seconds, prompt, principal,
                               body.prompt_mode, len(attachments), automatic_media_extension, long_text_requested),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        detached_nonstream = False
        try:
            # Shield the same broker Future rather than cancelling it at 300s.
            # Only request-v6 emits a terminal; correlated media handoff may
            # extend a default deadline but cannot override an explicit timeout.
            started_at = asyncio.get_running_loop().time()
            while True:
                remaining = chat_deadline_remaining(
                    state, started_at, timeout_seconds, automatic_media_extension, long_text_requested,
                )
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    text = await asyncio.wait_for(
                        asyncio.shield(state.final_future), timeout=min(1.0, remaining),
                    )
                    break
                except asyncio.TimeoutError:
                    continue
            # The real image bytes must be on chat2api storage before the API
            # exposes any image URI; the historical data URI is never logged.
            try:
                text, archived_media, archived_files = await archive_verified_terminal(
                    state, text, principal.key_id, request_id,
                    media_base_for_request(request),
                )
            except ValueError as error:
                raise RuntimeError(str(error)) from error
            # The transport returns the assistant's completed text unchanged.
            # Prompt-language formatting requirements are not HTTP transport
            # failures; explicit structured-output contracts belong to the API
            # request schema (and may be validated by an opt-in consumer).
            usage, token_meta = await record_request(
                state, body.model, prompt, "completed", principal, stream=False, prompt_mode=body.prompt_mode,
                response_id=response_id, final_text=text, request_type=request_type,
                attachments_count=len(attachments), media_items=archived_media,
            )
            meta = chat2api_meta(state, token_meta, principal)
            if native_content.get(request_id, principal.key_id):
                meta["native_content"] = {
                    "version": 1,
                    "url": f"/v1/native-content/{request_id}",
                    "events_url": f"/v1/native-content/{request_id}/events",
                }
            if archived_media:
                meta['media_files'] = archived_media
            if archived_files:
                meta['generated_files'] = archived_files
            if archived_media or archived_files:
                meta['content_blocks'] = rich_content_manifest(text, archived_media, archived_files)
        except asyncio.CancelledError:
            # The HTTP task is gone, not the Worker request. Never release the
            # Broker lease here: otherwise the real completion will be dropped
            # and Request History will remain running forever. Reuse exactly
            # the detached SSE supervisor and existing Broker terminal owner.
            state.diagnostics["api_delivery_state"] = "disconnected"
            state.diagnostics["api_nonstream_close_cause"] = "asgi-task-cancelled"
            state.diagnostics["server_finalization_stage"] = (
                "awaiting-broker-terminal-after-http-disconnect"
            )

            async def persist_detached_nonstream(terminal_error: str | None) -> None:
                terminal = str(state.terminal_event_type or "")
                is_success = terminal == "chat.completed"
                status_value = "completed" if is_success else failure_status(
                    state, terminal, terminal_error or ""
                )
                error_message = None if is_success else (
                    terminal_error or "Worker terminal: " + (terminal or "missing")
                )
                final_text, archived_media = "", []
                if is_success:
                    try:
                        final_text, archived_media, _ = await archive_verified_terminal(
                            state, state.text, principal.key_id, request_id,
                            media_base_for_request(request),
                        )
                    except Exception as exc:
                        status_value = "error"
                        error_message = str(exc)[:500]
                        state.diagnostics["detached_nonstream_archive_error"] = error_message
                await record_request(
                    state, body.model, prompt, status_value, principal, stream=False,
                    prompt_mode=body.prompt_mode, response_id=response_id,
                    final_text=final_text if status_value == "completed" else "",
                    error=error_message, request_type=request_type,
                    attachments_count=len(attachments), media_items=archived_media,
                )

            async def release_detached_nonstream() -> None:
                registry.busy_clients.discard(client_id)
                await broker.release(request_id)

            lease_remaining = max(1.0, float(browser_timeout_seconds)
                - (asyncio.get_running_loop().time() - started_at))
            task = asyncio.create_task(settle_detached_stream(
                state=state, broker=broker,
                finalize=persist_detached_nonstream,
                release=release_detached_nonstream,
                wait_seconds=lease_remaining + 45.0,
            ))
            app.state.detached_sse_tasks.add(task)
            def detached_nonstream_finished(done: asyncio.Task) -> None:
                app.state.detached_sse_tasks.discard(done)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(detached_nonstream_finished)
            detached_nonstream = True
            raise
        except asyncio.TimeoutError as error:
            await registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
            state.completed_mono = time.perf_counter()
            await record_request(state, body.model, prompt, "error", principal, stream=False,
                                 prompt_mode=body.prompt_mode, response_id=response_id, final_text=state.text,
                                 error="Timed out waiting for ChatGPT", request_type=request_type,
                                 attachments_count=len(attachments))
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT") from error
        except RuntimeError as error:
            message = str(error)
            await record_request(state, body.model, prompt, failure_status(state, message=message), principal, stream=False,
                                 prompt_mode=body.prompt_mode, response_id=response_id, final_text=state.text,
                                 error=message, request_type=request_type, attachments_count=len(attachments))
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            if not detached_nonstream:
                registry.busy_clients.discard(client_id)
                await broker.release(request_id)

        return JSONResponse(
            {"id": response_id, "object": "chat.completion", "created": int(time.time()), "model": body.model,
             "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
             "usage": usage, "chat2api": meta}
        )

    @app.post("/v1/images/generations")
    async def image_generations(
        body: ImageGenerationRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
        x_chat2api_request_id: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ) -> dict[str, object]:
        require_scope(principal, "images")
        attachments = attachment_specs([item.file_id for item in body.attachments], principal)
        request_id = accepted_request_id(x_chat2api_request_id, "imgreq_")
        response_id = "img_" + uuid.uuid4().hex
        started = time.perf_counter()
        client_id = resolve_client_now(body.client_id or x_chat2api_client)
        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        registry.busy_clients.add(client_id)
        state.probe_policy = probe_policy(client_id)
        timeout_seconds = body.timeout or max(config.request_timeout_seconds, 300)
        try:
            await telemetry.upsert(
                {
                    "request_id": request_id, "response_id": response_id, "client_id": client_id,
                    "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                    "requested_model": "gpt-image", "request_type": "image_generation",
                    "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                    "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "running",
                    "usage": {}, "timings": state.timings(), "diagnostics": dict(state.diagnostics), "error": None,
                }
            )
            await registry.send(
                client_id,
                {
                    "type": "image.request",
                    "request_id": request_id,
                    "prompt": body.prompt,
                    "attachments": attachments,
                    "options": {"model": "gpt-image", "size": body.size, "timeout_seconds": timeout_seconds,
                                "probe_policy": state.probe_policy},
                },
            )
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            completed: dict[str, Any] | None = None
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    continue
                kind = event.get("type")
                if kind == "image.completed":
                    completed = event
                    break
                if kind in {"image.error", "image.cancelled"}:
                    raise RuntimeError(str(event.get("error") or event.get("reason") or "Image generation failed"))
            if not completed:
                raise asyncio.TimeoutError
            images = completed.get("images") if isinstance(completed.get("images"), list) else []
            if not images:
                raise RuntimeError("ChatGPT Images finished without a captured image")
            output: list[dict[str, object]] = []
            for image in images[:1]:
                if body.response_format == "b64_json":
                    if image.get("b64_json"):
                        output.append({"b64_json": image["b64_json"]})
                    elif image.get("url"):
                        output.append({"url": image["url"]})
                else:
                    if image.get("url"):
                        output.append({"url": image["url"]})
                    elif image.get("b64_json"):
                        mime = image.get("mime_type") or "image/png"
                        output.append({"url": f"data:{mime};base64,{image['b64_json']}"})
            # Store actual generated image bytes separately from the JSONL
            # request history. Never persist a base64 image or guessed /mnt/data
            # path in response_text; a protected admin endpoint serves previews.
            archived_media: list[dict[str, object]] = []
            for image in images[:1]:
                encoded = image.get("b64_json")
                mime = str(image.get("mime_type") or "image/png").lower()
                if not isinstance(encoded, str) or mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    continue
                try:
                    media_item = await file_store.create(
                        filename=f"generated-{request_id}.png" if mime == "image/png" else f"generated-{request_id}.{mime.split('/')[-1]}",
                        data_base64=encoded, mime_type=mime,
                        owner_key_id=principal.key_id, purpose="generated-output",
                    )
                    archived_media.append({"file_id": media_item.file_id, "mime_type": mime, "bytes": media_item.size})
                except (ValueError, OSError) as error:
                    logger.warning("Could not archive generated media request=%s reason=%s", request_id, type(error).__name__)
            # Keep the legacy admin FileStore previews while routing client
            # downloads through the newly configurable archive provider.
            owned_media = []
            for image in images[:1]:
                encoded = image.get("b64_json")
                mime = str(image.get("mime_type") or "image/png").lower()
                if not isinstance(encoded, str):
                    if body.response_format != "b64_json":
                        raise RuntimeError("Image result has no bytes for chat2api-owned URL")
                    continue
                try:
                    raster = base64.b64decode(encoded, validate=True)
                    row = await asyncio.to_thread(media_archive.store_image, raster, mime, principal.key_id, request_id)
                except (ValueError, OSError) as error:
                    if body.response_format == "b64_json":
                        # Preserve the explicit OpenAI Base64 response contract,
                        # even if an auxiliary archive does not accept the asset.
                        # URL mode must never fall back to an upstream address.
                        logger.warning("Optional gpt-image archive skipped request=%s: %s", request_id, type(error).__name__)
                        continue
                    raise RuntimeError("Unable to archive generated image") from error
                origin = media_archive._config().get("public_base_url") or media_base_for_request(request)
                owned_url = f"{origin}/api/media/{row['id']}/{row['token']}"
                owned_media.append({"id": row["id"], "url": owned_url,
                                    "thumbnail_url": owned_url + "/thumbnail",
                                    "bytes": row["bytes"], "mime_type": mime,
                                    "backend": row["backend"]})
            if body.response_format == "url":
                output = [{"url": item["url"]} for item in owned_media]
            state.completed_mono = state.completed_mono or time.perf_counter()
            usage, token_meta = standard_usage(body.prompt, "")
            await telemetry.upsert(
                {
                    "response_media": [dict(legacy, **{"owned_url": owned_media[i]["url"],
                        "owned_thumbnail_url": owned_media[i]["thumbnail_url"]})
                        for i, legacy in enumerate(archived_media) if i < len(owned_media)],
                    "response_media_count": len(output),
                    "response_text": "已生成图片（在查看对话中预览）" if archived_media else "图片接口返回结果，但没有可归档的图片字节",
                    "response_text_available": bool(archived_media),
                    "request_id": request_id, "response_id": response_id, "client_id": client_id,
                    "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                    "requested_model": "gpt-image", "request_type": "image_generation",
                    "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                    "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "completed",
                    "usage": {**usage, **token_meta}, "timings": state.timings(),
                    "diagnostics": dict(state.diagnostics), "error": None,
                }
            )
            # The standalone Images API already archived verified bytes.
            # Reuse those exact records; do not capture or upload images twice.
            if owned_media:
                try:
                    native_content.publish(request_id, principal.key_id, {
                        "version": 1, "revision": 1,
                        "root": {"tag": "div", "style": {
                            "display": "flex", "flexWrap": "wrap", "gap": "8px",
                        }, "children": [
                            {"tag": "img", "media_index": index}
                            for index in range(min(40, len(owned_media)))
                        ]},
                    })
                    native_content.persist(request_id, principal.key_id)
                except (ValueError, TypeError):
                    pass
            return {
                "created": int(time.time()),
                "model": "gpt-image",
                "data": output,
                "usage": usage,
                "chat2api": {
                    "request_id": request_id,
                    "client_id": client_id,
                    "timings": state.timings(),
                    "diagnostics": state.diagnostics,
                    "token_usage": token_meta,
                    "media_files": archived_media,
                    "owned_media": owned_media,
                    **({"native_content": {
                        "version": 1,
                        "url": f"/v1/native-content/{request_id}",
                        "events_url": f"/v1/native-content/{request_id}/events",
                    }} if owned_media else {}),
                },
            }
        except asyncio.TimeoutError as error:
            state.completed_mono = time.perf_counter()
            await telemetry.upsert(
                {"request_id": request_id, "response_id": response_id, "client_id": client_id,
                 "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                 "requested_model": "gpt-image", "request_type": "image_generation",
                 "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                 "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "error",
                 "usage": {}, "timings": state.timings(), "diagnostics": state.diagnostics,
                 "error": "Timed out waiting for ChatGPT Images"}
            )
            raise HTTPException(status_code=504, detail="Timed out waiting for ChatGPT Images") from error
        except RuntimeError as error:
            state.completed_mono = state.completed_mono or time.perf_counter()
            await telemetry.upsert(
                {"request_id": request_id, "response_id": response_id, "client_id": client_id,
                 "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                 "requested_model": "gpt-image", "request_type": "image_generation",
                 "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                 "prompt_chars": len(body.prompt), "completion_chars": 0,
                 "status": failure_status(state, message=str(error)),
                 "usage": {}, "timings": state.timings(), "diagnostics": state.diagnostics, "error": str(error)}
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            registry.busy_clients.discard(client_id)
            await broker.release(request_id)

    # Both Images and Responses image_generation use this exact Worker/Broker
    # lifecycle. The Responses adapter only translates the result protocol.
    app.state.image_generation_executor = image_generations

    @app.get("/api/admin/requests/{request_id}/media/{file_id}", dependencies=[Depends(require_admin_key)])
    async def admin_request_media(request_id: str, file_id: str) -> Response:
        """Serve an archived image only when it belongs to the requested row."""
        row = telemetry.get(request_id)
        media = (row or {}).get("response_media")
        if not isinstance(media, list) or not any(
            isinstance(item, dict) and item.get("file_id") == file_id for item in media
        ):
            raise HTTPException(status_code=404, detail="No archived image for this request")
        try:
            item, payload = file_store.read(file_id)
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Archived image is no longer available") from error
        if item.purpose != "generated-output" or item.mime_type not in {
            "image/png", "image/jpeg", "image/webp", "image/gif"
        }:
            raise HTTPException(status_code=404, detail="Invalid request media")
        return Response(
            content=payload, media_type=item.mime_type,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.websocket("/ws/extensions/{client_id}")
    async def extension_socket(websocket: WebSocket, client_id: str, token: str = Query(default="")) -> None:
        if not await registry.authenticate(client_id, token):
            await websocket.close(code=4401, reason="Invalid extension token")
            return
        await websocket.accept()
        await registry.attach(client_id, websocket)
        # Per-socket generation guard: Worker settings are server-authoritative and
        # are pushed exactly once after this connection advertises control readiness.
        # The browser persistent pool remains the sole physical-window authority.
        window_target_synced = False
        socket_runtime_epoch = ""  # bound to this socket, not shared metadata
        try:
            await websocket.send_json({"type": "server.hello", "client_id": client_id, "version": APP_VERSION})
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else None
                if message_type == "extension.hello" and metadata:
                    socket_runtime_epoch = str(metadata.get("transport_runtime_epoch") or "")
                await registry.touch(client_id, metadata)
                if message_type in {"heartbeat", "extension.hello", "extension.status"}:
                    if message_type in {"extension.hello", "extension.status"} and not window_target_synced:
                        window_runtime = getattr(app.state, "worker_window_limits", {})
                        sync_online = window_runtime.get("sync_online") if isinstance(window_runtime, dict) else None
                        if callable(sync_online):
                            try:
                                sync_result = await sync_online(client_id)
                                window_target_synced = bool(sync_result.get("sent"))
                            except Exception:
                                logger.exception("Failed to sync Worker standby target on connect: %s", client_id)
                    if message_type == "heartbeat":
                        await websocket.send_json({"type": "heartbeat.ack", "ts": time.time()})
                    continue
                request_id = str(message.get("request_id") or "")
                if message_type == "chat.file.chunk":
                    # A successful socket.send is NOT a persisted chunk ACK.
                    # The server replies to the same authenticated socket only
                    # after the bounded stage write/validation completes.
                    accepted, reason = False, "request-or-socket-not-active"
                    state = broker.requests.get(request_id)
                    if (state and state.client_id == client_id
                        and registry.sockets.get(client_id) is websocket
                        and state.terminal_event_type is None):
                        try:
                            await asyncio.to_thread(
                                generated_staging.ingest,
                                request_id=request_id,
                                upload_id=message.get("upload_id"),
                                filename=message.get("filename"),
                                mime=message.get("mime"),
                                sequence=message.get("sequence"),
                                expected_bytes=message.get("expected_bytes"),
                                expected_sha256=message.get("expected_sha256"),
                                base64_chunk=message.get("base64_chunk"),
                                final=message.get("final"),
                            )
                            state.diagnostics["generated_chunk_upload_seen"] = True
                            accepted, reason = True, ""
                        except (ValueError, OSError) as exc:
                            # Persist only safe fixed-class evidence, never
                            # Base64 bytes or the browser's source URL.
                            reason = type(exc).__name__ + ":" + str(exc)[:90]
                            state.diagnostics["generated_chunk_failure"] = reason
                    await websocket.send_json({
                        "type": "chat.file.chunk.ack",
                        "request_id": request_id,
                        "upload_id": str(message.get("upload_id") or "")[:40],
                        "sequence": message.get("sequence"),
                        "ok": accepted,
                        "error": reason,
                    })
                    continue
                if message_type == "chat.native.snapshot" and request_id:
                    # Read-only observer from the authenticated current Worker.
                    # Never broker.publish, never set a terminal, never log the tree.
                    active_state = broker.requests.get(request_id)
                    row = telemetry.get(request_id) or {}
                    owner = str(row.get("api_key_id") or "")
                    if (active_state and active_state.client_id == client_id
                            and registry.sockets.get(client_id) is websocket and owner):
                        try:
                            native_content.publish(request_id, owner, message.get("snapshot"))
                        except (ValueError, TypeError):
                            pass
                    continue
                if message_type == "probe.event" and request_id:
                    # Existing authenticated socket and broker are the sole
                    # request-to-Worker authority; probe events never reach the
                    # request terminal queue.
                    state = broker.requests.get(request_id)
                    if state and state.client_id == client_id and registry.sockets.get(client_id) is websocket:
                        try:
                            result = await worker_probes.ingest(
                                request_id=request_id, worker_id=probe_worker_id(client_id),
                                client_id=client_id, message=message,
                                effective_policy=getattr(state, "probe_policy", None),
                                tab_id=state.diagnostics.get("tab_id"),
                                window_id=state.diagnostics.get("window_id"))
                            if result.get("accepted"):
                                state.diagnostics["probe_event_count"] = (
                                    int(state.diagnostics.get("probe_event_count") or 0)
                                    + (0 if result.get("deduplicated") else 1)
                                )
                                state.diagnostics["probe_capture_status"] = "ok"
                        except (ValueError, PermissionError) as error:
                            logger.info("Rejected Worker probe evidence request=%s reason=%s",
                                        request_id, type(error).__name__)
                    continue
                if request_id and message_type in {
                    "chat.diagnostics", "chat.started", "chat.delta", "chat.snapshot", "chat.completed",
                    "chat.error", "chat.cancelled", "image.diagnostics", "image.started", "image.progress",
                    "image.completed", "image.error", "image.cancelled",
                }:
                    terminal_message = message_type in {
                        "chat.completed", "chat.error", "chat.cancelled",
                        "image.completed", "image.error", "image.cancelled",
                    }
                    active_state = broker.requests.get(request_id)
                    if terminal_message and active_state and active_state.client_id == client_id:
                        active_state.diagnostics["terminal_transport_server_seen"] = True
                        active_state.diagnostics["terminal_transport_server_received_at"] = time.time()
                        active_state.diagnostics["terminal_transport_payload_chars"] = len(str(message.get("text") or ""))
                    accepted = await broker.publish(request_id, message)
                    if accepted and message_type == "chat.completed" and active_state:
                        row = telemetry.get(request_id) or {}
                        owner = str(row.get("api_key_id") or "")
                        if owner:
                            native_content.persist(request_id, owner)
                    if terminal_message:
                        if active_state and active_state.client_id == client_id:
                            active_state.diagnostics["terminal_transport_broker_accepted"] = bool(accepted)
                            # Persist acknowledgement as an observation, never as
                            # a competing completed/error terminal. If the API
                            # handler is stuck in archival, the exported log
                            # now reveals the precise boundary it reached.
                            active_state.diagnostics["server_terminal_receipt_stage"] = (
                                "broker-accepted" if accepted else "broker-rejected"
                            )
                            active_state.diagnostics["server_terminal_receipt_type"] = str(message_type)
                            await telemetry.upsert({
                                "request_id": request_id,
                                "diagnostics": dict(active_state.diagnostics),
                            })
                        if not accepted:
                            logger.warning(
                                "Worker terminal not accepted request=%s type=%s active=%s",
                                request_id, message_type, bool(active_state),
                            )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Extension WebSocket failed: %s", client_id)
        finally:
            await registry.detach(client_id, websocket)
            # Capacity can dispatch multiple distinct API keys on one Worker.
            # Only this socket's requests lost transport; never borrow the
            # single-slot legacy client_requests pointer (or affect new work).
            lost_requests = [
                state.request_id for state in list(broker.requests.values())
                if state.client_id == client_id and getattr(state, "dispatch_socket", None) is websocket
            ]
            if lost_requests:
                await asyncio.gather(*(broker.publish(request_id, {
                    "type": "chat.error", "request_id": request_id,
                    "error": "Chrome extension disconnected",
                    "_closed_socket": websocket,
                    "transport_runtime_epoch": socket_runtime_epoch,
                }) for request_id in lost_requests))

    return app


app = create_app()
