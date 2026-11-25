from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from .admin import admin_response
from .admin_auth import same_origin_admin_request
from .api_keys import ApiKeyStore, ApiPrincipal, UNSET
from .broker import RequestBroker, RequestState
from .chat_tool_references_v1 import apply_inline_citation_markers, extract_tool_references
from .web_chat_event_journal_v1 import WebChatEventJournal
from .web_chat_terminal_replay_v1 import (
    is_already_finalized_worker_terminal,
    is_replayed_mode1_terminal, replayed_terminal_projection,
)
from .chat_request_service import ChatRequestService
from .web_chat_cancellation_v1 import (
    WebChatAdmissionCancelled, WebChatCancellationSignal,
    await_worker_admission_or_cancel,
)
from .chat_media_deadline_v171 import chat_timeout_window, worker_chat_timeout, long_form_text_requested
from .chat_progress_stream_v196 import status_from_diagnostics, progress_is_real
from .chat_stream_delivery_v194 import StagedStreamText, final_text_chunks, sse_heartbeat_interval
from .stream_detached_terminal_v219 import settle_detached_stream
from .sse_asgi_transport_truth_v284 import SSETransportTruth
from .terminal_archive_singleflight_v277 import archive_once
from .mixed_media_archive_v280 import archive_mixed_reply
from .archive_write_stop_v280 import ArchiveWriteStop
from .config import Settings, get_settings
from .file_store import FileIntegrityError, FileStore
from .media_archive_v173 import MediaArchive
from .api_mode_guard import ApiModeGuardMiddleware
from .generated_files_v196 import GeneratedFileArchive
from .generated_file_staging_v238 import GeneratedFileStaging
from .generated_file_deferred_v247 import DeferredGeneratedFiles, FileStillProcessing, FileTransferFailed
from .rich_content_v196 import manifest as rich_content_manifest
from .native_content_v1 import (NativeContentStore, NativeChunkAssembler, native_delivery_fingerprint,
                                native_layout_summary, native_unique_dimension_candidates,
                                native_image_identity_candidates,
                                native_public_v1_tree)
from .native_dom_evidence_v1 import NativeDomEvidenceStore
from .native_capture_audit_v281 import capture_comparison
from .native_snapshot_finality_v282 import native_snapshot_finality, native_terminal_view
from .media_public_origin_v187 import effective_media_base_url, media_base_for_request
from .response_history_media_v188 import history_safe_response_text
from .responses_multimodal_input_v183 import (
    attachment_specs as canonical_attachment_specs,
    validate_document_file,
    validate_image_file,
    verify_raster_image,
)
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
from .telemetry import DuplicateRequestIdentity, TelemetryStore, parse_request_since
from .runtime_contract import SERVER_RUNTIME_VERSION
from .runtime_code_identity import runtime_code_sha
from .worker_probe_store import WorkerProbeStore
from .test_runs import TestRunStore
from .playground_truth_v170 import (reconcile_playground_runs, reconcile_playground_request_terminal,
                                  compact_playground_outcome, present_playground_run)
from .playground_stream_contract_v293 import playground_stream_attribution
from .playground_request_evidence_v207 import request_test_index, playground_link
from .token_usage import usage_for
from .quota_reservation_runtime_v1 import (
    authorize_wallet_dispatch,
    is_request_id_conflict,
    reserve_image_generation_quota,
    reserve_request_quota,
)

logger = logging.getLogger("chat2api")
APP_VERSION = "0.5.0"


def web_chat_delivery_text(final_text: str, source_text: str, terminal: dict[str, Any]) -> str:
    """Return the exact Web Chat projection while keeping canonical answer bytes untouched."""
    if not isinstance(final_text, str) or final_text != source_text or not isinstance(terminal, dict):
        return final_text if isinstance(final_text, str) else ""
    diagnostics = terminal.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    if (
        diagnostics.get("response_boundary_prompt_echo_removed") is True
        or diagnostics.get("native_citation_text_match") is not True
    ):
        return final_text
    references = terminal.get("references")
    if not isinstance(references, list) or not references:
        references = extract_tool_references(terminal.get("metadata"))
    if not references:
        return final_text
    return apply_inline_citation_markers(final_text, references)


async def _telemetry_upsert_best_effort(
    telemetry: Any, row: dict[str, Any], *, request_id: str, projection: str,
) -> None:
    """Persist an optional telemetry projection without owning request success."""
    try:
        await telemetry.upsert(row)
    except Exception:
        logger.exception(
            "Best-effort telemetry projection failed request_id=%s projection=%s",
            request_id, projection,
        )


