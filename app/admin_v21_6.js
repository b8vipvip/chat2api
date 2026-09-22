(() => {
  const VERSION = "0.22.94-health-canonicalized";
  globalThis.__CHAT2API_EXTENSION_RENDER_OWNER_V59__ = {
    worker_window: "admin_extension_columns",
    health_columns: ["network", "chatgpt"],
    health_summary: "admin_extension_columns",
    rule: "single-visible-render-owner",
    health_poll_ms: 5000,
    chained_capacity_poll: false,
    legacy_health_renderer_removed: true,
  };
  document.documentElement.dataset.chat2apiHealthCenterVersion = VERSION;
})();