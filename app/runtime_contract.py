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
# server runtime/console, the Worker wire protocol, the shipped unpacked
# Worker bundle, and the realtime wire protocol can evolve independently.
SERVER_RUNTIME_VERSION = "0.22.58"
# v0.22.24 remains the compatibility baseline for Workers that first gained the
# bounded initialize/recovery command. Later runtimes can evolve control-plane
# behavior without changing that bootstrap compatibility floor.
WORKER_INITIALIZE_BASELINE_SERVER_RUNTIME_VERSION = "0.22.24"
CHROME_BRIDGE_VERSION = "0.8.1"
CHROME_BRIDGE_BUNDLE_VERSION = "0.8.27"
PRODUCTION_ENTRYPOINT = "app.entry:app"
VERSION_CONTRACT_VERSION = 1
RUNTIME_FEATURE_REVISION = "capacity-native-v37-bundle-0819-runtime-logs-v1-playground-lifecycle-v1-playground-chat-v3-spare-freshness-v39-response-capture-v41-request-hygiene-v42-persistent-draft-ownership-v43-generation-liveness-v49-worker-initialize-v43-worker-online-upgrade-v44-worker-master-switch-v61-r62-worker-disable-authority-v62-worker-console-freeze-v22-27-server-update-recreate-guard-v22-28-server-update-poll-timeout-v22-29-github-transport-failover-v22-30-worker-transport-v47-device-identity-v47-response-stream-v49-network-response-v55-parser-v62-same-api-concurrency-v25-tool-isolation-v48-runtime-preflight-v48-worker-sudoers-guard-v22-33-request-lifecycle-v50-route-quarantine-v50-transient-retry-v50-autoreload-self-heal-v50-server-worker-auto-sync-v1-response-semantic-guard-v1-response-semantic-recovery-v51-helper-model-capability-routing-v2-rate-limit-guard-v52-single-response-owner-v53-generation-backend-health-v54-proxy-health-v55-worker-key-capacity-queue-v57-active-rate-limit-terminal-v56-admin-render-owner-v58-routed-dispatch-terminal-v58-worker-live-occupancy-v61-multimodal-upload-v64-worker-presentation-v64-worker-presentation-v65-console-liveness-v65-worker-presentation-v66-column-registry-v67-multimodal-upload-v68-api-key-console-v68-rich-response-v69-content-runtime-v71-multimodal-main-world-v78-active-request-disable-lease-v79-multimodal-upload-settle-v79-submission-liveness-v79-linux-window-response-lifecycle-v81-unicode-attachment-download-v82-live-worker-window-count-v82-runtime-version-observability-v82-physical-window-truth-v83-multimodal-upload-ready-v84-multimodal-safe-submit-v85-affinity-idle-5m-window-stagger-v85-spare-target-semantics-v85-worker-disabled-window-guard-v86-success-route-preservation-v86-runtime-preflight-fast-path-v86-request-prompt-viewer-repair-v86-window-affinity-v87-healthy-spare-lease-v87-stale-route-cleanup-v87-runtime-preflight-budget-v87-window-manager-fifo-v88-window-lifecycle-observer-v88-success-terminal-monotonic-v88-long-prompt-fast-insert-v88-admin-window-manager-v88-admin-navigation-freeze-v89-physical-window-live-truth-v89-release-v02258"
ADMIN_VERSION_ASSET = "/assets/chat2api-runtime-version.js"
ADMIN_EXTENSION_COLUMNS_ASSET = "/assets/chat2api-extension-columns.js"
ADMIN_LINUX_WORKERS_ASSET = "/assets/chat2api-linux-workers.js"
ADMIN_LINUX_PROXY_CATALOG_ASSET = "/assets/chat2api-linux-worker-proxy-catalog.js"


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
            "feature_revision": RUNTIME_FEATURE_REVISION,
        },
        "chrome_bridge": {
            "version": CHROME_BRIDGE_VERSION,
            "bundle_version": CHROME_BRIDGE_BUNDLE_VERSION,
            "build_revision": "capacity-native-v37-r2-spare-freshness-v39-response-capture-v41-request-hygiene-v42-persistent-draft-ownership-v43-generation-liveness-v49-response-stream-v49-network-response-v55-parser-v62-same-api-concurrency-v25-tool-isolation-v48-runtime-preflight-v48-request-lifecycle-v50-route-quarantine-v50-transient-retry-v50-autoreload-self-heal-v50-response-semantic-recovery-v51-helper-model-capability-routing-v2-rate-limit-guard-v52-single-response-owner-v53-generation-backend-health-v54-proxy-health-v55-active-rate-limit-terminal-v56-worker-key-capacity-queue-v57-routed-dispatch-terminal-v58-worker-master-switch-v61-r62-multimodal-upload-v64-multimodal-upload-v68-rich-response-v69-content-runtime-v71-multimodal-main-world-v78-active-request-disable-lease-v79-multimodal-upload-settle-v79-submission-liveness-v79-physical-window-truth-v83-multimodal-upload-ready-v84-multimodal-safe-submit-v85-affinity-idle-5m-window-stagger-v85-spare-target-semantics-v85-worker-disabled-window-guard-v86-success-route-preservation-v86-runtime-preflight-fast-path-v86-request-prompt-viewer-repair-v86-window-affinity-v87-healthy-spare-lease-v87-stale-route-cleanup-v87-runtime-preflight-budget-v87-window-manager-fifo-v88-window-lifecycle-observer-v88-success-terminal-monotonic-v88-long-prompt-fast-insert-v88-release-v0826",
            "capacity_control_version": 36,
            "capacity_reporter_version": 37,
            "worker_master_switch_version": 61,
            "worker_master_switch_revision": 62,
            "network_response_recovery_version": 55,
            "network_response_parser_revision": 62,
            "multimodal_revision": 85,
        },
        "features": {
            "runtime_logs": True,
            "runtime_log_export": True,
            "native_capacity_control": True,
            "worker_extension_runtime_diagnostics": True,
            "bridge_service_worker_cache_bust": True,
            "persistent_playground_runs": True,
            "playground_chat_window": True,
            "playground_chat_records": True,
            "playground_chat_running_records": True,
            "model_capability_routing_guard": True,
            "model_capability_routing_v2": True,
            "chatgpt_rate_limit_circuit_breaker": True,
            "worker_window_reopen_loop_guard": True,
            "active_rate_limit_terminal_error": True,
            "routed_dispatch_terminal_error": True,
            "admin_single_render_owner": True,
            "worker_key_capacity_fifo_queue": True,
            "worker_window_concurrency_controls": True,
            "api_key_concurrency_controls": True,
            "running_request_history": True,
            "playground_cancellation": True,
            "generation_activity_watchdog": True,
            "fresh_spare_rotation": True,
            "terminal_request_recovery": True,
            "failed_route_recycle": True,
            "failed_route_quarantine": True,
            "rendered_response_capture_recovery": True,
            "response_stream_recovery": True,
            "network_response_recovery": True,
            "network_response_parser_v62": True,
            "single_response_observer": True,
            "assistant_response_semantic_guard": True,
            "assistant_response_semantic_recovery": True,
            "browser_page_progress_probe": True,
            "same_api_parallel_requests": True,
            "request_controller_lifecycle_guard": True,
            "chatgpt_transient_retry": True,
            "worker_runtime_preflight": True,
            "external_account_tool_isolation": True,
            "managed_request_draft_recovery": True,
            "persistent_request_draft_ownership": True,
            "visible_generation_liveness": True,
            "linux_worker_initialize": True,
            "linux_worker_bridge_runtime_recovery": True,
            "linux_worker_autoreload_self_heal": True,
            "linux_worker_online_upgrade": True,
            "linux_worker_upgrade_live_progress": True,
            "linux_worker_sudoers_guard": True,
            "linux_worker_routing_toggle": True,
            "linux_worker_master_switch": True,
            "linux_worker_disable_authority": True,
            "worker_live_occupancy": True,
            "worker_live_window_count_v82": True,
            "worker_physical_window_truth_v83": True,
            "worker_physical_window_live_truth_v89": True,
            "worker_device_name_column": True,
            "worker_pairing_rename": True,
            "worker_presentation_console_liveness_v65": True,
            "worker_presentation_console_liveness_v66": True,
            "worker_column_registry_v67": True,
            "multimodal_upload_confirmation_v64": True,
            "multimodal_upload_v68": True,
            "multimodal_main_world_v78": True,
            "active_request_disable_lease_v79": True,
            "multimodal_upload_settle_v79": True,
            "multimodal_upload_ready_v84": True,
            "multimodal_safe_submit_v85": True,
            "conversation_affinity_idle_5m_v85": True,
            "managed_window_open_stagger_v85": True,
            "reserve_spare_target_semantics_v85": True,
            "worker_disabled_window_guard_v86": True,
            "successful_route_preservation_v86": True,
            "runtime_preflight_fast_path_v86": True,
            "request_prompt_viewer_repair_v86": True,
            "window_affinity_v87": True,
            "healthy_spare_lease_refresh_v87": True,
            "stale_route_window_cleanup_v87": True,
            "runtime_preflight_budget_v87": True,
            "request_prompt_button_v87": True,
            "worker_window_fifo_manager_v88": True,
            "worker_window_lifecycle_observer_v88": True,
            "successful_terminal_monotonic_v88": True,
            "long_prompt_fast_insert_v88": True,
            "admin_window_manager_v88": True,
            "admin_navigation_freeze_v89": True,
            "admin_window_live_truth_v89": True,
            "request_id_window_correlation_v88": True,
            "submission_liveness_v79": True,
            "runtime_version_observability_v80": True,
            "runtime_version_observability_v82": True,
            "unicode_attachment_download_v82": True,
            "api_key_console_v68": True,
            "rich_response_v69": True,
            "request_response_epoch_v69": True,
            "worker_content_runtime_epoch_v71": True,
            "linux_worker_console_freeze_guard": True,
            "linux_worker_generation_backend_health": True,
            "linux_worker_proxy_health_facets": True,
            "server_update_recreate_guard": True,
            "server_update_poll_timeout_guard": True,
            "github_transport_failover": True,
            "server_update_worker_auto_sync": True,
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
}})();\
'''


def install_runtime_contract(app: FastAPI) -> FastAPI:
    if getattr(app.state, "runtime_contract_installed", False):
        return app

    app.version = SERVER_RUNTIME_VERSION
    app.state.runtime_contract_installed = True

    @app.get("/version", tags=["system"])
    async def version_contract() -> dict[str, Any]:
        return version_contract_payload(app)

    @app.get(ADMIN_VERSION_ASSET, include_in_schema=False)
    async def admin_runtime_version_js() -> Response:
        return Response(
            _admin_version_script(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get(ADMIN_EXTENSION_COLUMNS_ASSET, include_in_schema=False)
    async def admin_extension_columns_js() -> Response:
        path = Path(__file__).with_name("admin_extension_columns.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get(ADMIN_LINUX_WORKERS_ASSET, include_in_schema=False)
    async def admin_linux_workers_js() -> Response:
        return Response(
            Path(__file__).with_name("admin_linux_workers.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(ADMIN_LINUX_PROXY_CATALOG_ASSET, include_in_schema=False)
    async def admin_linux_worker_proxy_catalog_js() -> Response:
        return Response(
            Path(__file__).with_name("admin_linux_worker_proxy_catalog.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def runtime_contract_response(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            version_marker = f'<script src="{ADMIN_VERSION_ASSET}"></script>'
            if version_marker not in text:
                text = text.replace("</body>", version_marker + "</body>")
            if path == "/admin":
                workers_marker = f'<script src="{ADMIN_LINUX_WORKERS_ASSET}"></script>'
                if workers_marker not in text:
                    text = text.replace("</body>", workers_marker + "</body>")
                proxy_catalog_marker = f'<script src="{ADMIN_LINUX_PROXY_CATALOG_ASSET}"></script>'
                if proxy_catalog_marker not in text:
                    text = text.replace("</body>", proxy_catalog_marker + "</body>")
                columns_marker = f'<script src="{ADMIN_EXTENSION_COLUMNS_ASSET}"></script>'
                if columns_marker not in text:
                    text = text.replace("</body>", columns_marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        if path.startswith("/api/admin/") and "application/json" in content_type:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")

            if isinstance(payload, dict):
                if path == "/api/admin/overview" or "version" in payload:
                    payload["version"] = SERVER_RUNTIME_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = SERVER_RUNTIME_VERSION

            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        return response

    return app
