from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import admin as admin_module
from . import model_capability_routing_patch as model_routing
from .model_catalog import model_transport_id, normalize_concrete_model_id, normalize_model_id, normalize_reasoning_level
from .worker_model_library_v145_patch import install_worker_model_library_v145_patch

logger = logging.getLogger(__name__)

PATCH_ID = "final-model-authority-v144"
PATCH_REVISION = 144
_SERVED_MODEL_FIELDS = frozenset({
    "used_model", "used_model_slug", "resolved_model", "resolved_model_slug",
    "served_model", "served_model_slug",
})
_NETWORK_RESPONSE_SOURCES = frozenset({
    "network_response_metadata", "network_response_header",
})


def _authority(target: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(target, dict):
        return None
    requested = normalize_model_id(target.get("requested_model") or target.get("model"))
    routed = normalize_model_id(target.get("routed_model") or target.get("model"))
    if not routed:
        return None
    reasoning = normalize_reasoning_level(
        target.get("reasoning") or target.get("reasoning_effort") or target.get("preferred_reasoning")
    )
    return {
        "revision": PATCH_REVISION,
        "authority": PATCH_ID,
        "strict": True,
        "requested_model": requested or routed,
        "routed_model": routed,
        "transport_model": model_transport_id(routed),
        "reasoning_requested": reasoning,
    }


def _evidence_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    diagnostics = event.get("diagnostics") if isinstance(event, dict) else None
    if not isinstance(diagnostics, dict):
        return None
    raw = diagnostics.get("model_evidence_v144")
    if not isinstance(raw, dict):
        return None
    # Only the current, explicitly versioned MAIN-world fetch observer may
    # supply served-model evidence. Unversioned/legacy Worker diagnostics
    # remain useful as raw telemetry but cannot verify a model.
    revision = diagnostics.get("model_evidence_revision")
    transport = diagnostics.get("model_evidence_transport")
    if type(revision) is not int or revision != PATCH_REVISION:
        return None
    if transport != "main-world-fetch":
        return None
    return dict(raw)


def _network_response_source(evidence: dict[str, Any]) -> str | None:
    source = str(evidence.get("evidence_source") or "").strip().lower()
    return source if source in _NETWORK_RESPONSE_SOURCES else None


def _served_model_field_valid(evidence: dict[str, Any], source: str | None) -> bool:
    field = str(evidence.get("model_field") or "").strip()
    if source == "network_response_header":
        return field == "response-header"
    if source != "network_response_metadata" or not field:
        return False
    # The observer emits the original JSON path. Validate its terminal key,
    # not the optional nesting path, against the explicit served-model schema.
    field_name = field.rsplit(".", 1)[-1].strip().lower().replace("-", "_")
    return field_name in _SERVED_MODEL_FIELDS


def _trace(authority: dict[str, Any] | None, evidence: dict[str, Any] | None) -> dict[str, Any]:
    authority = dict(authority or {})
    evidence = dict(evidence or {})
    requested = normalize_model_id(authority.get("requested_model") or evidence.get("authority_requested_model"))
    routed = normalize_model_id(authority.get("routed_model") or evidence.get("authority_routed_model") or requested)
    request_model = normalize_model_id(evidence.get("request_model"))
    # A page transport alias ("auto"/"default") is not evidence of a model
    # actually serving the request. Do not mark such a response verified.
    response_status_raw = evidence.get("response_status")
    response_status = (
        response_status_raw if type(response_status_raw) is int and 100 <= response_status_raw <= 599
        else None
    )
    # The success flag and HTTP status are independent evidence fields. A
    # served-model claim requires both to agree on a successful 2xx response.
    response_ok = (
        evidence.get("response_ok") is True
        and response_status is not None
        and 200 <= response_status < 300
    )
    # A model-shaped field in an HTTP error response is not evidence of a
    # model that served this request. Require the same-request 2xx response
    # observation before accepting any concrete served-model identifier.
    evidence_source = _network_response_source(evidence)
    served_field_valid = _served_model_field_valid(evidence, evidence_source)
    served_evidence_valid = bool(response_ok and evidence_source and served_field_valid)
    served = normalize_concrete_model_id(evidence.get("served_model")) if served_evidence_valid else None
    default_model = normalize_concrete_model_id(evidence.get("default_model"))
    # Profile/default metadata is diagnostic only. It must never prove which
    # concrete model actually served this request.
    observed = served
    # This is served-model evidence, not a profile-default mismatch.
    # A network-confirmed different model must be visible as a conflict
    # even when the lower-level observer did not flag an intra-stream clash.
    served_mismatch = bool(routed and served and routed != served)
    # A response-body conflict can be retained without a single model field,
    # but only when it came from the recognized network-response observer.
    conflict = (bool(evidence.get("model_conflict"))
                if response_ok and evidence_source else False) or served_mismatch
    page_default_route = authority.get("default_page_route") is True
    expected_wire = None if page_default_route else str(
        authority.get("transport_model") or model_transport_id(routed) or ""
    ).strip().lower()
    observed_wire = str(evidence.get("request_wire_model") or "").strip().lower()
    # The page-generated native model slug is more precise than a frozen
    # -*wm transport alias, but only if the MAIN-world request observer
    # explicitly proved it was left unchanged and canonically matched the
    # requested logical model. Never let response/default metadata assert this.
    native_wire = bool(evidence.get("page_native_wire_respected") is True)
    original_wire = str(evidence.get("original_wire_model") or "").strip().lower()
    page_native_verified = bool(
        native_wire and routed and original_wire and observed_wire
        and original_wire == observed_wire
        and normalize_model_id(observed_wire) == routed
    )
    wire_verified = (
        None if not expected_wire or not observed_wire else
        (expected_wire == observed_wire or page_native_verified)
    )
    authority_violation = bool(not page_default_route and (
        (routed and request_model and routed != request_model)
        or wire_verified is False
    ))
    verified = bool(routed and served and routed == served and not conflict and not authority_violation)
    level = "served" if served else "page-default-unverified" if page_default_route else "profile-default" if default_model else "unverified"
    fallback_reason = str(evidence.get("fallback_reason") or "").strip() or None
    if authority_violation:
        fallback_reason = fallback_reason or "worker_request_model_rewrite"
    elif routed and served and routed != served:
        fallback_reason = fallback_reason or "upstream_served_model_mismatch"
    return {
        "requested_model": requested,
        "routed_model": routed,
        "transport_model": None if page_default_route else (authority.get("transport_model") or model_transport_id(routed)),
        "default_page_route": page_default_route,
        "worker_request_model": request_model,
        "request_wire_model": observed_wire or None,
        "model_wire_transport_matches": wire_verified,
        "model_page_native_wire_respected": page_native_verified,
        "original_wire_model": original_wire or None,
        "served_model": served,
        "model_response_ok": response_ok,
        "model_response_status": response_status,
        "model_evidence_source_valid": bool(evidence_source),
        "model_response_evidence_valid": served_evidence_valid,
        "observed_model": observed,
        "default_model": default_model,
        "model_verified": verified,
        "model_conflict": conflict,
        "model_default_profile_conflict": bool(evidence.get("default_model_conflict")),
        "model_default_profile_mismatch": bool(routed and default_model and routed != default_model),
        "model_authority_violation": authority_violation,
        "model_verification_level": level,
        "model_evidence_source": evidence.get("evidence_source") or ("network_response_metadata" if served else None),
        "model_evidence_field": evidence.get("model_field") or evidence.get("default_model_field"),
        "model_fallback_reason": fallback_reason,
        "final_model_authority": PATCH_ID,
        "model_authority_revision": PATCH_REVISION,
    }


def _schedule_telemetry_projection(
    pending: dict[str, asyncio.Task[None]], telemetry: Any, row: dict[str, Any],
    *, request_id: str, projection: str,
) -> None:
    """Persist optional model telemetry off the request/Worker event path.

    Per-request tasks are chained so the initial requested/routed projection
    cannot finish after and overwrite a later accepted served-model projection.
    A bounded timeout prevents a wedged store from retaining tasks forever.
    """
    previous = pending.get(request_id)

    async def persist() -> None:
        if previous is not None:
            await previous
        try:
            await asyncio.wait_for(telemetry.upsert(row), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out persisting model telemetry request_id=%s projection=%s",
                request_id, projection,
            )
        except Exception:
            logger.exception(
                "Failed to persist model telemetry request_id=%s projection=%s",
                request_id, projection,
            )

    task = asyncio.create_task(
        persist(), name=f"model-telemetry-{projection}-{request_id[:12]}",
    )
    pending[request_id] = task

    def clear(completed: asyncio.Task[None]) -> None:
        if pending.get(request_id) is completed:
            pending.pop(request_id, None)

    task.add_done_callback(clear)


def _patch_admin_html() -> None:
    html = admin_module.ADMIN_HTML
    if 'data-model-evidence-v144="1"' in html:
        return
    old_header = "<th>设备标识</th><th>模型</th><th>附件</th>"
    new_header = '<th>设备标识</th><th>请求模型</th><th data-model-evidence-v144="1">实际模型 / 验证</th><th>附件</th>'
    if old_header not in html:
        return
    html = html.replace(old_header, new_header, 1)
    old_row = "requestHistoryCell(tr,r?.requested_model||r?.model);\n      const attachmentCount="
    new_row = """requestHistoryCell(tr,r?.requested_model||r?.model);
      const modelDiagnostics=(r?.diagnostics&&typeof r.diagnostics==='object')?r.diagnostics:{};
      const servedModel=r?.served_model||r?.observed_model||modelDiagnostics?.served_model||modelDiagnostics?.observed_model||'';
      const verified=Boolean(r?.model_verified??modelDiagnostics?.model_verified);
      const conflict=Boolean(r?.model_conflict??modelDiagnostics?.model_conflict);
      const fallback=r?.model_fallback_reason||modelDiagnostics?.model_fallback_reason||'';
      const verificationLevel=r?.model_verification_level||modelDiagnostics?.model_verification_level||'';
      const actualText=servedModel?`${servedModel} ${verified?'✓':conflict?'⚠':'·'}`:'等待证据';
      const actualCell=requestHistoryCell(tr,actualText,conflict?'bad':verified?'ok':'muted');
      actualCell.title=[`证据级别: ${verificationLevel||'-'}`,fallback?`Fallback: ${fallback}`:'',r?.model_evidence_source?`来源: ${r.model_evidence_source}`:''].filter(Boolean).join(' · ');
      const attachmentCount="""
    if old_row in html:
        html = html.replace(old_row, new_row, 1)
        html = html.replace("colSpan=13", "colSpan=14")
    admin_module.ADMIN_HTML = html


def install_model_observability_v144_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "model_observability_v144_installed", False):
        return app

    # The v145 library is the final catalog/routing authority: normal models
    # exist only after a Worker has freshly discovered and validated them.
    install_worker_model_library_v145_patch(app)

    registry = app.state.registry
    broker = app.state.broker
    telemetry = app.state.telemetry
    base_send: Callable[[str, dict[str, Any]], Awaitable[None]] = registry.send
    base_publish = broker.publish
    base_release = broker.release
    authorities: dict[str, dict[str, Any]] = {}
    projection_tasks: dict[str, asyncio.Task[None]] = {}

    async def send_with_model_authority(client_id: str, message: dict[str, Any]) -> None:
        if isinstance(message, dict) and str(message.get("type") or "") == "chat.request":
            message = dict(message)
            target = model_routing._MODEL_CONTEXT.get() or {}
            authority = _authority(target)
            request_id = str(message.get("request_id") or "")
            if authority:
                options = dict(message.get("options") or {})
                free_route = getattr(registry, "is_free_default_route", None)
                if callable(free_route) and free_route(client_id, authority["routed_model"]):
                    authority = {**authority, "strict": False, "default_page_route": True,
                                 "transport_model": None}
                options["free_default_route"] = authority.get("default_page_route") is True
                options["model_authority"] = authority
                message["options"] = options
                routing = dict(message.get("routing") or {})
                routing.update({
                    "requested_model": authority["requested_model"],
                    "routed_model": authority["routed_model"],
                    "transport_model": authority["transport_model"],
                    "final_model_authority": PATCH_ID,
                    "model_authority_revision": PATCH_REVISION,
                })
                message["routing"] = routing
                if request_id:
                    authorities[request_id] = authority
                    _schedule_telemetry_projection(
                        projection_tasks, telemetry,
                        {
                            "request_id": request_id,
                            **_trace(authority, None),
                            "client_id": client_id,
                        },
                        request_id=request_id,
                        projection="model-routing",
                    )
        await base_send(client_id, message)

    async def publish_with_model_evidence(request_id: str, event: dict[str, Any]) -> bool:
        # The Broker is the sole authority on whether an event belongs to an
        # active request and is still admissible. A late Worker diagnostic or
        # second terminal must never overwrite the accepted model provenance.
        # Publish first; the evidence is an optional projection, not a second
        # decision maker or a reason to fail a client-facing request.
        published = await base_publish(request_id, event)
        if published:
            try:
                evidence = _evidence_from_event(event)
                if evidence:
                    authority = authorities.get(str(request_id))
                    trace = _trace(authority, evidence)
                    state = broker.requests.get(str(request_id))
                    if state is not None:
                        state.diagnostics.update(trace)
                        state.diagnostics["model_evidence_v144"] = evidence
                    # Broker state is updated synchronously above. Persist the
                    # optional history projection off the Worker socket path;
                    # a slow telemetry store must not delay later deltas or
                    # terminal delivery. Per-request chaining preserves order.
                    _schedule_telemetry_projection(
                        projection_tasks, telemetry,
                        {"request_id": str(request_id), **trace},
                        request_id=str(request_id),
                        projection="served-model-evidence",
                    )
            except Exception:
                logger.exception(
                    "Accepted Broker event but failed to project model evidence request_id=%s",
                    request_id,
                )
        if str(event.get("type") or "") in {"chat.completed", "chat.error", "chat.cancelled"}:
            authorities.pop(str(request_id), None)
        return published

    async def release_with_model_authority(request_id: str) -> None:
        # Server-owned timeout, cancellation, admission failure and other
        # non-Worker terminals can release Broker state without a Worker
        # terminal event. Always discard the per-request authority snapshot
        # when the request lease is released, not only on chat.completed.
        try:
            await base_release(request_id)
        finally:
            authorities.pop(str(request_id), None)

    registry.send = send_with_model_authority
    broker.publish = publish_with_model_evidence
    broker.release = release_with_model_authority
    app.state.model_authorities_v144 = authorities
    app.state.model_observability_v144_projection_tasks = projection_tasks
    _patch_admin_html()
    app.state.model_observability_v144_installed = True
    app.state.model_observability_revision = PATCH_REVISION
    return app


__all__ = ["PATCH_ID", "PATCH_REVISION", "install_model_observability_v144_patch"]