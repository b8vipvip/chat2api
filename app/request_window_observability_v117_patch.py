from __future__ import annotations

"""Request-history window correlation and persisted routing diagnostics.

The browser has always known the routed tab/window and worker slot, but Responses
telemetry could replace the nested diagnostics object at terminalization. Persist
window identity as top-level request fields so the administrator can correlate
same-key requests reliably and downloaded per-request logs keep the same evidence.
"""

from typing import Any

from fastapi import FastAPI

from . import admin as admin_module


PATCH_REVISION = 117
PATCH_ID = "request-window-number-v117"


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _window_fields(diagnostics: dict[str, Any]) -> dict[str, Any]:
    window_number = _int_or_none(
        diagnostics.get("window_number")
        or diagnostics.get("extension_window_number")
        or diagnostics.get("extension_worker_index")
    )
    window_id = _int_or_none(diagnostics.get("routed_window_id") or diagnostics.get("window_id"))
    tab_id = _int_or_none(diagnostics.get("routed_tab_id") or diagnostics.get("tab_id"))
    route_key = str(
        diagnostics.get("extension_worker_route_key")
        or diagnostics.get("conversation_api_key_id")
        or ""
    ).strip() or None
    worker_client_id = str(
        diagnostics.get("worker_client_id")
        or diagnostics.get("client_id")
        or diagnostics.get("extension_client_id")
        or ""
    ).strip() or None
    return {
        "window_number": window_number,
        "window_id": window_id,
        "tab_id": tab_id,
        "window_route_key": route_key,
        "worker_client_id": worker_client_id,
    }


def _registry_window_fields(registry: Any, request_id: str) -> dict[str, Any]:
    request_id = str(request_id or "").strip()
    if not request_id:
        return {}
    for summary in registry.summaries():
        client_id = str(summary.get("client_id") or "").strip() or None
        metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
        snapshot = metadata.get("window_manager_v88") if isinstance(metadata.get("window_manager_v88"), dict) else {}
        for bucket in (snapshot.get("active") or [], snapshot.get("closed") or []):
            if not isinstance(bucket, dict) or str(bucket.get("request_id") or "").strip() != request_id:
                continue
            return {
                "window_number": _int_or_none(bucket.get("window_no") or bucket.get("window_number")),
                "window_id": _int_or_none(bucket.get("window_id")),
                "tab_id": _int_or_none(bucket.get("tab_id")),
                "window_route_key": str(bucket.get("route_key") or bucket.get("api_key_id") or "").strip() or None,
                "worker_client_id": client_id,
            }
    return {}


def _patch_admin_html() -> None:
    html = admin_module.ADMIN_HTML
    if 'data-request-window-number-v117="1"' in html:
        return

    header_old = "<th>设备标识</th><th>模型</th>"
    header_new = '<th>设备标识</th><th data-request-window-number-v117="1">窗口标识</th><th>模型</th>'
    if html.count(header_old) != 1:
        raise RuntimeError(f"{PATCH_ID}: expected one canonical request-history device/model header")
    html = html.replace(header_old, header_new, 1)

    row_old = """      requestHistoryCell(tr,r?.device_name||(clientId?`未绑定 · ${clientId}`:'-'));
      requestHistoryCell(tr,r?.requested_model||r?.model);"""
    row_new = """      requestHistoryCell(tr,r?.device_name||(clientId?`未绑定 · ${clientId}`:'-'));
      const requestDiagnostics=(r?.diagnostics&&typeof r.diagnostics==='object')?r.diagnostics:{};
      const windowNumber=r?.window_number??requestDiagnostics?.window_number??requestDiagnostics?.extension_window_number??requestDiagnostics?.extension_worker_index??null;
      const windowWorkerId=r?.worker_client_id??r?.client_id??requestDiagnostics?.worker_client_id??requestDiagnostics?.client_id??requestDiagnostics?.extension_client_id??'';
      const workerTail=String(windowWorkerId||'').trim().slice(-4)||'????';
      const windowIdentity=windowNumber?`${workerTail}#${windowNumber}`:'-';
      const windowCell=requestHistoryCell(tr,windowIdentity);
      const rawWindowId=r?.window_id??requestDiagnostics?.routed_window_id??'-';
      const rawTabId=r?.tab_id??requestDiagnostics?.routed_tab_id??'-';
      const routeKey=r?.window_route_key??requestDiagnostics?.extension_worker_route_key??requestDiagnostics?.conversation_api_key_id??'-';
      windowCell.title=windowNumber?`窗口标识 ${windowIdentity} · window_id=${rawWindowId} · tab_id=${rawTabId} · route=${routeKey}`:`未记录窗口 · window_id=${rawWindowId} · tab_id=${rawTabId}`;
      requestHistoryCell(tr,r?.requested_model||r?.model);"""
    if html.count(row_old) != 1:
        raise RuntimeError(f"{PATCH_ID}: expected one canonical request-history row renderer")
    html = html.replace(row_old, row_new, 1)

    colspan_count = html.count("colSpan=13")
    if colspan_count != 3:
        raise RuntimeError(f"{PATCH_ID}: expected three canonical request-history colSpan=13 markers, found {colspan_count}")
    html = html.replace("colSpan=13", "colSpan=14")
    admin_module.ADMIN_HTML = html


def install_request_window_observability_v117_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "request_window_observability_v117_installed", False):
        return app

    _patch_admin_html()
    broker = app.state.broker
    telemetry = app.state.telemetry
    registry = app.state.registry
    base_publish = broker.publish

    async def publish_with_window_observability(request_id: str, event: dict[str, Any]) -> bool:
        diagnostics = event.get("diagnostics") if isinstance(event, dict) else None
        fields = _window_fields(diagnostics) if isinstance(diagnostics, dict) else {
            "window_number": None,
            "window_id": None,
            "tab_id": None,
            "window_route_key": None,
            "worker_client_id": None,
        }
        registry_fields = _registry_window_fields(registry, request_id)
        for key, value in registry_fields.items():
            if fields.get(key) is None and value is not None:
                fields[key] = value

        if isinstance(diagnostics, dict):
            if fields["window_number"] is not None:
                diagnostics.setdefault("window_number", fields["window_number"])
                diagnostics.setdefault("extension_window_number", fields["window_number"])
            if fields["window_id"] is not None:
                diagnostics.setdefault("window_id", fields["window_id"])
            if fields["tab_id"] is not None:
                diagnostics.setdefault("tab_id", fields["tab_id"])
            if fields["worker_client_id"] is not None:
                diagnostics.setdefault("worker_client_id", fields["worker_client_id"])

        published = await base_publish(request_id, event)
        if published:
            if fields.get("window_number") is None or fields.get("worker_client_id") is None:
                later = _registry_window_fields(registry, request_id)
                for key, value in later.items():
                    if fields.get(key) is None and value is not None:
                        fields[key] = value
            if any(value is not None for value in fields.values()):
                await telemetry.upsert({"request_id": request_id, **fields})
        return published

    broker.publish = publish_with_window_observability
    app.state.request_window_observability_v117_installed = True
    app.state.request_window_observability_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_window_fields",
    "install_request_window_observability_v117_patch",
]
