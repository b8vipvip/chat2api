(() => {
  const VERSION = "0.22.102-capacity-v152";
  const MIN_LIMIT = 1;
  const MAX_LIMIT = 32;
  let keyRenderInFlight = false;
  let keyRefreshScheduled = false;

  function keyViewActive() {
    return document.getElementById("view-keys")?.classList.contains("active");
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  }

  function ensureToastHost() {
    let host = document.getElementById("chat2apiActionToastHost");
    if (host) return host;
    host = document.createElement("div");
    host.id = "chat2apiActionToastHost";
    host.style.cssText = "position:fixed;top:18px;right:18px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:min(520px,calc(100vw - 36px));pointer-events:none";
    document.body.appendChild(host);
    return host;
  }

  function showActionResult(message, level = "ok", button = null) {
    const text = String(message || "").trim();
    if (!text) return;
    if (button) button.title = text;
    if (typeof globalThis.status === "function") globalThis.status(text, level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    const toast = document.createElement("div");
    toast.textContent = text;
    toast.style.cssText = "pointer-events:auto;padding:10px 12px;border:1px solid rgba(148,163,184,.32);border-radius:10px;background:#0f172a;box-shadow:0 14px 36px rgba(0,0,0,.4);font-size:13px;line-height:1.45;white-space:normal";
    toast.classList.add(level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    ensureToastHost().appendChild(toast);
    setTimeout(() => toast.remove(), 4800);
  }
  globalThis.chat2apiActionToast = showActionResult;

  // v0.22.102: Worker settings/list rendering has one structural owner only:
  // app/admin_extension_columns.js (canonical-worker-list-v152).  The former
  // v59 renderer is deliberately retired instead of being kept as a hidden or
  // delayed fallback, because it caused the old table to flash on every refresh.
  globalThis.__CHAT2API_WORKER_SETTINGS_RENDER_OWNER_V59__ = Object.freeze({
    retired: true,
    structural_owner: false,
    delegated_to: "admin_extension_columns-v152",
    renderer: "canonical-worker-list-v152",
  });

  // API Key concurrency editor retired. Only real Worker capacity is bounded.

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · ${VERSION}`;
})();