def _native_snapshot_persist_best_effort(
    native_content: Any, request_id: str, owner: str,
    diagnostics: dict[str, Any],
) -> bool:
    """Persist native content without allowing archive I/O to reverse Broker finality."""
    if not owner:
        return False
    try:
        return bool(native_content.persist(request_id, owner))
    except Exception:
        diagnostics["native_snapshot_persist_error"] = "persistence-failed"
        logger.exception(
            "Broker accepted terminal but native snapshot persistence failed request_id=%s",
            request_id,
        )
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    registry = ClientRegistry(config.data_dir)
    event_journal = WebChatEventJournal(config.data_dir)
    broker = RequestBroker()
    broker.event_journal = event_journal
    chat_request_service = ChatRequestService(registry, broker)
    # The request origin is the actual dispatch socket; disconnection of an
    # old socket must not terminate requests already sent on its replacement.
    registry.request_broker = broker
    telemetry = TelemetryStore(config.data_dir)
    worker_probes = WorkerProbeStore(config.data_dir)
    native_dom_evidence = NativeDomEvidenceStore(config.data_dir)
    native_content = NativeContentStore(config.data_dir)
    native_chunks = NativeChunkAssembler(native_content)
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
        journal_ready = True
        try:
            await event_journal.open()
        except Exception:
            journal_ready = False
            broker.event_journal = None
            app.state.web_chat_event_journal = None
            logger.exception("Mode 1 event journal unavailable; Mode 2 remains available")
        await registry.load()
        await telemetry.load()
        await api_keys.load()
        recovered_queued = await telemetry.recover_queued_after_restart(set(broker.requests))
        if recovered_queued:
            logger.warning(
                "Recovered %s queued request(s) from the previous process as stalled",
                recovered_queued,
            )
        recovered_running = await telemetry.recover_running_after_restart(
            set(broker.requests), event_journal if journal_ready else None,
        )
        if recovered_running:
            logger.warning(
                "Recovered %s Mode 1 running request(s); waiting up to 120 seconds for durable Worker terminal replay",
                recovered_running,
            )
        wallet_reconciler = getattr(app.state, "wallet_charge_reconciler", None)
        billing_store = getattr(app.state, "user_billing", None)
        if billing_store is not None:
            try:
                unstarted_wallet_holds = await asyncio.to_thread(
                    billing_store.unstarted_admissions
                )
            except Exception as error:
                unstarted_wallet_holds = []
                logger.warning(
                    "Wallet pre-dispatch startup scan deferred error_type=%s",
                    type(error).__name__,
                )
            quota_store = getattr(app.state, "quota_reservations", None)
            for hold in unstarted_wallet_holds:
                request_id = str(hold.get("request_id") or "")
                user_id = str(hold.get("user_id") or "")
                token_row = None
                if quota_store is not None and request_id:
                    try:
                        token_row = await asyncio.to_thread(quota_store.get, request_id)
                    except Exception as error:
                        logger.warning(
                            "Pre-dispatch token reservation lookup deferred request_id=%s error_type=%s",
                            request_id, type(error).__name__,
                        )
                key_id = str((token_row or {}).get("key_id") or "")
                existing = telemetry.get(request_id) if request_id else None
                if existing is None and request_id:
                    persisted_lookup = getattr(telemetry, "get_many_persisted", None)
                    if callable(persisted_lookup):
                        try:
                            existing = (await asyncio.to_thread(
                                persisted_lookup, {request_id},
                            )).get(request_id)
                        except Exception as error:
                            logger.warning(
                                "Pre-dispatch telemetry lookup deferred request_id=%s error_type=%s",
                                request_id, type(error).__name__,
                            )
                existing_key_id = str((existing or {}).get("api_key_id") or "")
                if key_id and existing_key_id and key_id != existing_key_id:
                    logger.error(
                        "Pre-dispatch wallet/token identity conflict request_id=%s",
                        request_id,
                    )
                    continue
                if str((existing or {}).get("status") or "").lower() == "completed":
                    logger.error(
                        "Pre-dispatch wallet marker conflicts with completed telemetry request_id=%s",
                        request_id,
                    )
                    continue
                existing_diagnostics = (
                    existing.get("diagnostics")
                    if isinstance((existing or {}).get("diagnostics"), dict) else {}
                )
                row_payload = {
                    **(existing or {}),
                    "request_id": request_id,
                    "status": "stalled",
                    "error": "Server restarted before Worker dispatch",
                    "diagnostics": {
                        **existing_diagnostics,
                        "request_admission_recovery": "server-restart-before-worker-dispatch",
                        "request_admission_failure_kind": "server_restart",
                    },
                }
                if key_id or existing_key_id:
                    row_payload["api_key_id"] = key_id or existing_key_id
                try:
                    recovered_row = await telemetry.upsert(row_payload)
                except Exception as error:
                    recovered_row = telemetry.get(request_id) if request_id else None
                    recovered_row = recovered_row or existing
                    logger.warning(
                        "Wallet pre-dispatch telemetry recovery failed request_id=%s error_type=%s",
                        request_id, type(error).__name__,
                    )
                # The first persisted completed terminal always wins. Do not
                # release a hold if contradictory completion evidence exists;
                # leave it for normal charge reconciliation instead.
                if str((recovered_row or {}).get("status") or "").lower() == "completed":
                    logger.error(
                        "Pre-dispatch wallet marker conflicts with completed telemetry request_id=%s",
                        request_id,
                    )
                    continue
                if quota_store is not None and key_id:
                    try:
                        await asyncio.to_thread(quota_store.release, request_id, key_id)
                    except Exception as error:
                        logger.warning(
                            "Pre-dispatch token hold release deferred request_id=%s error_type=%s",
                            request_id, type(error).__name__,
                        )
                if user_id and request_id:
                    try:
                        await asyncio.to_thread(
                            billing_store.release_reservation,
                            request_id, user_id,
                            reason="server_restart_before_worker_dispatch",
                        )
                    except Exception as error:
                        logger.warning(
                            "Pre-dispatch wallet hold release deferred request_id=%s error_type=%s",
                            request_id, type(error).__name__,
                        )
        if callable(wallet_reconciler) and billing_store is not None:
            for user_id in billing_store.active_reservation_user_ids():
                try:
                    wallet_reconciler(user_id)
                except Exception as error:
                    # An unresolved hold remains reserved; startup must not
                    # invent a release or prevent unrelated users from serving.
                    logger.warning(
                        "Wallet reservation startup recovery deferred error_type=%s",
                        type(error).__name__,
                    )
        quota_store = getattr(app.state, "quota_reservations", None)
        if quota_store is not None and getattr(app.state, "quota_reservation_runtime_enabled", False):
            # Recover terminal telemetry before expiring old reservations.
            # TelemetryStore persists a terminal row before its accounting
            # callback; a crash in that gap leaves the reservation reserved,
            # not committing, and must not lose a completed charge.
            replayed_reservations = await quota_store.recover_committing(telemetry, api_keys)
            reconciled_reservations = quota_store.reconcile(api_keys)
            expired_reservations = quota_store.expire_reservations()
            if expired_reservations or replayed_reservations or reconciled_reservations:
                logger.info(
                    "Quota reservation startup recovery expired=%s replayed=%s reconciled=%s",
                    expired_reservations, replayed_reservations, reconciled_reservations,
                )
        await file_store.load()
        conversation_files = getattr(app.state, "web_chat_conversations", None)

        async def expire_web_chat_conversations() -> int:
            if conversation_files is None:
                return 0
            total = 0
            while True:
                expired = await asyncio.to_thread(
                    conversation_files.expire_old_conversations, 500,
                )
                total += expired
                if expired < 500:
                    return total

        async def cleanup_web_chat_orphan_files() -> int:
            if conversation_files is None:
                return 0

            def cleanup_under_file_lock() -> int:
                # Use the same lock order as conversation saves and user/API
                # file deletion: FileStore -> conversation-reference database.
                # This prevents a new reference being committed between the
                # reference snapshot and orphan unlink in this process.
                with file_store.lock:
                    with conversation_files.file_reference_snapshot_guard() as referenced:
                        return file_store.delete_expired_web_chat_files(
                            referenced,
                            older_than_seconds=90 * 86400,
                            limit=500,
                        )

            return await asyncio.to_thread(cleanup_under_file_lock)

        try:
            expired_conversations = await expire_web_chat_conversations()
            if expired_conversations:
                logger.info(
                    "Expired %s Web Chat conversation(s) under the configured retention policy",
                    expired_conversations,
                )
            removed_web_chat_files = await cleanup_web_chat_orphan_files()
            if removed_web_chat_files:
                logger.info(
                    "Expired %s unreferenced Web Chat attachment(s)",
                    removed_web_chat_files,
                )
        except Exception:
            # Retention is best-effort maintenance. A failed scan must not
            # affect chat startup or delete files with uncertain references.
            logger.exception("Web Chat attachment retention scan failed")

        async def expire_web_chat_orphan_files() -> None:
            while True:
                await asyncio.sleep(24 * 60 * 60)
                try:
                    expired = await expire_web_chat_conversations()
                    if expired:
                        logger.info("Expired %s Web Chat conversation(s)", expired)
                    removed = await cleanup_web_chat_orphan_files()
                    if removed:
                        logger.info("Expired %s unreferenced Web Chat attachment(s)", removed)
                except Exception:
                    logger.exception("Web Chat attachment retention scan failed")

        await test_runs.load()
        recovered_runs = await test_runs.recover_interrupted()
        if recovered_runs:
            logger.warning("Recovered %s interrupted playground test run(s) as stalled", len(recovered_runs))
        if config.api_key in {"", "change-me"}:
            logger.warning("CHAT2API_API_KEY is using an unsafe default. Change it before remote exposure.")
        if config.pairing_code in {"", "change-me-pairing"}:
            logger.warning("CHAT2API_PAIRING_CODE is using an unsafe default. Change it before remote exposure.")

        async def expire_lost_web_chat_leases() -> None:
            while True:
                await asyncio.sleep(1.0)
                try:
                    expired = await telemetry.expire_recovering_after_restart(
                        event_journal if journal_ready else None,
                    )
                    if expired:
                        logger.warning(
                            "Marked %s Mode 1 request(s) stalled after Worker terminal replay grace expired",
                            expired,
                        )
                except Exception:
                    logger.exception("Mode 1 restart recovery watchdog failed")

        recovery_watchdog = asyncio.create_task(expire_lost_web_chat_leases())
        file_retention_task = asyncio.create_task(expire_web_chat_orphan_files())
        try:
            yield
        finally:
            recovery_watchdog.cancel()
            file_retention_task.cancel()
            try:
                await recovery_watchdog
            except asyncio.CancelledError:
                pass
            try:
                await file_retention_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="chat2api",
        version=APP_VERSION,
        description="OpenAI-compatible browser bridge for text, files, vision and ChatGPT Images.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.registry = registry
    app.state.broker = broker
    app.state.chat_request_service = chat_request_service
    app.state.web_chat_event_journal = event_journal
    app.state.telemetry = telemetry
    app.state.worker_probes = worker_probes
    app.state.native_dom_evidence = native_dom_evidence
    app.state.native_content = native_content
    app.state.api_keys = api_keys
    app.state.file_store = file_store
    app.state.media_archive = media_archive
    app.state.generated_files = generated_files
    app.state.generated_staging = generated_staging
    app.state.deferred_generated_files = deferred_generated_files
    app.state.test_runs = test_runs
    # Keep detached-stream supervisors alive until their Broker terminal is persisted.
    app.state.detached_sse_tasks = set()
    # Mode 1 cancellation signals cover the queued/admission window before a
    # RequestBroker lease exists. The same lock serializes queued cancellation
    # against the transition to running + the initial Worker dispatch.
    app.state.web_chat_cancel_events = {}
    app.state.web_chat_cancel_lock = asyncio.Lock()

    @app.exception_handler(HTTPException)
    async def mode2_http_exception_handler(request: Request, error: HTTPException):
        if not request.url.path.startswith("/v1/"):
            return await http_exception_handler(request, error)
        detail = error.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            payload = {"error": detail["error"]}
        else:
            values = detail if isinstance(detail, dict) else {}
            if isinstance(detail, dict):
                message = str(values.get("message") or values.get("detail") or "Request failed")
            else:
                message = str(detail or "Request failed")
            status_code = int(error.status_code)
            default_type = (
                "authentication_error" if status_code == 401 else
                "permission_error" if status_code == 403 else
                "not_found_error" if status_code == 404 else
                "conflict_error" if status_code == 409 else
                "rate_limit_error" if status_code == 429 else
                "server_error" if status_code >= 500 else
                "invalid_request_error"
            )
            payload = {"error": {
                "message": message,
                "type": str(values.get("type") or default_type),
                "param": values.get("param"),
                "code": values.get("code"),
            }}
        return JSONResponse(payload, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def mode2_validation_exception_handler(request: Request, error: RequestValidationError):
        if not request.url.path.startswith("/v1/"):
            return await request_validation_exception_handler(request, error)
        errors = error.errors()
        first = errors[0] if errors else {}
        location = first.get("loc") or ()
        param = ".".join(str(part) for part in location if part not in {"body", "query", "path", "header", "cookie"})
        return JSONResponse({
            "error": {
                "message": str(first.get("msg") or "Invalid request"),
                "type": "invalid_request_error",
                "param": param or None,
                "code": str(first.get("type") or "invalid_request_error"),
            }
        }, status_code=422)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=config.origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Pure ASGI receive/send observer: records HTTP disconnect and actual
    # body send boundaries, never contents, and never owns Broker terminal.
    app.add_middleware(SSETransportTruth, telemetry=telemetry)
    # Guard the optional API mode extension before request handlers and transport
    # observers. Every /v1 request is pinned to the OpenAI-compatible API path.
    app.add_middleware(ApiModeGuardMiddleware)

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
        # A managed key selected in Client Configuration is reserved for the
        # authenticated Web Chat Mode 1 adapter. Do not let the same bearer
        # credential silently fall back to the generic OpenAI-compatible Mode 2
        # ingress. User-owned keys are never selected as the shared key and
        # continue to use the original /v1 Mode 2 contract.
        control_plane = getattr(app.state, "control_plane", None)
        configured_mode1_key_id = (
            control_plane.client_api_key_id() if control_plane is not None else ""
        )
        if configured_mode1_key_id and principal.key_id == configured_mode1_key_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This API Key is reserved for Web Chat Mode 1. "
                    "Use the signed-in Web Chat client; user-created API Keys remain Mode 2."
                ),
            )
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

    @app.get("/api/admin/web-chat-retention", dependencies=[Depends(require_admin_key)])
    async def admin_web_chat_retention() -> dict[str, Any]:
        store = getattr(app.state, "web_chat_conversations", None)
        if store is None:
            raise HTTPException(status_code=503, detail="Web Chat conversation service unavailable")
        return store.retention_policy()

    @app.put("/api/admin/web-chat-retention")
    async def update_admin_web_chat_retention(
        payload: dict[str, Any],
        principal: ApiPrincipal = Depends(require_admin_key),
    ) -> dict[str, Any]:
        store = getattr(app.state, "web_chat_conversations", None)
        if store is None:
            raise HTTPException(status_code=503, detail="Web Chat conversation service unavailable")
        retention_days = payload.get("retention_days")
        if type(retention_days) is not int or not 0 <= retention_days <= 3650:
            raise HTTPException(status_code=422, detail="retention_days must be an integer between 0 and 3650")
        policy = store.set_retention_days(retention_days)
        logger.warning(
            "Web Chat conversation retention policy changed admin_key_id=%s retention_days=%s",
            principal.key_id, retention_days,
        )
        return policy

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
        except (ValueError, OSError):
            # Return explicit storage-unavailable instead of HTTP 200 with
            # corrupt original bytes (or a raw internal exception/traceback).
            # This is not a Worker/Broker request terminal transition.
            raise HTTPException(
                status_code=503, detail="Media storage integrity unavailable"
            ) from None
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

    inline_generated_media_mimes = frozenset({
        "audio/mpeg", "audio/wav", "audio/mp4", "audio/ogg", "audio/flac",
        "video/mp4", "video/webm",
    })

    def generated_file_response(data: bytes, mime: str, filename: str,
                                request: Request) -> Response:
        from urllib.parse import quote
        if mime not in inline_generated_media_mimes:
            return Response(data, media_type="application/octet-stream", headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "sandbox",
                "Cache-Control": "private, max-age=86400",
                "X-Chat2API-File-MIME": mime,
            })
        total = len(data)
        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=86400",
            "X-Chat2API-File-MIME": mime,
        }
        range_header = str(request.headers.get("range") or "").strip()
        if len(range_header) > 80:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        if not range_header:
            return Response(data, media_type=mime, headers=headers)
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header, re.IGNORECASE)
        if not match or not any(match.groups()) or total <= 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) if last else total - 1
        else:
            suffix = int(last)
            if suffix <= 0:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
            start = max(0, total - suffix)
            end = total - 1
        if start >= total or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        end = min(end, total - 1)
        payload = data[start:end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(payload))
        return Response(payload, status_code=206, media_type=mime, headers=headers)

    @app.get("/api/generated-files/pending/{file_id}/{access_token}")
    async def download_deferred_generated_file(file_id: str, access_token: str,
                                               request: Request) -> Response:
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
        response = generated_file_response(data, mime, filename, request)
        response.headers["X-Chat2API-File-Status"] = "ready"
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    @app.get("/api/generated-files/pending/{file_id}/{access_token}/status")
    async def status_deferred_generated_file(file_id: str, access_token: str) -> Response:
        try:
            value = await asyncio.to_thread(
                deferred_generated_files.status, file_id, access_token)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @app.get("/api/generated-files/{file_id}/{access_token}")
    async def download_generated_file(file_id: str, access_token: str,
                                      request: Request) -> Response:
        try:
            data, mime, filename = await asyncio.to_thread(generated_files.read, file_id, access_token)
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Unknown generated file") from None
        except (ValueError, OSError):
            # A missing/corrupt S3/local object must never be a successful
            # delivery; do not expose backend paths or credentials to clients.
            raise HTTPException(status_code=503, detail="Generated file storage integrity unavailable") from None
        # Documents/code remain forced downloads. Only verified media types
        # are inline-playable by the native Web Chat renderer.
        return generated_file_response(data, mime, filename, request)

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

    async def native_response_snapshot(request_id: str, owner: str,
                                       *, watch_pending_archive: bool = False,
                                       preserve_native_tree: bool = False) -> dict | None:
        snapshot = native_content.get(request_id, owner)
        if not snapshot:
            return None
        row = telemetry.get(request_id) or {}
        # Historical req_3a882bb5: rev12 persisted, then final capture failed
        # structural-max-children. Never serve that old revision as final
        # native content. Running requests may still show live increments.
        finality_row = native_terminal_view(row, broker.requests.get(request_id))
        # Only the single authoritative native finality decision may publish.
        # Legacy unverified-final is NOT safe merely because it is not an
        # explicit rejection. Keep in-progress snapshots provisional only;
        # completed requests require a proven final revision and ACK.
        finality_status = native_snapshot_finality(finality_row, snapshot)
        if finality_status not in {"live-provisional", "verified-final"}:
            return None
        # Reuse the *same* archived media records as the canonical Markdown.
        # Never return a ChatGPT blob URL or a model-generated remote URL.
        assets = []
        asset_metadata = []
        for media in (row.get("response_media") or [])[:40]:
            if not isinstance(media, dict):
                continue
            # Standalone image generation may retain an upstream source URL
            # in "url"; the verified, owned MediaArchive URL wins.
            for raw in (media.get("owned_url"), media.get("url")):
                uri = raw if isinstance(raw, str) else ""
                if re.fullmatch(
                    r"https://[^/?#]+(?::[0-9]{1,5})?/api/media/[a-zA-Z0-9_-]{6,100}/[a-zA-Z0-9_-]{8,150}",
                    uri,
                ):
                    assets.append(uri)
                    asset_metadata.append({"width":media.get("width"),
                                           "height":media.get("height"),
                                           "sha256":media.get("sha256")})
                    break
        # Files are derived only from this request's archived final reply.
        # Never forward unverified model-provided or upstream temporary URLs.
        files = []
        pending_archive_pollable = False
        final_text = str(row.get("response_text") or "")
        for item in (row.get("response_generated_files") or [])[:10]:
            if not isinstance(item, dict):
                continue
            url, name = item.get("url"), item.get("filename")
            if (not isinstance(url, str) or not isinstance(name, str)
                or not 1 <= len(name) <= 180 or not 1 <= len(url) <= 500
                or url not in final_text
                or not re.fullmatch(
                    r"https://[^/?#]+(?::[0-9]{1,5})?/api/generated-files/"
                    r"(?:pending/pgen_[a-f0-9]{32}/[a-zA-Z0-9_-]{16,150}"
                    r"|[a-zA-Z0-9_-]{6,100}/[a-zA-Z0-9_-]{8,150})", url
                )):
                continue
            status = str(item.get("status") or "")
            size = item.get("bytes", item.get("size"))
            if (status not in {"processing", "archived", "ready"}
                or type(size) is not int or not 0 <= size <= 24 * 1024 * 1024):
                continue
            # GeneratedFileArchive.archive_reply stores the original bytes in
            # a committed transaction before emitting its permanent file URL
            # with status="archived". The old native sidecar mapped every
            # "archived" record to "processing" forever; unlike the deferred
            # pending-URL workflow, these records are ALREADY downloadable and
            # have no manifest polling path. Normalize only permanent,
            # syntactically validated archive URLs. An inconsistent
            # pending URL marked archived must NOT be treated as ready.
            if status == "archived" and "/pending/" not in url:
                status = "ready"
            # File descriptors are immutable after the OpenAI terminal, but
            # the deferred archive continues running. Resolve the real state
            # from its durable SQLite manifest on each authenticated read.
            if status == "processing" and "/pending/" in url:
                pending_id, pending_token = url.rsplit("/", 2)[-2:]
                try:
                    # SQLite can wait for a concurrent archive writer. Do
                    # not block unrelated API requests or SSE heartbeats on
                    # the event loop while reading the durable file state.
                    pending_state = (await asyncio.to_thread(
                        deferred_generated_files.status, pending_id, pending_token,
                    )).get("status")
                    if pending_state == "ready":
                        status = "ready"
                    elif pending_state == "failed":
                        status = "failed"
                    elif pending_state in {"processing", "pending"}:
                        # Only a successfully checked durable manifest may
                        # extend the non-authoritative sidecar lifecycle.
                        # Missing/locked SQLite must not keep a socket open.
                        pending_archive_pollable = True
                except (KeyError, OSError, sqlite3.Error):
                    # SQLite can raise OperationalError on locked/unavailable
                    # storage. A failed optional sidecar status read must not
                    # turn a completed OpenAI request into an HTTP 500 or
                    # terminate native SSE. Never invent a ready file.
                    pass
            file_descriptor = {
                "filename": name, "url": url, "bytes": size,
                "status": status if status in {"ready", "failed"} else "processing",
            }
            mime_type = item.get("mime_type")
            if isinstance(mime_type, str) and mime_type:
                file_descriptor["mime_type"] = mime_type
            files.append(file_descriptor)
        # Legacy native v1 clients index assets directly by img.media_index.
        # A candidate report alone is insufficient: extra archived images
        # would otherwise make them draw the WRONG picture at each position.
        # Return a request-owned, presentation-ordered subset only when all
        # layout images have one unique dimension match. With no safe mapping,
        # supply zero image URLs and let the consumer fall back to Markdown.
        candidates = native_image_identity_candidates(snapshot, asset_metadata)
        if candidates["status"] == "verified-sha256":
            visible_assets = [assets[index] for index in candidates["indices"]]
        elif candidates["status"] == "identity-incomplete":
            # A partial/contradictory byte proof cannot be replaced by a
            # dimension or ordinal guess, even when counts happen to match.
            visible_assets = []
        else:
            # Ordinal/dimension equality cannot prove original image byte
            # identity. Never inject a potentially wrong asset into the v1
            # layout; retain canonical Markdown until every raster is proven.
            visible_assets = []
        result = {**snapshot,
                  "root": snapshot["root"] if preserve_native_tree else native_public_v1_tree(snapshot["root"]),
                  "assets": visible_assets, "files": files,
                  "image_asset_candidates": candidates}
        # The Web Chat renderer consumes the full, already validated native
        # tree (including safe SVG attributes and MathML tags). The legacy
        # public v1 endpoint keeps its compatibility projection unchanged.
        if preserve_native_tree:
            result["finality"] = finality_status
        # Internal loop-only flag; do not change strict public native v1
        # schema or expose file transport internals to external clients.
        if watch_pending_archive:
            result["_pending_archive_pollable"] = pending_archive_pollable
        return result

    # The user-session Web Chat adapter reuses this exact read/finality and
    # archived-media authority; it does not create a second native store.
    app.state.native_response_snapshot = native_response_snapshot

    @app.get("/v1/native-content/{request_id}")
    async def read_native_content(
        request_id: str, principal: ApiPrincipal = Depends(require_api_key),
    ) -> Response:
        owner = native_owner(request_id, principal)
        try:
            snapshot = await native_response_snapshot(request_id, owner)
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
            last = None
            # The sidecar may arrive after the client subscribes. No terminal
            # authority or worker lease is owned by this stream.
            for tick in range(3600):
                try:
                    current = await native_response_snapshot(
                        request_id, owner, watch_pending_archive=True)
                    pending_archive_pollable = bool(
                        current and current.pop("_pending_archive_pollable", False))
                    # Archival can finish *after* the last DOM revision. The
                    # newly verified media URLs must be delivered even when the
                    # Worker produced no further layout mutation.
                    signature = native_delivery_fingerprint(current)
                    if signature is not None and signature != last:
                        last = signature
                        yield "event: native.snapshot\n" + "data: " + json.dumps(current, ensure_ascii=False) + "\n\n"
                    row = telemetry.get(request_id) or {}
                    status = row.get("status")
                    if status in {"completed", "error", "cancelled", "stalled"}:
                        # Broker completion does NOT imply async generated-file
                        # archive is ready. A verified 'processing' manifest
                        # keeps only this presentation SSE alive (max 3min),
                        # allowing same-revision ready/failed updates. A
                        # manifest query failure or non-completed terminal
                        # closes immediately, preserving previous behavior.
                        if not (status == "completed" and pending_archive_pollable
                                and tick < 720):
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

    @app.get("/api/admin/requests/{request_id}/native-content", dependencies=[Depends(require_admin_key)])
    async def admin_native_content_evidence(request_id: str) -> Response:
        # Privileged visual evidence for a specific request. The tree is already
        # schema-validated and bounded; never expose credential-bearing asset
        # URLs in an exported layout diagnostic.
        row = telemetry.get(request_id)
        if not row or not row.get("api_key_id"):
            raise HTTPException(status_code=404, detail="Unknown request")
        try:
            snapshot = native_content.get(request_id, str(row["api_key_id"]))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No native layout evidence")
        archived = row.get("response_media") or []
        details = [
            {key: media[key] for key in ("id", "mime", "width", "height", "sha256") if key in media}
            for media in archived[:40] if isinstance(media, dict)
        ]
        # Transport ACK can prove a validated snapshot was persisted; it
        # cannot prove visual fidelity when Canvas/iframe was omitted. Expose
        # bounded request-only counts for the privileged preview.
        recorded = row.get("diagnostics") or {}
        if not isinstance(recorded, dict):
            recorded = {}
        # Private preview may inspect a proven stale revision, but its
        # finality must be visible, never silently claimed as 1:1.
        finality_row = native_terminal_view(row, broker.requests.get(request_id))
        finality = native_snapshot_finality(finality_row, snapshot)
        def gap_count(key: str) -> int:
            value = recorded.get(key, 0)
            return max(0, min(30000, value)) if type(value) is int else 0
        layout = native_layout_summary(snapshot, len(details))
        image_candidates = native_image_identity_candidates(snapshot, details)
        # Historical req_20a00da had one 12x12 decorative forensic IMG
        # but zero native IMG nodes: simple 1!=0 counts were a false alarm.
        # Only compare displayed >=40x40 images within a complete, trusted
        # request-owned private DOM capture. No raw DOM/user text is returned.
        forensic = native_dom_evidence.get(request_id, str(row["api_key_id"]))
        comparison = capture_comparison(
            forensic, layout, len(details), image_candidates,
        )
        return JSONResponse(
            {"request_id": request_id, "version": 1, "snapshot": snapshot,
             "layout_summary": layout,
             "media_metadata": details,
             "image_asset_candidates": image_candidates,
             "capture_comparison": comparison,
             "snapshot_finality": finality,
             "visual_gaps": {"unsupported_graphics": gap_count("native_graphics_unsupported"),
                             "canvas_omitted": gap_count("native_canvas_omitted"),
                             "svg_captured": gap_count("native_svg_charts_captured"),
                             "visual_completeness_verified": False}},
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/admin/requests/{request_id}/native-dom-evidence", dependencies=[Depends(require_admin_key)])
    async def admin_request_native_dom_evidence(request_id: str) -> Response:
        row=telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Unknown request")
        try:
            evidence=native_dom_evidence.get(request_id,str(row.get("api_key_id") or ""))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid request ID") from None
        if evidence is None:
            raise HTTPException(status_code=404, detail="Native DOM evidence unavailable")
        return JSONResponse({"request_id":request_id,"native_dom_evidence":evidence},
                            headers={"Cache-Control":"private, no-store",
                                     "X-Content-Type-Options":"nosniff"})

    @app.get("/api/admin/requests/{request_id}/probes/download", dependencies=[Depends(require_admin_key)])
    async def request_probe_download(request_id: str) -> Response:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Unknown request")
        try:
            archive = worker_probes.archive(
                request_id, row,
                native_snapshot=native_content.get(request_id, str(row.get("api_key_id") or "")),
                native_dom_evidence=native_dom_evidence.get(request_id, str(row.get("api_key_id") or "")),
            )
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
        source: str | None = Query(default=None, pattern="^(playground|external)$"),
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
        test_index = request_test_index(test_runs, limit=test_runs.items.maxlen or 300)
        test_ids = set(test_index)
        result = telemetry.query(
            limit=limit, offset=offset, status=status_filter, model=model,
            key_id=key_id, q=q, since=cutoff, source=source,
            playground_request_ids=test_ids,
            current_version_only=current_version_only,
            server_version=SERVER_RUNTIME_VERSION,
            server_commit=active_commit,
        )
        # Enrich only the display copies. Do not rewrite persisted requests,
        # shorten real IDs on the server, or infer a scene from prompt text.
        rows = result.get("data") or []
        result["data"] = [
            {**row, **playground_link(test_index.get(str(row.get("request_id") or "")))}
            for row in rows
        ]
        return {**result, "summary": telemetry.summary(),
                "current_version_commit": active_commit if current_version_only else None,
                # Echo the *applied*, normalized UTC cutoff so the UI can
                # reject stale/unfiltered responses, not silently show old rows.
                "applied_filters": {
                    "since": cutoff.isoformat() if cutoff is not None else None,
                    "current_version_only": current_version_only,
                    "q": q, "status": status_filter, "model": model,
                    "source": source,
                }}

    @app.get("/api/admin/requests/{request_id}", dependencies=[Depends(require_admin_key)])
    async def admin_request_detail(request_id: str) -> dict[str, object]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        # Terminal records snapshot file processing. A later admin view must
        # query the owner-token-bound status, not assume the old state is live.
        current = dict(row)
        files = row.get("response_generated_files")
        if isinstance(files, list):
            live = []
            for item in files[:10]:
                if not isinstance(item, dict):
                    continue
                updated = dict(item)
                url = str(updated.get("url") or "")
                matched = re.search(
                    r"/api/generated-files/pending/(pgen_[a-f0-9]{32})/([A-Za-z0-9_-]{16,150})$",
                    url,
                )
                if matched:
                    try:
                        actual = await asyncio.to_thread(
                            deferred_generated_files.status, matched.group(1), matched.group(2),
                        )
                        status = actual.get("status")
                        if status in {"ready", "processing", "failed"}:
                            updated["status"] = status
                    except (KeyError, ValueError, OSError, sqlite3.Error):
                        # DeferredGeneratedFiles.status() reads a shared SQLite
                        # manifest while archive workers may hold write locks.
                        # A busy/corrupt manifest is UNKNOWN, not a reason to
                        # return an unrelated HTTP 500 from request history,
                        # nor evidence to mark an unfinished file "ready".
                        updated["status"] = "unavailable"
                live.append(updated)
            current["response_generated_files"] = live
            current["response_generated_files_live_checked"] = True
        return current

    def active_playground_run_ids() -> set[str]:
        manager = getattr(app.state, "playground_run_manager", None)
        tasks = getattr(manager, "tasks", None)
        return set(tasks) if isinstance(tasks, dict) else set()

    @app.get("/api/admin/tests", dependencies=[Depends(require_admin_key)])
    async def admin_tests(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
        await reconcile_playground_runs(
            test_runs, telemetry, limit=min(200, limit),
            active_run_ids=active_playground_run_ids(),
        )
        return {"data": [present_playground_run(row) for row in test_runs.recent(limit)]}

    @app.get("/api/admin/tests/{run_id}", dependencies=[Depends(require_admin_key)])
    async def admin_test_detail(run_id: str) -> dict[str, object]:
        await reconcile_playground_runs(
            test_runs, telemetry, limit=200,
            active_run_ids=active_playground_run_ids(),
        )
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

    def standard_file_object(item) -> dict[str, object]:
        try:
            created_at = int(datetime.fromisoformat(str(item.created_at).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError, OverflowError):
            created_at = int(time.time())
        return {
            "id": item.file_id,
            "object": "file",
            "bytes": int(item.size),
            "created_at": created_at,
            "filename": item.filename,
            "purpose": item.purpose,
            "status": "processed",
            "status_details": None,
            "mime_type": item.mime_type,
            "created_at_iso": item.created_at,
        }

    @app.post(
        "/v1/files",
        openapi_extra={"requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {"schema": {
                    "type": "object", "required": ["file", "purpose"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "purpose": {"type": "string", "enum": ["assistants", "vision", "user_data"]},
                    },
                }},
                "application/json": {"schema": {
                    "type": "object", "required": ["filename", "data_base64"],
                    "properties": {
                        "filename": {"type": "string"},
                        "data_base64": {"type": "string"},
                        "mime_type": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                }},
            },
        }},
    )
    async def upload_file(request: Request, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        content_type = str(request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            try:
                form = await request.form()
            except Exception as error:
                raise HTTPException(status_code=400, detail="Invalid multipart file upload") from error
            upload = form.get("file")
            if upload is None or not callable(getattr(upload, "read", None)):
                raise HTTPException(status_code=422, detail="Multipart upload requires a file field")
            purpose = str(form.get("purpose") or "").strip()
            if purpose not in {"assistants", "vision", "user_data"}:
                raise HTTPException(status_code=400, detail="Unsupported file purpose; use assistants, vision, or user_data")
            filename = str(getattr(upload, "filename", "") or "upload.bin")
            mime_type = str(getattr(upload, "content_type", "") or "application/octet-stream")
            try:
                payload = await upload.read(file_store.max_file_bytes + 1)
            finally:
                close = getattr(upload, "close", None)
                if callable(close):
                    await close()
            if not payload:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            if len(payload) > file_store.max_file_bytes:
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured size limit")
            try:
                body = FileUploadRequest(
                    filename=filename,
                    data_base64=base64.b64encode(payload).decode("ascii"),
                    mime_type=mime_type,
                    purpose=purpose,
                )
            except ValidationError as error:
                # Multipart form metadata must fail like any other /v1 input,
                # not bubble out as an unhandled Pydantic exception / HTTP 500.
                # Do not include arbitrary uploaded filenames in error text.
                raise HTTPException(status_code=422, detail="Invalid multipart file upload metadata") from error
            standard_response = True
        elif content_type.startswith("application/json"):
            try:
                body = FileUploadRequest.model_validate(await request.json())
            except (ValueError, TypeError, ValidationError) as error:
                raise HTTPException(status_code=422, detail="Invalid JSON file upload request") from error
            standard_response = False
        else:
            raise HTTPException(status_code=415, detail="Use multipart/form-data or the Chat2API JSON upload format")
        try:
            upload_source = (
                "web_chat"
                if str(request.headers.get("x-chat2api-source") or "").strip().lower() == "web-chat"
                else "api"
            )
            item = await file_store.create(
                filename=body.filename,
                data_base64=body.data_base64,
                mime_type=body.mime_type,
                owner_key_id=principal.key_id,
                purpose=body.purpose,
                source=upload_source,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return standard_file_object(item) if standard_response else item.public()

    @app.get("/v1/files/{file_id}")
    async def file_metadata(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        item = file_store.get(file_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="File belongs to another API key")
        return standard_file_object(item)

    @app.get(
        "/v1/files/{file_id}/content",
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    async def file_content(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> Response:
        require_scope(principal, "files")
        item = file_store.get(file_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown file_id")
        if principal.kind != "master" and item.owner_key_id != principal.key_id:
            raise HTTPException(status_code=403, detail="File belongs to another API key")
        try:
            _, payload = await asyncio.to_thread(file_store.read, file_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=410, detail="Stored file content is missing") from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown file_id") from error
        ascii_filename = re.sub(r"[^A-Za-z0-9._-]", "_", item.filename).strip("._") or "download"
        encoded_filename = quote(item.filename, safe="")
        return Response(payload, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        })

    @app.delete("/v1/files/{file_id}")
    async def delete_file(file_id: str, principal: ApiPrincipal = Depends(require_api_key)) -> dict[str, object]:
        require_scope(principal, "files")
        conversations = getattr(app.state, "web_chat_conversations", None)
        with file_store.lock:
            if conversations is not None:
                with conversations.file_deletion_guard(file_id) as can_delete:
                    if not can_delete:
                        raise HTTPException(
                            status_code=409,
                            detail="File is referenced by a saved Web Chat conversation; delete or update that conversation first",
                        )
                    try:
                        await file_store.delete(file_id, None if principal.kind == "master" else principal.key_id)
                    except KeyError as error:
                        raise HTTPException(status_code=404, detail=str(error)) from error
                    except PermissionError as error:
                        raise HTTPException(status_code=403, detail=str(error)) from error
            else:
                try:
                    await file_store.delete(file_id, None if principal.kind == "master" else principal.key_id)
                except KeyError as error:
                    raise HTTPException(status_code=404, detail=str(error)) from error
                except PermissionError as error:
                    raise HTTPException(status_code=403, detail=str(error)) from error
        return {"id": file_id, "object": "file", "deleted": True}

    @app.get("/api/extensions/files/{file_id}")
    async def extension_file(
        file_id: str, request_id: str = Query(..., min_length=6, max_length=120),
        client_id: str = Query(...), token: str = Query(...),
    ) -> Response:
        if not await registry.authenticate(client_id, token):
            raise HTTPException(status_code=401, detail="Invalid extension token")
        row = telemetry.get(request_id)
        item = file_store.get(file_id)
        active = getattr(broker, "requests", {}).get(request_id)
        allowed_ids = row.get("input_file_ids") if isinstance(row, dict) else None
        dispatch_socket = getattr(active, "dispatch_socket", None)
        sockets = getattr(registry, "sockets", None)
        current_socket = sockets.get(client_id) if isinstance(sockets, dict) else None
        if (
            not isinstance(row, dict)
            or active is None
            or dispatch_socket is None
            or current_socket is not dispatch_socket
            or str(getattr(active, "client_id", "") or "") != client_id
            or getattr(active, "terminal_event_type", None) is not None
            or getattr(active, "completed_mono", None) is not None
            or str(row.get("status") or "") != "running"
            or str(row.get("client_id") or "") != client_id
            or not item
            or (
                str(row.get("api_key_id") or "") != "master"
                and str(row.get("api_key_id") or "") != str(item.owner_key_id or "")
            )
            or not isinstance(allowed_ids, list)
            or file_id not in {str(value) for value in allowed_ids}
        ):
            raise HTTPException(status_code=404, detail="Unknown attachment")
        try:
            item, payload = file_store.read(file_id)
        except FileIntegrityError as error:
            raise HTTPException(status_code=503, detail="Stored file content failed integrity verification") from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=503, detail="Stored file content is temporarily unavailable") from error
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

    def attachment_specs(
        file_ids: list[str], principal: ApiPrincipal, *, request_id: str | None = None,
    ) -> list[dict[str, object]]:
        rows = canonical_attachment_specs(file_store, file_ids, principal)
        if not request_id:
            return rows
        # The worker download grant is bound to the exact request, API-key owner
        # and assigned Worker. It is not a reusable file-id capability.
        return [{**row, "request_id": request_id} for row in rows]

    async def inline_image_files(body: ChatCompletionRequest, principal: ApiPrincipal) -> list[str]:
        # Apply one request-wide attachment budget across existing image IDs,
        # document IDs and inline data URLs before allocating any new objects.
        existing_file_ids = body.all_file_ids()
        inline_data_count = sum(
            1
            for message in body.messages
            if isinstance(message.content, list)
            for part in message.content
            if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}
            for raw_image_value in [part.get("image_url")]
            for image_value in [raw_image_value.get("url") if isinstance(raw_image_value, dict) else raw_image_value]
            if isinstance(image_value, str) and image_value.startswith("data:")
        )
        if len(existing_file_ids) + inline_data_count > 4:
            raise HTTPException(status_code=400, detail="At most four image/file inputs are supported per request")
        existing_specs = attachment_specs(existing_file_ids, principal) if existing_file_ids else []
        total_bytes = sum(int(item.get("size") or 0) for item in existing_specs)
        if total_bytes > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Combined image/file inputs may not exceed 20 MiB")
        seen_file_ids = set(existing_file_ids)
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
                    try:
                        validate_image_file(file_store, value, principal)
                    except ValueError as error:
                        raise HTTPException(status_code=400, detail=str(error)) from error
                    result.append(value)
                    continue
                if not value.startswith("data:"):
                    raise HTTPException(
                        status_code=400,
                        detail="Remote image URLs are not fetched for security. Use a base64 data URL or upload via POST /v1/files.",
                    )
                if len(seen_file_ids) >= 4:
                    raise HTTPException(status_code=400, detail="At most four image/file inputs are supported per request")
                sequence += 1
                try:
                    image_bytes, image_mime = file_store._decode(value, None)
                    verify_raster_image(image_bytes, image_mime)
                    if len(image_bytes) > 10 * 1024 * 1024:
                        raise ValueError("input_image exceeds the 10 MiB per-image limit")
                    if total_bytes + len(image_bytes) > 20 * 1024 * 1024:
                        raise ValueError("Combined image/file inputs may not exceed 20 MiB")
                    item = await file_store.create(
                        filename=f"inline-image-{sequence}.{image_mime.split('/', 1)[-1]}",
                        data_base64=base64.b64encode(image_bytes).decode("ascii"),
                        mime_type=image_mime,
                        owner_key_id=principal.key_id,
                        purpose="vision",
                    )
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                result.append(item.file_id)
                seen_file_ids.add(item.file_id)
                total_bytes += len(image_bytes)
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
        # The Worker/Broker alone decides completion. Post-terminal media
        # writes must also have one owner: if SSE/HTTP disconnects during a
        # to_thread archive, the thread keeps running. Detached finalization
        # therefore REUSES exactly the same shielded archive task; otherwise
        # it can write each media asset twice for a single Worker terminal.
        async def run_verified_archive():
            # Cancelling asyncio.to_thread() does not cancel its worker thread.
            # Signal that thread to stop *future* writes and compensate any
            # in-flight image writes at the next safe checkpoint.
            stop = ArchiveWriteStop()
            state.diagnostics["server_finalization_stage"] = "archive-started"
            state.diagnostics["server_finalization_started_at"] = time.time()
            await telemetry.upsert({
                "request_id": request_id,
                "diagnostics": dict(state.diagnostics),
            })
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        archive_final_markdown, text, key_id, request_id, base_url, stop
                    ),
                    timeout=180.0,
                )
            except asyncio.TimeoutError as error:
                stop.stop()
                state.diagnostics["server_finalization_cooperative_stop_signaled"] = True
                state.diagnostics["server_finalization_stage"] = "archive-timeout"
                state.diagnostics["server_finalization_elapsed_ms"] = round(
                    (time.time() - state.diagnostics["server_finalization_started_at"]) * 1000
                )
                raise ValueError("Post-terminal media archival exceeded 180 seconds") from error
            except asyncio.CancelledError:
                # Server shutdown / archive-task cancellation is distinct
                # from an HTTP disconnect, which archive_once already shields.
                stop.stop()
                state.diagnostics["server_finalization_cooperative_stop_signaled"] = True
                raise
            except Exception:
                state.diagnostics["server_finalization_stage"] = "archive-error"
                raise
            state.diagnostics["server_finalization_stage"] = "archive-finished"
            state.diagnostics["server_finalization_elapsed_ms"] = round(
                (time.time() - state.diagnostics["server_finalization_started_at"]) * 1000
            )
            return result

        return await archive_once(
            state, request_id=request_id, owner=key_id, final_text=text,
            base_url=base_url, run=run_verified_archive,
        )

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
        delivery_text: str | None = None,
        error: str | None = None,
        request_type: str = "text",
        attachments_count: int = 0,
        media_items: list[dict[str, object]] | None = None,
        file_items: list[dict[str, object]] | None = None,
        usage_override: dict[str, object] | None = None,
        prompt_chars_override: int | None = None,
        timings_override: dict[str, object] | None = None,
        verify_replayed_usage_settlement: bool = False,
    ) -> tuple[dict[str, int], dict[str, object]]:
        if usage_override is None:
            usage, token_meta = standard_usage(prompt, final_text)
        else:
            counts: dict[str, int] = {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage_override.get(field)
                if type(value) is not int or value < 0:
                    raise ValueError("Recovered usage contains an invalid token count")
                counts[field] = value
            if counts["total_tokens"] != counts["prompt_tokens"] + counts["completion_tokens"]:
                raise ValueError("Recovered usage token totals do not reconcile")
            usage = counts
            token_meta = {"estimated": bool(usage_override.get("estimated", True))}
            estimator = str(usage_override.get("estimator") or "").strip()[:80]
            if estimator:
                token_meta["estimator"] = estimator
        prompt_chars = len(prompt)
        if prompt_chars_override is not None:
            if type(prompt_chars_override) is not int or prompt_chars_override < 0:
                raise ValueError("Recovered prompt character count is invalid")
            prompt_chars = prompt_chars_override
        timings = dict(timings_override) if isinstance(timings_override, dict) else state.timings()
        state.diagnostics["server_finalization_stage"] = "request-row-finalized"
        # Diagnostics only: bounded structural evidence for A/B/C/D visual
        # comparisons. Do not export DOM HTML, CSS text, URLs or image bytes.
        native = native_content.get(state.request_id, principal.key_id)
        # The old request ZIP reported 960 private DOM nodes / 4 archived
        # images yet *no* native layout summary. Preserve that distinction:
        # a complete independent DOM probe does not imply a published
        # native-rich snapshot, let alone verified raster identity.
        state.diagnostics["native_snapshot_state"] = (
            "published" if native else "missing"
        )
        # Published/persisted can describe an old incremental revision:
        # 3a882b retained revision 12 (333 nodes) although the final capture
        # failed max-children. Do not report that as complete final content.
        state.diagnostics["native_snapshot_terminal_complete"] = bool(
            native
            and state.diagnostics.get("native_snapshot_persisted") is True
            and state.diagnostics.get("native_final_capture_committed") is True
            and state.diagnostics.get("native_chunk_complete") is True
            and not state.diagnostics.get("native_capture_failure")
            and native.get("revision") == state.diagnostics.get("native_capture_revision")
        )
        state.diagnostics["native_chunk_assembly"] = native_chunks.progress(
            state.request_id, principal.key_id
        )
        if native:
            items = [item for item in (media_items or [])[:40]
                     if isinstance(item, dict)]
            summary = native_layout_summary(native, len(items))
            identity = native_image_identity_candidates(native, items)
            # Non-authoritative, size-qualified comparison of independent DOM
            # observation against actual native payload. A later forensic
            # report can still be inspected via the live admin endpoint.
            state.diagnostics["native_capture_comparison"] = capture_comparison(
                native_dom_evidence.get(state.request_id, principal.key_id),
                summary, len(items), identity,
            )
            state.diagnostics["native_layout_summary"] = summary
            state.diagnostics["native_image_identity_status"] = identity["status"]
            state.diagnostics["native_identity_matched_count"] = (
                len(identity.get("indices") or [])
                if identity.get("status") == "verified-sha256"
                else sum(i is not None for i in identity.get("indices") or ())
                    if identity.get("status") == "verified-sha256-partial"
                else 0
            )
            state.diagnostics["native_identity_archived_count"] = len(items)
            state.diagnostics["native_identity_dom_image_count"] = summary["images"]
        delivery_journal_ready = False
        if state.mode1_web_chat and state.mode1_owner_key_id:
            try:
                if status_value == "completed":
                    journal_text = final_text if delivery_text is None else delivery_text
                    delivery = await event_journal.append_delivery(
                        state.request_id, state.mode1_owner_key_id, journal_text,
                    )
                    state.delivery_event_ids = [
                        int(value) for value in delivery.get("chunk_event_ids", [])
                    ]
                    delivery_snapshot = await event_journal.snapshot(
                        state.request_id, state.mode1_owner_key_id,
                    )
                    delivery_journal_ready = bool(
                        delivery_snapshot.get("delivered_text") == journal_text
                        and delivery_snapshot.get("delivered_text_truncated") is not True
                        and delivery_snapshot.get("terminal_status") in {"", "completed"}
                    )
                    if not delivery_journal_ready:
                        raise RuntimeError("Durable Web Chat delivery does not match the final projection")
            except Exception as journal_error:
                state.diagnostics["web_chat_event_journal_error"] = type(journal_error).__name__
                logger.exception(
                    "Mode 1 event journal finalization failed request_id=%s",
                    state.request_id,
                )
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "shared_api_key_id": principal.auth_key_id or None,
                "web_chat_mode": bool(state.mode1_web_chat or principal.kind == "web_chat"),
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": prompt_chars,
                "completion_chars": len(final_text),
                "response_media": media_items or [],
                "response_media_count": len(media_items or []),
                "response_generated_files": file_items or [],
                # Persist only the final post-archive owned-media URL, never the
                # browser's several-megabyte raw Base64 transport payload.
                **({"response_text": history_safe_response_text(final_text),
                    "response_text_available": bool(final_text)}
                   if final_text or status_value == "completed" else {}),
                "response_text_provisional": "" if status_value == "completed" else None,
                "status": status_value,
                "probe_summary": worker_probes.summary(state.request_id),
                "probe_capture_status": worker_probes.summary(state.request_id)["capture_status"],
                "usage": {**usage, **token_meta},
                "timings": timings,
                "diagnostics": dict(state.diagnostics),
                "error": error,
            }
        )
        if verify_replayed_usage_settlement:
            completed_row = telemetry.get(state.request_id) or {}
            if not await ensure_replayed_usage_settlement(completed_row):
                raise RuntimeError("Recovered API-key usage settlement was not durably verified")
        # The terminal cursor is appended only after the authoritative
        # telemetry terminal has been written and replay usage has been verified.
        # A crash between the two leaves
        # replayable content but cannot falsely turn a running row into success.
        if state.mode1_web_chat and state.mode1_owner_key_id:
            try:
                if status_value == "completed":
                    journal_text = final_text if delivery_text is None else delivery_text
                    terminal_id = None
                    if delivery_journal_ready:
                        terminal_id = await event_journal.append_delivery_terminal(
                            state.request_id, state.mode1_owner_key_id, len(journal_text),
                        )
                        if not terminal_id:
                            terminal_snapshot = await event_journal.snapshot(
                                state.request_id, state.mode1_owner_key_id,
                            )
                            if (
                                terminal_snapshot.get("terminal_status") == "completed"
                                and terminal_snapshot.get("delivered_text") == journal_text
                                and terminal_snapshot.get("delivered_text_truncated") is not True
                            ):
                                terminal_id = int(terminal_snapshot.get("last_event_id") or 0)
                elif status_value in {"error", "cancelled", "stalled"}:
                    terminal_id = await event_journal.append_terminal(
                        state.request_id, state.mode1_owner_key_id,
                        status_value, error or "",
                    )
                else:
                    terminal_id = None
                state.delivery_terminal_event_id = int(terminal_id or 0)
                state.last_event_id = max(state.last_event_id, state.delivery_terminal_event_id)
            except Exception as journal_error:
                state.diagnostics["web_chat_event_journal_error"] = type(journal_error).__name__
                logger.exception(
                    "Mode 1 event journal terminal marker failed request_id=%s",
                    state.request_id,
                )
        # A manual Playground run is created before the API dispatch. If its
        # browser disappeared (real req_b734ffd3 stalled after 915s), the
        # browser cannot call the terminal-only chat-records endpoint and
        # the Test History incorrectly remains "running" until a separate
        # admin Tests GET. Project the *persisted* Broker terminal onto the
        # matching existing test record now; no second request terminal.
        try:
            await reconcile_playground_request_terminal(
                test_runs, telemetry, state.request_id,
                active_run_ids=active_playground_run_ids(),
            )
        except Exception:
            logger.exception(
                "Cannot reconcile Playground terminal request_id=%s",
                state.request_id,
            )
        # The Broker has now persisted its authoritative terminal; completed
        # chunks were already archived and all other partial staging bytes
        # are no longer eligible to be attached to a future request.
        await asyncio.to_thread(generated_staging.drop_request, state.request_id)
        return usage, token_meta

    async def ensure_replayed_delivery_terminal(
        request_id: str, owner: str, final_text: str,
    ) -> bool:
        """Repair the durable SSE delivery cursor after telemetry already finalized."""
        snapshot = await event_journal.snapshot(request_id, owner)
        terminal_status = str(snapshot.get("terminal_status") or "")
        delivered = snapshot.get("delivered_text")
        if not isinstance(delivered, str):
            return False
        if terminal_status:
            return bool(
                terminal_status == "completed"
                and snapshot.get("delivered_text_truncated") is not True
                and delivered == final_text
            )
        # append_delivery atomically compares the durable prefix and appends
        # only the missing suffix. It is safe to retry after any process crash.
        await event_journal.append_delivery(request_id, owner, final_text)
        verified_delivery = await event_journal.snapshot(request_id, owner)
        if (
            verified_delivery.get("delivered_text") != final_text
            or verified_delivery.get("delivered_text_truncated") is True
        ):
            return False
        await event_journal.append_delivery_terminal(
            request_id, owner, len(final_text),
        )
        verified = await event_journal.snapshot(request_id, owner)
        return bool(
            verified.get("terminal_status") == "completed"
            and verified.get("delivered_text") == final_text
            and verified.get("delivered_text_truncated") is not True
        )

    async def ensure_replayed_usage_settlement(record: dict[str, Any]) -> bool:
        """Retry and prove idempotent usage settlement before ACKing a replay."""
        if (
            not isinstance(record, dict)
            or record.get("status") != "completed"
            or record.get("web_chat_mode") is not True
        ):
            return False
        request_id = str(record.get("request_id") or "")
        key_id = str(record.get("api_key_id") or "")
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        counts: dict[str, int] = {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is not int or value < 0:
                return False
            counts[field] = value
        if counts["total_tokens"] <= 0 or counts["total_tokens"] != (
            counts["prompt_tokens"] + counts["completion_tokens"]
        ):
            return False
        if not request_id or not key_id or key_id == "master":
            return False
        api_key = getattr(api_keys, "keys", {}).get(key_id)
        shared_key_id = str(record.get("shared_api_key_id") or "")
        # New session-backed Web Chat requests must retain the shared service
        # key identity all the way through terminal replay. Only pre-migration
        # Web Chat rows (auth_kind=managed) may use the legacy owner-key ledger.
        if str(record.get("auth_kind") or "") == "web_chat" and not shared_key_id:
            return False
        accounting = getattr(telemetry, "usage_accounting", None)
        if api_key is None or not callable(accounting):
            return False
        # Telemetry is written before its accounting callback. If the callback
        # was interrupted, a duplicate Worker replay must retry that same
        # idempotent request_id charge rather than ACKing a merely completed row.
        await accounting(record)
        # Legacy Web Chat requests used the account-owned API key directly.
        # New Web Chat requests carry shared_api_key_id and charge only that
        # server-managed key. Keep the old path recoverable after an upgrade.
        if not shared_key_id:
            ledger = getattr(api_key, "charged_requests", None)
            if not isinstance(ledger, dict):
                ledger = getattr(api_keys, "charged_requests", None)
            charged = ledger.get(request_id, 0) if isinstance(ledger, dict) else 0
            if type(charged) is not int or charged < counts["total_tokens"]:
                return False
        if shared_key_id:
            shared_key = getattr(api_keys, "keys", {}).get(shared_key_id)
            shared_ledger = getattr(shared_key, "charged_requests", None) if shared_key else None
            if not isinstance(shared_ledger, dict):
                return False
            shared_charged = shared_ledger.get(request_id, 0)
            if type(shared_charged) is not int or shared_charged < counts["total_tokens"]:
                return False

        if getattr(app.state, "quota_reservation_runtime_enabled", False):
            quota_store = getattr(app.state, "quota_reservations", None)
            reservation = quota_store.get(request_id) if quota_store is not None else None
            if reservation:
                if str(reservation.get("key_id") or "") != key_id:
                    return False
                reservation_state = str(reservation.get("state") or "")
                if reservation_state in {"reserved", "committing", "released"}:
                    return False
                if (
                    reservation_state == "settled"
                    and int(reservation.get("actual_tokens") or 0) < counts["total_tokens"]
                ):
                    return False

        # Wallet billing is a second durable ledger, separate from API-key
        # token usage. A telemetry write can succeed while record_charge fails;
        # on the duplicate Worker replay, retry that exact owner's wallet hold
        # before acknowledging completion. Never infer or backfill a charge
        # when no admission-time wallet reservation exists.
        accounts = getattr(app.state, "user_accounts", None)
        billing = getattr(app.state, "user_billing", None)
        owner_for_key = getattr(accounts, "owner_for_key", None)
        if billing is not None and callable(owner_for_key):
            wallet_owner = owner_for_key(key_id)
            if wallet_owner:
                reservation_reader = getattr(billing, "reservation_for_request", None)
                charge_reader = getattr(billing, "charge_for_request", None)
                if not callable(reservation_reader) or not callable(charge_reader):
                    return False
                wallet_reservation = reservation_reader(request_id)
                if wallet_reservation:
                    if str(wallet_reservation.get("user_id") or "") != wallet_owner:
                        return False
                    wallet_state = str(wallet_reservation.get("state") or "")
                    if wallet_state == "reserved":
                        reconcile_wallet = getattr(app.state, "wallet_charge_reconciler", None)
                        if not callable(reconcile_wallet):
                            return False
                        await asyncio.to_thread(reconcile_wallet, wallet_owner)
                        wallet_reservation = reservation_reader(request_id)
                        wallet_state = str((wallet_reservation or {}).get("state") or "")
                    if wallet_state == "settled":
                        wallet_charge = charge_reader(request_id)
                        if (
                            not isinstance(wallet_charge, dict)
                            or str(wallet_charge.get("user_id") or "") != wallet_owner
                            or wallet_charge.get("debited") is not True
                        ):
                            return False
                    elif wallet_state == "released":
                        # These are the only legitimate no-charge admission
                        # markers. A positive hold released before a completed
                        # response is a conflicting terminal and must not ACK.
                        if (
                            type(wallet_reservation.get("reserved_cny_cents")) is not int
                            or wallet_reservation.get("reserved_cny_cents") != 0
                            or wallet_reservation.get("release_reason") not in {
                                "billing_disabled_at_admission", "zero_price_at_admission",
                            }
                        ):
                            return False
                    else:
                        return False
                else:
                    existing_charge = charge_reader(request_id)
                    if existing_charge and str(existing_charge.get("user_id") or "") != wallet_owner:
                        return False

        # A failed first finalization may have retained a provisional copy after
        # the completed row was committed. Clear it only after ledger proof.
        await telemetry.upsert({
            "request_id": request_id,
            "response_text_provisional": "",
        })
        confirmed = telemetry.get(request_id) or {}
        return bool(
            confirmed.get("status") == "completed"
            and confirmed.get("response_text_available") is True
            and confirmed.get("response_text_provisional", "") == ""
        )

    def media_base_for_websocket(websocket: WebSocket) -> str:
        base_url = str(websocket.base_url)
        if base_url.startswith("ws://"):
            base_url = "http://" + base_url[5:]
        elif base_url.startswith("wss://"):
            base_url = "https://" + base_url[6:]
        return effective_media_base_url(
            base_url,
            websocket.headers.get("origin"),
            config.public_url,
        )

    async def finalize_replayed_chat_completion(
        websocket: WebSocket, recovered_row: dict[str, Any],
        message: dict[str, Any], client_id: str,
    ) -> bool:
        """Run the normal archive + usage + delivery finalizer for a durable replay."""
        request_id = str(recovered_row.get("request_id") or message.get("request_id") or "")
        owner = str(recovered_row.get("api_key_id") or "")
        candidate = message.get("text")
        if (
            not request_id or not owner or owner == "master"
            or not isinstance(candidate, str) or not candidate
            or len(candidate) > 8_000_000
            or "<chat2api-truncated>" in candidate
            or str(recovered_row.get("status") or "").lower() != "recovering"
        ):
            return False

        original_usage = recovered_row.get("usage")
        original_usage = original_usage if isinstance(original_usage, dict) else {}
        prompt_tokens = original_usage.get("prompt_tokens")
        if type(prompt_tokens) is not int or prompt_tokens < 0:
            return False
        requested_model = str(recovered_row.get("requested_model") or "").strip()
        response_id = str(recovered_row.get("response_id") or "").strip()
        prompt_chars = recovered_row.get("prompt_chars")
        if (
            not requested_model or not response_id
            or type(prompt_chars) is not int or prompt_chars < 0
        ):
            return False
        timings = recovered_row.get("timings")
        timings = dict(timings) if isinstance(timings, dict) else {}
        raw_scopes = recovered_row.get("scopes")
        scopes = (
            tuple(value for value in raw_scopes if isinstance(value, str))
            if isinstance(raw_scopes, (list, tuple)) else ("chat",)
        )
        if not scopes:
            scopes = ("chat",)

        state = RequestState(request_id=request_id, client_id=client_id)
        state.mode1_web_chat = True
        state.mode1_owner_key_id = owner
        state.terminal_event_type = "chat.completed"
        state.completed_mono = time.perf_counter()
        state.diagnostics = (
            dict(recovered_row.get("diagnostics"))
            if isinstance(recovered_row.get("diagnostics"), dict) else {}
        )
        state.diagnostics.update({
            "web_chat_request_recovery": "worker-terminal-replayed-after-broker-restart",
            "web_chat_terminal_replay_type": "chat.completed",
            "web_chat_terminal_replay_reconciled": "full-finalization",
            "web_chat_terminal_replay_received_at": time.time(),
            "worker_transport_replayed": True,
            "worker_transport_recovery": "transport-outbox-v47",
            "server_finalization_recovered_after_restart": True,
        })
        native_persisted = _native_snapshot_persist_best_effort(
            native_content, request_id, owner, state.diagnostics,
        )
        state.diagnostics["native_snapshot_persisted"] = bool(
            native_persisted or state.diagnostics.get("native_snapshot_persisted") is True
        )

        # A replayed terminal is still Worker-owned completion evidence. Only
        # after the full text has been archived into request-owned storage may
        # the API publish completed status and settle quota/wallet reservations.
        archived_text, media_items, file_items = await archive_verified_terminal(
            state, candidate, owner, request_id, media_base_for_websocket(websocket),
        )
        display_text = web_chat_delivery_text(archived_text, candidate, message)
        # The SSE journal retains at most two million final-text characters.
        # Check after media/file rewriting so large inline Base64 assets can be
        # archived into compact owned URLs before applying the delivery bound.
        if len(archived_text) > 2_000_000:
            raise ValueError("Recovered response exceeds the durable SSE delivery limit")
        completion_usage, token_meta = standard_usage("", archived_text)
        completion_tokens = completion_usage.get("completion_tokens")
        if type(completion_tokens) is not int or completion_tokens < 0:
            raise ValueError("Recovered completion usage estimate is invalid")
        usage_override = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            **token_meta,
        }
        principal = ApiPrincipal(
            key_id=owner,
            name=str(recovered_row.get("api_key_name") or "Web Chat"),
            kind=str(recovered_row.get("auth_kind") or "managed"),
            scopes=scopes,
            auth_key_id=str(recovered_row.get("shared_api_key_id") or ""),
        )
        await record_request(
            state,
            requested_model,
            "",
            "completed",
            principal,
            stream=bool(recovered_row.get("stream", True)),
            prompt_mode=str(recovered_row.get("prompt_mode") or "auto"),
            response_id=response_id,
            final_text=archived_text,
            delivery_text=display_text,
            request_type=str(recovered_row.get("request_type") or "web_chat"),
            attachments_count=(
                recovered_row.get("attachments_count")
                if type(recovered_row.get("attachments_count")) is int
                and recovered_row.get("attachments_count") >= 0 else 0
            ),
            media_items=media_items,
            file_items=file_items,
            usage_override=usage_override,
            prompt_chars_override=prompt_chars,
            timings_override=timings,
            verify_replayed_usage_settlement=True,
        )
        if not await ensure_replayed_delivery_terminal(request_id, owner, display_text):
            raise RuntimeError("Recovered SSE delivery terminal was not durably journaled")
        return True

    async def record_request_queued(
        *, request_id: str, response_id: str, requested_model: str, prompt: str,
        principal: ApiPrincipal, stream: bool, prompt_mode: str,
        request_type: str, attachments_count: int, input_file_ids: list[str],
        web_chat_mode: bool = False,
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
            "shared_api_key_id": principal.auth_key_id or None,
            "web_chat_mode": bool(web_chat_mode or principal.kind == "web_chat"),
            "api_key_name": principal.name,
            "auth_kind": principal.kind,
            "requested_model": requested_model,
            "request_type": request_type,
            "attachments_count": attachments_count,
            "input_file_ids": list(dict.fromkeys(str(value) for value in input_file_ids if value)),
            "stream": stream,
            "prompt_mode": prompt_mode,
            "prompt_chars": len(prompt),
            "completion_chars": 0,
            "status": "queued",
            "usage": {**usage, **token_meta},
            "timings": {},
            "diagnostics": {"request_admission_stage": "selecting-worker"},
            "error": None,
        }, create_only=True)
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
        input_file_ids: list[str],
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        await telemetry.upsert(
            {
                "request_id": state.request_id,
                "response_id": response_id,
                "client_id": state.client_id,
                "api_key_id": principal.key_id,
                "shared_api_key_id": principal.auth_key_id or None,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "input_file_ids": list(dict.fromkeys(str(value) for value in input_file_ids if value)),
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": "running",
                "web_chat_mode": bool(state.mode1_web_chat or principal.kind == "web_chat"),
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
        status_value: str = "error",
    ) -> None:
        usage, token_meta = standard_usage(prompt, "")
        existing = telemetry.get(request_id) or {}
        diagnostics = dict(existing.get("diagnostics") or {})
        if status_value == "cancelled":
            diagnostics["request_admission_failure_stage"] = diagnostics.get(
                "request_admission_stage", "selecting-worker",
            )
            diagnostics["request_admission_stage"] = "cancelled-before-worker-dispatch"
            diagnostics["request_admission_failure_kind"] = "web_chat_user_cancelled"
            diagnostics["request_admission_wait_ms"] = round(
                (time.perf_counter() - started_mono) * 1000, 1
            )
        elif diagnostics.get("request_admission_stage"):
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
                "shared_api_key_id": principal.auth_key_id or None,
                "api_key_name": principal.name,
                "auth_kind": principal.kind,
                "requested_model": requested_model,
                "request_type": request_type,
                "attachments_count": attachments_count,
                "stream": stream,
                "prompt_mode": prompt_mode,
                "prompt_chars": len(prompt),
                "completion_chars": 0,
                "status": status_value,
                "usage": {**usage, **token_meta},
                "timings": {"total_ms": round((time.perf_counter() - started_mono) * 1000, 1)},
                "diagnostics": diagnostics,
                "error": error,
            },
            create_only=not bool(existing),
        )
        # Early admission failures (e.g. no connected Chrome Worker) have
        # no RequestState or later Worker finalizer. Reconcile that exact
        # previously registered run here as well, not via an invented
        # background Worker terminal.
        try:
            await reconcile_playground_request_terminal(
                test_runs, telemetry, request_id,
                active_run_ids=active_playground_run_ids(),
            )
        except Exception:
            logger.exception(
                "Cannot reconcile early Playground failure request_id=%s",
                request_id,
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


    def archive_final_markdown(text: str, owner: str, request_id: str,
                               base_url: str, stop: ArchiveWriteStop | None = None):
        # Broker retains sole terminal authority. The stop token comes only
        # from the request-owned 180s archive deadline, never from HTTP peer
        # cancellation. All writes check it at safe rollback checkpoints.
        return archive_mixed_reply(
            text, owner, request_id, base_url,
            media_archive=media_archive,
            deferred_generated_files=deferred_generated_files,
            stop=stop,
        )

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
                if event_type == "chat.references":
                    if (request.scope.get("chat2api_mode1_web_chat") is True
                            and isinstance(event.get("references"), list)
                            and event["references"]):
                        reference_delta = {"chat2api_references": event["references"]}
                        if isinstance(event.get("event_id"), int):
                            reference_delta["chat2api_event_id"] = event["event_id"]
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, reference_delta),
                            ensure_ascii=False,
                        ) + "\n\n"
                    continue
                if event_type in {"chat.tool.call", "chat.tool.result"}:
                    # Tool activity is an auxiliary Mode 1 presentation lane.
                    # Do not leak it into ordinary Mode 2 SDK streams, and do
                    # not mix tool arguments/results into assistant text.
                    if request.scope.get("chat2api_mode1_web_chat") is True:
                        tool_delta = {
                            "phase": "call" if event_type == "chat.tool.call" else "result",
                            "tool_name": event.get("tool_name") or "tool",
                            "call_id": event.get("call_id") or "",
                            "result_id": event.get("result_id") or "",
                            "status": event.get("status") or "completed",
                            "arguments": event.get("arguments") or "",
                            "output": event.get("output") or "",
                            "references": event.get("references") if isinstance(event.get("references"), list) else [],
                        }
                        if isinstance(event.get("event_id"), int):
                            tool_delta["chat2api_event_id"] = event["event_id"]
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, {"chat2api_tool": tool_delta}),
                            ensure_ascii=False,
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
                    # Compute the exact Web Chat body before persisting its SSE
                    # delivery journal. The journal cursor must describe the same
                    # bytes the live client receives, including inline citations.
                    display_final_text = (
                        web_chat_delivery_text(final_text, raw_final, event)
                        if request.scope.get("chat2api_mode1_web_chat") is True
                        else final_text
                    )
                    # Record the one genuine Worker terminal BEFORE yielding SSE
                    # content: slow or disconnected clients must not leave a
                    # completed browser request stuck in telemetry.running.
                    state.diagnostics["server_stream_terminal_stage"] = "persisting-before-sse"
                    usage, token_meta = await record_request(
                        state, model, prompt, "completed", principal, stream=True, prompt_mode=prompt_mode,
                        response_id=response_id, final_text=final_text, delivery_text=display_final_text,
                        request_type=request_type,
                        attachments_count=attachments_count, media_items=archived_media,
                        file_items=archived_files,
                    )
                    recorded = True
                    state.diagnostics["server_stream_terminal_stage"] = "persisted-before-sse"
                    if (request.scope.get("chat2api_mode1_web_chat") is True
                            and isinstance(event.get("references"), list)
                            and event["references"]):
                        reference_delta = {"chat2api_references": event["references"]}
                        if isinstance(event.get("event_id"), int):
                            reference_delta["chat2api_event_id"] = event["event_id"]
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, reference_delta),
                            ensure_ascii=False,
                        ) + "\n\n"
                    # The final same-request Worker terminal is the one owner
                    # of assistant content, regardless of provisional snapshots.
                    # Emit the *whole* authoritative text in bounded SSE chunks
                    # even if the preview is unrelated ("我会..." -> "# ...").
                    # display_final_text was also used for the durable delivery
                    # journal above, so event IDs and replay content stay aligned.
                    final_chunks = list(final_text_chunks(display_final_text))
                    for index, delta in enumerate(final_chunks):
                        sent_text += delta
                        chunk_delta: dict[str, object] = {"content": delta}
                        if (request.scope.get("chat2api_mode1_web_chat") is True
                                and index < len(state.delivery_event_ids)):
                            chunk_delta["chat2api_event_id"] = state.delivery_event_ids[index]
                        yield "data: " + json.dumps(
                            chunk_payload(response_id, model, chunk_delta),
                            ensure_ascii=False,
                        ) + "\n\n"
                    meta = chat2api_meta(state, token_meta, principal)
                    if native_content.get(state.request_id, principal.key_id) and (
                        native_snapshot_finality(
                            {"status": "completed", "diagnostics": state.diagnostics},
                            native_content.get(state.request_id, principal.key_id),
                        ) == "verified-final"
                    ):
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
                    terminal_delta: dict[str, object] = {}
                    if (request.scope.get("chat2api_mode1_web_chat") is True
                            and state.delivery_terminal_event_id):
                        terminal_delta["chat2api_event_id"] = state.delivery_terminal_event_id
                    yield "data: " + json.dumps(
                        chunk_payload(response_id, model, terminal_delta, "stop", usage, meta),
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
                    archived_media, archived_files = [], []
                    if is_success:
                        try:
                            final_text, archived_media, archived_files = await archive_verified_terminal(
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
                        file_items=archived_files if final_status == "completed" else [],
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
        for message in body.messages:
            for file_id in message.referenced_document_file_ids():
                try:
                    validate_document_file(file_store, file_id, principal)
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
        file_ids = list(dict.fromkeys(body.all_file_ids() + inline_ids))
        request_id = accepted_request_id(x_chat2api_request_id, "req_")
        attachments = attachment_specs(file_ids, principal, request_id=request_id)
        if len(attachments) > 4:
            raise HTTPException(status_code=400, detail="At most four image/file inputs are supported per request")
        if sum(int(item.get("size") or 0) for item in attachments) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Combined image/file inputs may not exceed 20 MiB")
        if any(
            str(item.get("mime_type") or "").startswith("image/")
            and int(item.get("size") or 0) > 10 * 1024 * 1024
            for item in attachments
        ):
            raise HTTPException(status_code=400, detail="input_image exceeds the 10 MiB per-image limit")
        response_id = completion_id()
        request_started = time.perf_counter()
        requested_client = body.client_id or x_chat2api_client
        request_type = "multimodal" if attachments else "text"

        # Read-only version identity for browser-origin Playground calls.
        # ABCD may legitimately use real SSE even if its final report never
        # arrives. Never reject a request solely for missing that report.
        origin_evidence = playground_stream_attribution(
            request_id=request_id, stream=body.stream,
            headers=request.headers, test_runs=test_runs,
        )
        requested_output_limits = [
            getattr(body, "max_tokens", None),
            getattr(body, "max_completion_tokens", None),
        ]
        output_budget = max([
            4096,
            *(value for value in requested_output_limits if isinstance(value, int) and not isinstance(value, bool) and value > 0),
        ])
        try:
            quota_reservation = await reserve_request_quota(
                app, request_id, principal, prompt, output_budget,
                model_id=body.model,
            )
        except HTTPException as quota_error:
            if not is_request_id_conflict(quota_error):
                await record_early_error(
                    request_id=request_id, response_id=response_id, requested_model=body.model,
                    prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                    started_mono=request_started, error=str(quota_error.detail), request_type=request_type,
                    attachments_count=len(attachments),
                )
            raise
        # Persist request visibility only after durable quota/wallet admission.
        # Otherwise an old request ID outside TelemetryStore's 500-row cache
        # could overwrite its history before the persistent reservation journal
        # rejects the duplicate.
        def release_admission_holds(reason: str = "request_admission_projection_conflict") -> None:
            if not quota_reservation:
                return
            quota_store = getattr(app.state, "quota_reservations", None)
            if quota_store is not None:
                try:
                    quota_store.release(request_id, principal.key_id)
                except Exception as release_error:
                    logger.warning(
                        "Duplicate request token hold cleanup failed request_id=%s error_type=%s",
                        request_id, type(release_error).__name__,
                    )
            accounts = getattr(app.state, "user_accounts", None)
            billing = getattr(app.state, "user_billing", None)
            try:
                owner = accounts.owner_for_key(principal.key_id) if accounts is not None else None
                if owner and billing is not None:
                    billing.release_reservation(
                        request_id, str(owner), reason=reason,
                    )
            except Exception as release_error:
                logger.warning(
                    "Duplicate request wallet hold cleanup failed request_id=%s error_type=%s",
                    request_id, type(release_error).__name__,
                )

        web_chat_cancel_signal = None
        if request.scope.get("chat2api_mode1_web_chat") is True:
            cancel_events = app.state.web_chat_cancel_events
            if request_id in cancel_events:
                release_admission_holds()
                raise HTTPException(status_code=409, detail="Request ID is already awaiting admission")
            web_chat_cancel_signal = WebChatCancellationSignal()
            cancel_events[request_id] = web_chat_cancel_signal

        try:
            await record_request_queued(
                request_id=request_id, response_id=response_id, requested_model=body.model,
                prompt=prompt, principal=principal, stream=body.stream,
                prompt_mode=body.prompt_mode, request_type=request_type,
                web_chat_mode=request.scope.get("chat2api_mode1_web_chat") is True,
                attachments_count=len(attachments),
                input_file_ids=[str(item["file_id"]) for item in attachments],
            )
        except DuplicateRequestIdentity as error:
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            release_admission_holds()
            raise HTTPException(
                status_code=409,
                detail={"code": "request_id_conflict", "message": str(error)},
            ) from error
        except Exception:
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            release_admission_holds()
            raise
        if quota_reservation:
            try:
                await telemetry.merge_diagnostics(request_id, {
                    "quota_reservation_v1": {
                        "state": "reserved",
                        "reserved_tokens": int(quota_reservation.get("reserved_tokens") or 0),
                        "estimate_policy": "prompt_tokens_plus_conservative_4096_output_floor_v1",
                    },
                })
            except Exception as quota_diagnostic_error:
                logger.warning(
                    "Quota reservation diagnostic persistence failed request_id=%s error_type=%s",
                    request_id, type(quota_diagnostic_error).__name__,
                )
        if origin_evidence:
            try:
                await telemetry.merge_diagnostics(
                    request_id, {"playground_transport_attribution_v293": origin_evidence},
                )
            except Exception as evidence_error:
                # An observer must not break an admitted production request.
                # Never include prompt, credentials, page DOM, URL or raw
                # browser exception in runtime/request-log diagnostics.
                logger.exception(
                    "Passive Playground transport evidence failed request_id=%s error_type=%s",
                    request_id, type(evidence_error).__name__,
                )
                try:
                    await telemetry.update(request_id, {
                        "diagnostics": {
                            "playground_transport_attribution_error_v294":
                                type(evidence_error).__name__[:60],
                        },
                    })
                except Exception:
                    logger.warning(
                        "Passive Playground evidence failure could not be persisted request_id=%s",
                        request_id,
                    )
        try:
            client_id = await await_worker_admission_or_cancel(
                lambda: resolve_client_with_reconnect_grace(requested_client),
                web_chat_cancel_signal,
            )
            if web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set():
                raise WebChatAdmissionCancelled()
            await telemetry.update(request_id, {
                "client_id": client_id,
                "diagnostics": {"request_admission_stage": "waiting-capacity"},
            })
            if web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set():
                raise WebChatAdmissionCancelled()
            logger.info(
                "API request awaiting Broker capacity request_id=%s worker=%s",
                request_id, client_id,
                extra={"request_id": request_id, "client_id": client_id,
                       "admission_stage": "waiting-capacity"},
            )
            state = await chat_request_service.reserve(request_id, client_id)
            state.mode1_web_chat = request.scope.get("chat2api_mode1_web_chat") is True
            state.mode1_owner_key_id = principal.key_id if state.mode1_web_chat else ""
        except WebChatAdmissionCancelled:
            await record_early_error(
                request_id=request_id, response_id=response_id, requested_model=body.model,
                prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                started_mono=request_started, error="Web Chat request cancelled before Worker dispatch",
                request_type=request_type, attachments_count=len(attachments), status_value="cancelled",
            )
            release_admission_holds("web_chat_cancelled_before_worker_dispatch")
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            raise HTTPException(status_code=409, detail="Request cancelled before Worker dispatch")
        except HTTPException as error:
            cancelled = web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set()
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error.detail), request_type=request_type,
                                     attachments_count=len(attachments),
                                     status_value="cancelled" if cancelled else "error")
            release_admission_holds("web_chat_cancelled_before_worker_dispatch" if cancelled else "request_admission_failed")
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            raise
        except RuntimeError as error:
            cancelled = web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set()
            await record_early_error(request_id=request_id, response_id=response_id, requested_model=body.model,
                                     prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                                     started_mono=request_started, error=str(error), request_type=request_type,
                                     attachments_count=len(attachments),
                                     status_value="cancelled" if cancelled else "error")
            release_admission_holds("web_chat_cancelled_before_worker_dispatch" if cancelled else "request_admission_failed")
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except asyncio.CancelledError:
            if web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set():
                await record_early_error(
                    request_id=request_id, response_id=response_id, requested_model=body.model,
                    prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                    started_mono=request_started, error="Web Chat request cancelled before Worker dispatch",
                    request_type=request_type, attachments_count=len(attachments), status_value="cancelled",
                )
                release_admission_holds("web_chat_cancelled_before_worker_dispatch")
            else:
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
                release_admission_holds("request_transport_ended_before_worker_dispatch")
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            raise
        except Exception as admission_error:
            # A failure after the queued row was persisted but before any
            # Worker was dispatched must not strand Request History in queued.
            # Actual stack stays in redacted Runtime Logs; the per-request
            # export gets a stable, content-free exception class and stage.
            kind = type(admission_error).__name__[:60]
            logger.exception(
                "Unexpected API admission failure request_id=%s exception_kind=%s",
                request_id, kind,
            )
            cancelled = web_chat_cancel_signal is not None and web_chat_cancel_signal.requested.is_set()
            try:
                if cancelled:
                    await record_early_error(
                        request_id=request_id, response_id=response_id, requested_model=body.model,
                        prompt=prompt, principal=principal, stream=body.stream, prompt_mode=body.prompt_mode,
                        started_mono=request_started, error="Web Chat request cancelled before Worker dispatch",
                        request_type=request_type, attachments_count=len(attachments), status_value="cancelled",
                    )
                else:
                    await telemetry.update(request_id, {
                        "status": "error",
                        "error": "Internal request admission failed (" + kind + ")",
                        "diagnostics": {
                            "request_admission_stage": "failed-before-worker-dispatch",
                            "request_admission_failure_kind_v294": kind,
                            "request_admission_wait_ms": round(
                                (time.perf_counter() - request_started) * 1000, 1
                            ),
                        },
                    })
            except Exception:
                logger.exception(
                    "Failed to persist request admission failure request_id=%s",
                    request_id,
                )
            release_admission_holds("web_chat_cancelled_before_worker_dispatch" if cancelled else "request_admission_failed")
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            raise HTTPException(
                status_code=500,
                detail="Internal request admission failure; inspect request_id=" + request_id,
            ) from admission_error

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
            if web_chat_cancel_signal is not None:
                async with app.state.web_chat_cancel_lock:
                    if web_chat_cancel_signal.requested.is_set():
                        raise WebChatAdmissionCancelled()
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
                        input_file_ids=[str(item["file_id"]) for item in attachments],
                    )
                    await asyncio.to_thread(
                        authorize_wallet_dispatch, app, request_id, principal,
                    )
                    await chat_request_service.dispatch(
                        client_id,
                        request_id=request_id,
                        prompt=prompt,
                        attachments=attachments,
                        options={"auto_switch_text": True, "timeout_seconds": browser_timeout_seconds, "model": body.model,
                                 "probe_policy": state.probe_policy},
                    )
                    state.diagnostics["request_admission_stage"] = "dispatched"
                    web_chat_cancel_signal.finalized.set()
                    app.state.web_chat_cancel_events.pop(request_id, None)
            else:
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
                    input_file_ids=[str(item["file_id"]) for item in attachments],
                )
                await asyncio.to_thread(
                    authorize_wallet_dispatch, app, request_id, principal,
                )
                await chat_request_service.dispatch(
                    client_id,
                    request_id=request_id,
                    prompt=prompt,
                    attachments=attachments,
                    options={"auto_switch_text": True, "timeout_seconds": browser_timeout_seconds, "model": body.model,
                             "probe_policy": state.probe_policy},
                )
        except WebChatAdmissionCancelled:
            state.diagnostics["request_admission_stage"] = "cancelled-before-worker-dispatch"
            try:
                await broker.publish(request_id, {
                    "type": "chat.cancelled",
                    "request_id": request_id,
                    "reason": "Web Chat cancelled before Worker dispatch",
                })
                await record_request(
                    state, body.model, prompt, "cancelled", principal,
                    stream=body.stream, prompt_mode=body.prompt_mode,
                    response_id=response_id, error="Web Chat request cancelled before Worker dispatch",
                    request_type=request_type, attachments_count=len(attachments),
                )
            finally:
                registry.busy_clients.discard(client_id)
                release_admission_holds("web_chat_cancelled_before_worker_dispatch")
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
                await broker.release(request_id)
            raise HTTPException(status_code=409, detail="Request cancelled before Worker dispatch")
        except Exception as error:
            state.completed_mono = time.perf_counter()
            await record_request(state, body.model, prompt, "error", principal, stream=body.stream,
                                 prompt_mode=body.prompt_mode, response_id=response_id, error=str(error),
                                 request_type=request_type, attachments_count=len(attachments))
            registry.busy_clients.discard(client_id)
            if web_chat_cancel_signal is not None:
                web_chat_cancel_signal.finalized.set()
                app.state.web_chat_cancel_events.pop(request_id, None)
            await broker.release(request_id)
            raise HTTPException(status_code=503, detail=str(error)) from error


        if body.stream:
            # Only this request-owned state may be correlated by the ASGI
            # observer. No model text/credentials are copied into the scope.
            request.scope["chat2api_sse_request_id"] = request_id
            request.scope["chat2api_sse_broker_state"] = state
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
                    file_items=archived_files,
            )
            meta = chat2api_meta(state, token_meta, principal)
            if native_content.get(request_id, principal.key_id) and (
                        native_snapshot_finality(
                            {"status": "completed", "diagnostics": state.diagnostics},
                            native_content.get(request_id, principal.key_id),
                        ) == "verified-final"
                    ):
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
                final_text, archived_media, archived_files = "", [], []
                if is_success:
                    try:
                        final_text, archived_media, archived_files = await archive_verified_terminal(
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
                    file_items=archived_files,
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

    # The Mode 1 Web Chat adapter is a dedicated user-session + managed-Key
    # ingress. It reuses the exact shared Chat Completions admission/Worker
    # handler above, but does not expose or call the OpenAI-compatible /v1 route.
    app.state.chat_completions_adapter = chat_completions

    # Web Chat uses the HttpOnly account session and a server-managed shared
    # API Key. The per-user key ID header is an ownership namespace only; it is
    # validated against the logged-in account and is never an auth credential.
    @app.post("/api/user/chat/completions")
    async def mode1_user_chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        x_chat2api_request_id: str | None = Header(default=None),
        x_chat2api_user_key_id: str | None = Header(default=None, alias="X-Chat2API-User-Key-ID"),
    ) -> Response:
        if not same_origin_admin_request(request):
            raise HTTPException(
                status_code=403, detail="Same-origin request required",
                headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
            )
        from .web_chat_identity_v1 import resolve_web_chat_principal

        user, principal = await resolve_web_chat_principal(request, x_chat2api_user_key_id)
        request.scope["chat2api_mode1_web_chat"] = True
        request.scope["chat2api_web_chat_user_id"] = str(user["user_id"])
        adapter = request.app.state.chat_completions_adapter
        response = await adapter(body, request, None, x_chat2api_request_id, principal)
        if isinstance(response, Response):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie"
        return response

    @app.post("/v1/images/generations")
    async def image_generations(
        body: ImageGenerationRequest,
        request: Request,
        x_chat2api_client: str | None = Header(default=None),
        x_chat2api_request_id: str | None = Header(default=None),
        principal: ApiPrincipal = Depends(require_api_key),
    ) -> dict[str, object]:
        require_scope(principal, "images")
        request_id = accepted_request_id(x_chat2api_request_id, "imgreq_")
        if telemetry.get(request_id):
            raise HTTPException(
                status_code=409,
                detail={"code": "request_id_conflict", "message": "Duplicate request_id"},
            )
        attachments = attachment_specs(
            [item.file_id for item in body.attachments], principal, request_id=request_id,
        )
        response_id = "img_" + uuid.uuid4().hex
        started = time.perf_counter()
        client_id = resolve_client_now(body.client_id or x_chat2api_client)
        if telemetry.get(request_id):
            raise HTTPException(
                status_code=409,
                detail={"code": "request_id_conflict", "message": "request_id already exists"},
            )
        try:
            state = await broker.create(request_id, client_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        quota_reservation = None
        try:
            quota_reservation = await reserve_image_generation_quota(
                app, request_id, principal, body.prompt, body.n,
                model_id="gpt-image",
            )
        except HTTPException as quota_error:
            state.completed_mono = time.perf_counter()
            if not is_request_id_conflict(quota_error):
                try:
                    await telemetry.upsert({
                        "request_id": request_id, "response_id": response_id, "client_id": client_id,
                        "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                        "requested_model": "gpt-image", "request_type": "image_generation",
                        "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                        "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "error",
                        "usage": {}, "timings": state.timings(),
                        "diagnostics": {"quota_reservation_v1": {
                            "state": "rejected", "http_status": quota_error.status_code,
                            "estimate_policy": "prompt_tokens_plus_4096_per_image_admission_floor_v1",
                        }},
                        "error": str(quota_error.detail),
                    }, create_only=True)
                except DuplicateRequestIdentity:
                    # A concurrent request won this identity. Never replace
                    # its history with the losing admission's quota error.
                    pass
                except Exception as telemetry_error:
                    logger.warning(
                        "Image quota rejection could not be persisted request_id=%s error_type=%s",
                        request_id, type(telemetry_error).__name__,
                    )
            try:
                await broker.release(request_id)
            except Exception as release_error:
                logger.warning(
                    "Image quota rejection broker release failed request_id=%s error_type=%s",
                    request_id, type(release_error).__name__,
                )
            raise

        quota_diagnostics = {}
        if quota_reservation:
            quota_diagnostics = {"quota_reservation_v1": {
                "state": "reserved",
                "reserved_tokens": int(quota_reservation.get("reserved_tokens") or 0),
                "estimate_policy": "prompt_tokens_plus_4096_per_image_admission_floor_v1",
                "provider_usage_verified": False,
            }}

        def image_diagnostics() -> dict[str, Any]:
            return {**dict(state.diagnostics), **quota_diagnostics}

        registry.busy_clients.add(client_id)
        state.probe_policy = probe_policy(client_id)
        timeout_seconds = body.timeout or max(config.request_timeout_seconds, 300)
        try:
            await telemetry.upsert(
                {
                    "request_id": request_id, "response_id": response_id, "client_id": client_id,
                    "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                    "requested_model": "gpt-image", "request_type": "image_generation",
                    "attachments_count": len(attachments),
                    "input_file_ids": [str(item["file_id"]) for item in attachments],
                    "stream": False, "prompt_mode": "image",
                    "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "running",
                    "usage": {}, "timings": state.timings(), "diagnostics": image_diagnostics(), "error": None,
                },
                create_only=True,
            )
            await asyncio.to_thread(
                authorize_wallet_dispatch, app, request_id, principal,
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
                    "diagnostics": image_diagnostics(), "error": None,
                },
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
                    "diagnostics": image_diagnostics(),
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
        except DuplicateRequestIdentity as error:
            state.completed_mono = time.perf_counter()
            if quota_reservation:
                try:
                    app.state.quota_reservations.release(request_id, principal.key_id)
                except Exception as release_error:
                    logger.warning(
                        "Image quota release after duplicate request ID failed request_id=%s error_type=%s",
                        request_id, type(release_error).__name__,
                    )
            accounts = getattr(app.state, "user_accounts", None)
            billing = getattr(app.state, "user_billing", None)
            try:
                owner = accounts.owner_for_key(principal.key_id) if accounts is not None else None
                if owner and billing is not None:
                    billing.release_reservation(
                        request_id, str(owner), reason="request_admission_projection_conflict",
                    )
            except Exception as release_error:
                logger.warning(
                    "Image wallet hold cleanup after duplicate request ID failed request_id=%s error_type=%s",
                    request_id, type(release_error).__name__,
                )
            raise HTTPException(
                status_code=409,
                detail={"code": "request_id_conflict", "message": "request_id already exists"},
            ) from error
        except asyncio.TimeoutError as error:
            state.completed_mono = time.perf_counter()
            await telemetry.upsert(
                {"request_id": request_id, "response_id": response_id, "client_id": client_id,
                 "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                 "requested_model": "gpt-image", "request_type": "image_generation",
                 "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                 "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "error",
                 "usage": {}, "timings": state.timings(), "diagnostics": image_diagnostics(),
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
                 "usage": {}, "timings": state.timings(), "diagnostics": image_diagnostics(), "error": str(error)}
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        except Exception as error:
            current = telemetry.get(request_id)
            current_status = str((current or {}).get("status") or "")
            if current_status not in {"completed", "error", "cancelled", "stalled", "failed", "timeout"}:
                state.completed_mono = state.completed_mono or time.perf_counter()
                try:
                    await telemetry.upsert({
                        "request_id": request_id, "response_id": response_id, "client_id": client_id,
                        "api_key_id": principal.key_id, "api_key_name": principal.name, "auth_kind": principal.kind,
                        "requested_model": "gpt-image", "request_type": "image_generation",
                        "attachments_count": len(attachments), "stream": False, "prompt_mode": "image",
                        "prompt_chars": len(body.prompt), "completion_chars": 0, "status": "error",
                        "usage": {}, "timings": state.timings(), "diagnostics": image_diagnostics(),
                        "error": type(error).__name__,
                    })
                except Exception as telemetry_error:
                    logger.warning(
                        "Image request failure telemetry could not be persisted request_id=%s error_type=%s",
                        request_id, type(telemetry_error).__name__,
                    )
            raise
        finally:
            registry.busy_clients.discard(client_id)
            if quota_reservation:
                current = telemetry.get(request_id)
                if str((current or {}).get("status") or "") != "completed":
                    try:
                        app.state.quota_reservations.release(request_id, principal.key_id)
                    except Exception as release_error:
                        logger.warning(
                            "Image quota reservation cleanup deferred request_id=%s error_type=%s",
                            request_id, type(release_error).__name__,
                        )
                    accounts = getattr(app.state, "user_accounts", None)
                    billing = getattr(app.state, "user_billing", None)
                    try:
                        owner = accounts.owner_for_key(principal.key_id) if accounts is not None else None
                        if owner and billing is not None:
                            billing.release_reservation(
                                request_id, str(owner), reason="image_request_not_completed",
                            )
                    except Exception as release_error:
                        logger.warning(
                            "Image wallet reservation cleanup deferred request_id=%s error_type=%s",
                            request_id, type(release_error).__name__,
                        )
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
        item = file_store.get(file_id)
        if not item or str(item.owner_key_id) != str(row.get("api_key_id") or ""):
            raise HTTPException(status_code=404, detail="Archived image is no longer available")
        if item.purpose != "generated-output" or item.mime_type not in {
            "image/png", "image/jpeg", "image/webp", "image/gif"
        }:
            raise HTTPException(status_code=404, detail="Invalid request media")
        try:
            item, payload = file_store.read(file_id)
        except FileIntegrityError as error:
            raise HTTPException(status_code=503, detail="Archived image integrity check failed") from error
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Archived image is no longer available") from error
        except OSError as error:
            raise HTTPException(status_code=503, detail="Archived image storage is temporarily unavailable") from error
        return Response(
            content=payload, media_type=item.mime_type,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.websocket("/ws/extensions/{client_id}")
    async def extension_socket(websocket: WebSocket, client_id: str, token: str = Query(default="")) -> None:
        if not await registry.authenticate(client_id, token):
            client = registry.clients.get(client_id)
            if client is None:
                reason = "unknown_client"
            elif not client.connection_enabled:
                reason = "connection_disabled"
            elif not registry.worker_admin_enabled(client_id):
                reason = "worker_disabled"
            else:
                reason = "invalid_token"
            logger.warning(
                "Extension WebSocket authentication rejected client_id=%s reason=%s",
                client_id, reason,
            )
            await websocket.close(code=4401, reason="Invalid extension credentials")
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
                if not await registry.touch(client_id, metadata, websocket=websocket):
                    # This connection was replaced while it was still draining.
                    # Ignore every message from the stale socket.
                    break
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
                if message_type in {"native.page.windows.chunk","native.page.windows.finished","native.page.windows.progress","native.page.windows.tool.updated"}:
                    # Only the authenticated extension's current WebSocket may
                    # deliver its administrator-triggered, read-only evidence.
                    # Never forward this event into the live request broker.
                    receiver=getattr(app.state,"native_page_windows_ingest",None)
                    if callable(receiver) and registry.sockets.get(client_id) is websocket:
                        await receiver(client_id,message)
                    continue
                if message_type == "chat.file.chunk":
                    # A successful socket.send is NOT a persisted chunk ACK.
                    # The server replies to the same authenticated socket only
                    # after the bounded stage write/validation completes.
                    accepted, reason = False, "request-or-socket-not-active"
                    state = broker.requests.get(request_id)
                    if (
                        state
                        and state.owns_dispatch_socket(
                            client_id, websocket, registry.sockets.get(client_id)
                        )
                        and state.terminal_event_type is None
                        and state.completed_mono is None
                    ):
                        try:
                            await asyncio.to_thread(
                                generated_staging.ingest,
                                request_id=request_id,
                                upload_id=message.get("upload_id"),
                                owner_key_id=str((telemetry.get(request_id) or {}).get("api_key_id") or ""),
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
                if message_type == "chat.native.dom_evidence" and request_id:
                    # A standalone authenticated read-only event: never alters
                    # request-v6 status, terminal, streaming or routing.
                    active_state=broker.requests.get(request_id)
                    row=telemetry.get(request_id) or {}
                    owner=str(row.get("api_key_id") or "")
                    if (
                        active_state
                        and active_state.owns_dispatch_socket(
                            client_id, websocket, registry.sockets.get(client_id)
                        )
                        and active_state.terminal_event_type is None
                        and active_state.completed_mono is None
                        and owner
                        and message.get("phase") == "before_terminal"
                    ):
                        try:
                            native_dom_evidence.save(request_id,owner,message.get("report"))
                        except (ValueError, TypeError, OSError):
                            logger.info("Worker native DOM evidence rejected request=%s", request_id)
                    continue
                if message_type in {"chat.native.chunk", "chat.native.snapshot"} and request_id:
                    # Sidecar-only transport ACK: a local socket.send never
                    # proves that the server authenticated, validated or
                    # published this revision. No new Broker terminal owner.
                    active_state = broker.requests.get(request_id)
                    row = telemetry.get(request_id) or {}
                    owner = str(row.get("api_key_id") or "")
                    accepted, committed = False, False
                    reason = "request-or-socket-not-active"
                    authorized = (
                        active_state is not None
                        and active_state.owns_dispatch_socket(
                            client_id, websocket, registry.sockets.get(client_id)
                        )
                        and active_state.terminal_event_type is None
                        and active_state.completed_mono is None
                        and bool(owner)
                    )
                    if authorized:
                        try:
                            if message_type == "chat.native.chunk":
                                payload = {key: message.get(key) for key in (
                                    "revision", "index", "count", "byte_length",
                                    "sha256", "data")}
                                committed = native_chunks.ingest(request_id, owner, payload)
                            else:
                                committed = native_chunks.publish_snapshot(
                                    request_id, owner, message.get("snapshot"))
                            accepted = True
                            reason = ""
                            if committed:
                                active_state.diagnostics["native_snapshot_received_revision"] = (
                                    message.get("revision") if message_type == "chat.native.chunk"
                                    else (message.get("snapshot") or {}).get("revision")
                                )
                                active_state.diagnostics["native_snapshot_server_committed"] = True
                        except (ValueError, TypeError) as error:
                            # Do not send exception details or raw DOM/media.
                            reason = "native-validation-rejected"
                            active_state.diagnostics["native_sidecar_rejection"] = type(error).__name__
                            logger.info("Native sidecar rejected request=%s kind=%s reason=%s",
                                        request_id, message_type, type(error).__name__)
                    await websocket.send_json({
                        "type": "chat.native.ack",
                        "kind": "chunk" if message_type == "chat.native.chunk" else "snapshot",
                        "request_id": request_id,
                        "revision": message.get("revision") if message_type == "chat.native.chunk"
                                    else (message.get("snapshot") or {}).get("revision")
                                         if isinstance(message.get("snapshot"), dict) else None,
                        "index": message.get("index") if message_type == "chat.native.chunk" else -1,
                        "ok": accepted,
                        "committed": bool(committed),
                        "error": reason,
                    })
                    continue
                if message_type == "probe.event" and request_id:
                    # Existing authenticated socket and broker are the sole
                    # request-to-Worker authority; probe events never reach the
                    # request terminal queue.
                    state = broker.requests.get(request_id)
                    if (
                        state
                        and state.owns_dispatch_socket(
                            client_id, websocket, registry.sockets.get(client_id)
                        )
                        and state.terminal_event_type is None
                        and state.completed_mono is None
                    ):
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
                    "chat.diagnostics", "chat.started", "chat.delta", "chat.snapshot",
                    "chat.tool.call", "chat.tool.result", "chat.references", "chat.completed",
                    "chat.error", "chat.cancelled", "image.diagnostics", "image.started", "image.progress",
                    "image.completed", "image.error", "image.cancelled",
                }:
                    terminal_message = message_type in {
                        "chat.completed", "chat.error", "chat.cancelled",
                        "image.completed", "image.error", "image.cancelled",
                    }
                    worker_event_ack_id = str(message.get("worker_event_ack_id") or "")
                    voice_event_ack_id = (
                        worker_event_ack_id
                        if message.get("kind") == "voice-live"
                        and message_type in {"image.started", "image.progress", "image.diagnostics"}
                        and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", worker_event_ack_id)
                        else ""
                    )
                    active_state = broker.requests.get(request_id)
                    recovered_row = telemetry.get(request_id) or {}
                    if active_state is None and is_replayed_mode1_terminal(
                        recovered_row, message, client_id,
                    ):
                        replay_owner = str(recovered_row.get("api_key_id") or "")
                        if message_type == "chat.completed" and str(recovered_row.get("status") or "").lower() == "recovering":
                            try:
                                # The journal intentionally keeps only a bounded
                                # terminal summary, never the potentially multi-MiB
                                # inline media payload. Archive the full Worker text
                                # before publishing any user-visible completion.
                                await event_journal.append_worker_event(
                                    request_id, replay_owner, message,
                                )
                                finalized = await finalize_replayed_chat_completion(
                                    websocket, recovered_row, message, client_id,
                                )
                            except Exception as recovery_error:
                                logger.exception(
                                    "Replayed Worker completion finalization failed request_id=%s error_type=%s",
                                    request_id, type(recovery_error).__name__,
                                )
                                try:
                                    diagnostics = (
                                        dict(recovered_row.get("diagnostics"))
                                        if isinstance(recovered_row.get("diagnostics"), dict) else {}
                                    )
                                    diagnostics.update({
                                        "web_chat_terminal_replay_reconciled": "retryable-finalization-failure",
                                        "web_chat_terminal_replay_error": type(recovery_error).__name__,
                                        "web_chat_terminal_replay_received_at": time.time(),
                                    })
                                    await telemetry.upsert({
                                        "request_id": request_id,
                                        "response_text_provisional": str(message.get("text") or "")[:50_000],
                                        "diagnostics": diagnostics,
                                    })
                                except Exception:
                                    logger.exception(
                                        "Could not persist provisional replay text request_id=%s",
                                        request_id,
                                    )
                                await websocket.send_json({
                                    "type": "chat.terminal.ack",
                                    "request_id": request_id,
                                    "terminal_type": message_type,
                                    "accepted": False,
                                    "reason": "terminal_finalization_retryable",
                                })
                                continue
                            if finalized:
                                logger.info(
                                    "Fully finalized replayed Worker completion request_id=%s",
                                    request_id,
                                )
                                await websocket.send_json({
                                    "type": "chat.terminal.ack",
                                    "request_id": request_id,
                                    "terminal_type": message_type,
                                    "accepted": True,
                                    "recovered": True,
                                })
                                continue
                        # Late replay after the recovery grace period, or a row
                        # missing the original usage/request identity evidence,
                        # remains provisional. Never fabricate a successful
                        # response or charge without the durable admission data.
                        projection = replayed_terminal_projection(recovered_row, message)
                        recovered_status = str(projection["status"])
                        candidate_text = str(projection.get("response_text") or "")
                        try:
                            if message_type == "chat.completed":
                                await event_journal.append_worker_event(
                                    request_id, replay_owner,
                                    {"type": "chat.snapshot", "text": candidate_text},
                                )
                            await event_journal.append_worker_event(
                                request_id, replay_owner, message,
                            )
                        except Exception:
                            logger.exception(
                                "Cannot journal replayed Worker terminal request_id=%s",
                                request_id,
                            )
                        persisted = False
                        try:
                            await telemetry.upsert({
                                **recovered_row,
                                **projection,
                            })
                            persisted = True
                            try:
                                await event_journal.append_terminal(
                                    request_id, replay_owner, recovered_status,
                                    str(message.get("error") or message.get("reason") or ""),
                                )
                            except Exception:
                                logger.exception(
                                    "Cannot append replayed Worker terminal journal marker request_id=%s",
                                    request_id,
                                )
                            logger.warning(
                                "Reconciled replayed Worker terminal as %s request_id=%s; no generation was redispatched",
                                recovered_status, request_id,
                            )
                        except Exception:
                            logger.exception(
                                "Cannot reconcile replayed Worker terminal request_id=%s",
                                request_id,
                            )
                        if persisted:
                            await websocket.send_json({
                                "type": "chat.terminal.ack",
                                "request_id": request_id,
                                "terminal_type": message_type,
                                "accepted": True,
                                "recovered": True,
                            })
                        continue
                    replay_diagnostics = message.get("diagnostics")
                    replay_diagnostics = replay_diagnostics if isinstance(replay_diagnostics, dict) else {}
                    if (
                        active_state is None
                        and terminal_message
                        and replay_diagnostics.get("worker_transport_replayed") is True
                        and replay_diagnostics.get("worker_transport_recovery") == "transport-outbox-v47"
                    ):
                        if is_already_finalized_worker_terminal(recovered_row, client_id, message_type):
                            delivery_durable = True
                            if (
                                message_type == "chat.completed"
                                and recovered_row.get("web_chat_mode") is True
                                and str(recovered_row.get("status") or "").lower() == "completed"
                            ):
                                try:
                                    delivery_durable = await ensure_replayed_usage_settlement(
                                        recovered_row,
                                    )
                                    if delivery_durable:
                                        snapshot = await event_journal.snapshot(
                                            request_id, str(recovered_row.get("api_key_id") or ""),
                                        )
                                        if snapshot.get("terminal_status") != "completed":
                                            final_text = recovered_row.get("response_text")
                                            delivery_durable = bool(
                                                isinstance(final_text, str)
                                                and recovered_row.get("response_text_available") is True
                                                and await ensure_replayed_delivery_terminal(
                                                    request_id,
                                                    str(recovered_row.get("api_key_id") or ""),
                                                    final_text,
                                                )
                                            )
                                except Exception:
                                    delivery_durable = False
                            if delivery_durable:
                                if (
                                    message_type == "chat.completed"
                                    and recovered_row.get("web_chat_mode") is True
                                ):
                                    diagnostics = (
                                        dict(recovered_row.get("diagnostics"))
                                        if isinstance(recovered_row.get("diagnostics"), dict) else {}
                                    )
                                    diagnostics.update({
                                        "web_chat_terminal_replay_reconciled": "full-finalization",
                                        "web_chat_terminal_replay_recovered_at": time.time(),
                                    })
                                    diagnostics.pop("web_chat_terminal_replay_error", None)
                                    try:
                                        await telemetry.upsert({
                                            "request_id": request_id,
                                            "diagnostics": diagnostics,
                                        })
                                    except Exception:
                                        delivery_durable = False
                                if delivery_durable:
                                    await websocket.send_json({
                                        "type": "chat.terminal.ack",
                                        "request_id": request_id,
                                        "terminal_type": message_type,
                                        "accepted": True,
                                        "already_finalized": True,
                                    })
                                else:
                                    await websocket.send_json({
                                        "type": "chat.terminal.ack",
                                        "request_id": request_id,
                                        "terminal_type": message_type,
                                        "accepted": False,
                                        "reason": "usage_or_delivery_terminal_not_durable",
                                    })
                            else:
                                await websocket.send_json({
                                    "type": "chat.terminal.ack",
                                    "request_id": request_id,
                                    "terminal_type": message_type,
                                    "accepted": False,
                                    "reason": "usage_or_delivery_terminal_not_durable",
                                })
                        else:
                            await websocket.send_json({
                                "type": "chat.terminal.ack",
                                "request_id": request_id,
                                "terminal_type": message_type,
                                "accepted": False,
                                "reason": "request_not_recoverable",
                            })
                        continue
                    if not active_state or not active_state.owns_dispatch_socket(
                        client_id, websocket, registry.sockets.get(client_id)
                    ):
                        # A valid extension token is not authority over every
                        # Broker request. Reject cross-Worker, stale and same-ID
                        # reconnect sockets before mutating request diagnostics.
                        logger.warning(
                            "Rejected Worker event without original dispatch socket request=%s client=%s type=%s",
                            request_id, client_id, message_type,
                        )
                        if voice_event_ack_id:
                            await websocket.send_json({
                                "type": "voice.event.ack",
                                "event_id": voice_event_ack_id,
                                "request_id": request_id,
                                "accepted": False,
                                "reason": "request-not-owned-or-active",
                            })
                        continue
                    record_terminal_receipt = bool(
                        terminal_message
                        and active_state.terminal_event_type is None
                        and active_state.completed_mono is None
                    )
                    if record_terminal_receipt:
                        active_state.diagnostics["terminal_transport_server_seen"] = True
                        active_state.diagnostics["terminal_transport_server_received_at"] = time.time()
                        active_state.diagnostics["terminal_transport_payload_chars"] = len(str(message.get("text") or ""))
                    # The Broker repeats the socket/assignment check at its
                    # publication boundary and rejects any server-finalized state.
                    accepted = await broker.publish_worker_event(
                        request_id,
                        message,
                        client_id=client_id,
                        websocket=websocket,
                        current_socket=registry.sockets.get(client_id),
                    )
                    if voice_event_ack_id:
                        await websocket.send_json({
                            "type": "voice.event.ack",
                            "event_id": voice_event_ack_id,
                            "request_id": request_id,
                            "accepted": bool(accepted),
                            "reason": "" if accepted else "broker-rejected-event",
                        })
                    replay_diagnostics = message.get("diagnostics")
                    replay_diagnostics = replay_diagnostics if isinstance(replay_diagnostics, dict) else {}
                    if (
                        terminal_message
                        and replay_diagnostics.get("worker_transport_replayed") is True
                        and replay_diagnostics.get("worker_transport_recovery") == "transport-outbox-v47"
                    ):
                        # A Broker queue acceptance is not enough: the terminal
                        # must also exist in the durable owner-scoped journal.
                        # If a prior attempt closed the Broker state but its
                        # journal write failed, retry the journal append only
                        # for the exact same Worker terminal on the assigned
                        # socket; never reopen or republish the terminal.
                        terminal_journaled = bool(
                            active_state
                            and active_state.mode1_web_chat
                            and active_state.diagnostics.get("web_chat_worker_terminal_journaled_type") == message_type
                            and int(active_state.diagnostics.get("web_chat_worker_terminal_journaled_event_id") or 0) > 0
                        )
                        if (
                            not terminal_journaled
                            and active_state
                            and active_state.mode1_web_chat
                            and active_state.terminal_event_type == message_type
                            and active_state.owns_dispatch_socket(
                                client_id, websocket, registry.sockets.get(client_id)
                            )
                        ):
                            try:
                                retry_event_id = await event_journal.append_worker_event(
                                    request_id, active_state.mode1_owner_key_id, message,
                                )
                                if retry_event_id:
                                    active_state.last_event_id = max(
                                        active_state.last_event_id, int(retry_event_id),
                                    )
                                    active_state.diagnostics["web_chat_worker_terminal_journaled_type"] = message_type
                                    active_state.diagnostics["web_chat_worker_terminal_journaled_event_id"] = int(retry_event_id)
                                    terminal_journaled = True
                            except Exception:
                                logger.exception(
                                    "Worker terminal replay journal retry failed request_id=%s",
                                    request_id,
                                )
                        await websocket.send_json({
                            "type": "chat.terminal.ack",
                            "request_id": request_id,
                            "terminal_type": message_type,
                            "accepted": bool(
                                active_state
                                and active_state.terminal_event_type == message_type
                                and (
                                    (active_state.mode1_web_chat and terminal_journaled)
                                    or not active_state.mode1_web_chat
                                )
                            ),
                            "recovered": False,
                        })
                    if accepted and message_type == "chat.completed" and active_state:
                        row = telemetry.get(request_id) or {}
                        owner = str(row.get("api_key_id") or "")
                        # This is the actual disk-persistence proof; the
                        # earlier per-chunk ACK establishes only validated,
                        # in-memory publication. Neither result owns terminal.
                        active_state.diagnostics["native_snapshot_persisted"] = (
                            _native_snapshot_persist_best_effort(
                                native_content, request_id, owner, active_state.diagnostics,
                            )
                        )
                    if record_terminal_receipt:
                        active_state.diagnostics["terminal_transport_broker_accepted"] = bool(accepted)
                        # Persist acknowledgement as an observation, never as
                        # a competing completed/error terminal. If the API
                        # handler is stuck in archival, the exported log
                        # now reveals the precise boundary it reached.
                        active_state.diagnostics["server_terminal_receipt_stage"] = (
                            "broker-accepted" if accepted else "broker-rejected"
                        )
                        active_state.diagnostics["server_terminal_receipt_type"] = str(message_type)
                        await _telemetry_upsert_best_effort(
                            telemetry,
                            {
                                "request_id": request_id,
                                "diagnostics": dict(active_state.diagnostics),
                            },
                            request_id=request_id,
                            projection="worker-terminal-receipt",
                        )
                    if terminal_message and not accepted:
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
