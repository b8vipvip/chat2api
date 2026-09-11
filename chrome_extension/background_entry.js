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

  // Terminal reporting only. Register its tab/window removal listeners before
  // the router listeners so it can snapshot an active request before the sole
  // lifecycle authority clears route state for the removed browser object.
  // This module still has no create/remove/mutation authority of its own.
  "background_route_close_terminal_v91.js",

  // v0.8.32 request/window ownership boundary:
  // conversation_routing.js remains the only module that creates/selects API
  // route windows. background_window_limit_v121.js is a bounded admission and
  // idle-eviction guard around that authority: it never creates a window and it
  // never closes an in-flight route. There is still no speculative warm pool.
  "conversation_routing.js",
  "background_window_limit_v121.js",
  "conversation_dispatch.js",

  "background_tool_isolation_v48.js",
  "background_runtime_preflight_v48.js",
  "background_request_hygiene_v42.js",
  "background_transport_recovery_v47.js",
  "audio_routing_live.js",
  "background_capacity_control_v35.js",
  "background_capacity_control_v36.js",
  "background_capacity_capability_v37.js",
  "background_worker_master_switch_v61.js",

  // Observation/capture only. It has no route/window mutation authority.
  "background_window_observer_v90.js",
);

globalThis.__CHAT2API_WINDOW_OBSERVER_V90__?.report?.(true).catch?.(() => {});
