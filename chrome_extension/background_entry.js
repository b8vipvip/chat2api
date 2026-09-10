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

  // v0.8.30 ownership boundary:
  // conversation_routing.js is the only authority allowed to select, create,
  // reuse, rotate, retire, or idle-close a ChatGPT route window for an API key.
  // conversation_dispatch.js only transports an already admitted request to
  // that route. Same-key FIFO admission lives on server scheduler v58.
  "conversation_routing.js",
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

// Report physical route truth without reconciling, allocating, recycling or
// protecting windows. All lifecycle decisions remain in conversation_routing.js.
globalThis.__CHAT2API_WINDOW_OBSERVER_V90__?.report?.(true).catch?.(() => {});
