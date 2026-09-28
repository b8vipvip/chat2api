from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import admin as admin_module
from . import model_capability_routing_patch as model_routing
from .model_catalog import model_transport_id, normalize_model_id, normalize_reasoning_level

PATCH_ID = "final-model-authority-v144"
PATCH_REVISION = 144


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
    return dict(raw) if isinstance(raw, dict) else None


def _trace(authority: dict[str, Any] | None, evidence: dict[str, Any] | None) -> dict[str, Any]:
    authority = dict(authority or {})
    evidence = dict(evidence or {})
    requested = normalize_model_id(authority.get("requested_model") or evidence.get("authority_requested_model"))
    routed = normalize_model_id(authority.get("routed_model") or evidence.get("authority_routed_model") or requested)
    request_model = normalize_model_id(evidence.get("request_model"))
    served = normalize_model_id(evidence.get("served_model"))
    default_model = normalize_model_id(evidence.get("default_model"))
    observed = served or default_model
    conflict = bool(evidence.get("model_conflict"))
    authority_violation = bool(routed and request_model and routed != request_model)
    verified = bool(routed and observed and routed == observed and not conflict and not authority_violation)
    level = "served" if served else "profile-default" if default_model else "unverified"
    fallback_reason = str(evidence.get("fallback_reason") or "").strip() or None
    if authority_violation:
        fallback_reason = fallback_reason or "worker_request_model_rewrite"
    elif routed and served and routed != served:
        fallback_reason = fallback_reason or "upstream_served_model_mismatch"
    elif routed and default_model and routed != default_model:
        fallback_reason = fallback_reason or "upstream_default_model_mismatch"
    return {
        "requested_model": requested,
        "routed_model": routed,
        "transport_model": authority.get("transport_model") or model_transport_id(routed),
        "worker_request_model": request_model,
        "served_model": served,
        "observed_model": observed,
        "default_model": default_model,
        "model_verified": verified,
        "model_conflict": conflict,
        "model_authority_violation": authority_violation,
        "model_verification_level": level,
        "model_evidence_source": evidence.get("evidence_source") or ("network_response_metadata" if observed else None),
        "model_evidence_field": evidence.get("model_field") or evidence.get("default_model_field"),
        "model_fallback_reason": fallback_reason,
        "final_model_authority": PATCH_ID,
        "model_authority_revision": PATCH_REVISION,
    }


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

    registry = app.state.registry
    broker = app.state.broker
    telemetry = app.state.telemetry
    base_send: Callable[[str, dict[str, Any]], Awaitable[None]] = registry.send
    base_publish = broker.publish
    authorities: dict[str, dict[str, Any]] = {}

    async def send_with_model_authority(client_id: str, message: dict[str, Any]) -> None:
        if isinstance(message, dict) and str(message.get("type") or "") == "chat.request":
            message = dict(message)
            target = model_routing._MODEL_CONTEXT.get() or {}
            authority = _authority(target)
            request_id = str(message.get("request_id") or "")
            if authority:
                options = dict(message.get("options") or {})
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
                    await telemetry.upsert({"request_id": request_id, **_trace(authority, None), "client_id": client_id})
        await base_send(client_id, message)

    async def publish_with_model_evidence(request_id: str, event: dict[str, Any]) -> bool:
        evidence = _evidence_from_event(event)
        if evidence:
            authority = authorities.get(str(request_id))
            trace = _trace(authority, evidence)
            state = broker.requests.get(str(request_id))
            if state is not None:
                state.diagnostics.update(trace)
                state.diagnostics["model_evidence_v144"] = evidence
            await telemetry.upsert({"request_id": str(request_id), **trace})
        published = await base_publish(request_id, event)
        if str(event.get("type") or "") in {"chat.completed", "chat.error", "chat.cancelled"}:
            authorities.pop(str(request_id), None)
        return published

    registry.send = send_with_model_authority
    broker.publish = publish_with_model_evidence
    _patch_admin_html()
    app.state.model_observability_v144_installed = True
    app.state.model_observability_revision = PATCH_REVISION
    return app


__all__ = ["PATCH_ID", "PATCH_REVISION", "install_model_observability_v144_patch"]
