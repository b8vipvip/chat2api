importScripts(
  "background.js",
  "background_device_v17.js",
  "background_time_v14.js",
  "background_platform_v26.js",
  "background_network_v26.js",
  "content_bootstrap.js",
  "background_login_v27.js",
  "background_worker_binding_v30.js",
  "background_site_permissions_v31.js",
  "background_hardening.js",
  "background_socket_singleflight_v21.js",
  "browser_tabs.js",
  "background_window_open_stagger_v85.js",
  "background_worker_disabled_window_guard_v86.js",
  "background_rate_limit_guard_v52.js",
  "background_tab_supervisor_v32.js",
  "model_routing_v2.js",
  "background_page_smoke_v22.js",
  "background_multimodal_quota_v36.js",
  "background_account_v20.js",
  "model_prefetch_fast_v21.js",
  "image_routing.js",
  "voice_routing.js",
  "audio_routing_v2.js",
  "image_routing_v3.js",
  "audio_routing_v3.js",
  "audio_routing_v4.js",
  "background_logging.js",
  "model_affinity_v23.js",
  "model_contract_v25.js",
  "background_standby_storage_lease_v87.js",
  "conversation_routing.js",
  "conversation_warm_pool_v2.js",
  "background_external_warm_v28.js",
  "background_reserve_pool_v29.js",
  "background_window_truth_v83.js",
  "background_reserve_status_reconnect_v29.js",
  "conversation_workers_v25.js",
  "conversation_dispatch.js",
  "background_route_quarantine_v50.js",
  "background_tool_isolation_v48.js",
  "background_runtime_preflight_v48.js",
  "background_request_hygiene_v42.js",
  "background_request_recovery_v40.js",
  "background_transport_recovery_v47.js",
  "audio_routing_live.js",
  "background_capacity_control_v35.js",
  "background_capacity_control_v36.js",
  "background_capacity_capability_v37.js",
  "background_worker_master_switch_v61.js",
  "background_window_affinity_v87.js",
  "background_orphan_route_cleanup_v87.js",
  "background_window_manager_v88.js",
  "background_window_lifecycle_observer_v88.js",
  "background_window_truth_refresh_v89.js",
);

// Refresh the lease of already healthy standby windows before the historical
// warm/reserve reconciliation timers get a chance to retire them purely because
// their readiness timestamp aged out. Broken/missing windows still fail the
// health probe and are replaced by the normal pool reconcilers.
globalThis.__CHAT2API_WINDOW_AFFINITY_V87__?.refreshHealthySpareLeases?.().catch?.(() => {});
// v88 remains the final routing authority. v89 is telemetry/control only: after
// v88 has built the final ownership graph, it can demand-reconcile that graph
// against chrome.windows.getAll() before the server presents it as live truth.
globalThis.__CHAT2API_WINDOW_MANAGER_V88__?.reconcile?.(true).catch?.(() => {});
globalThis.__CHAT2API_WINDOW_TRUTH_REFRESH_V89__?.refresh?.("background-entry").catch?.(() => {});
