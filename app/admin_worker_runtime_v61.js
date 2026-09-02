(() => {
  const KEY = "__CHAT2API_WORKER_RUNTIME_V61__";
  if (globalThis[KEY]) return;

  // Compatibility shim. The historical v61 runtime injected an independent
  // `occupied_windows` column and refreshed it continuously. The canonical
  // Worker list now owns the single `occupancy` column, whose value includes
  // both used and configured capacity (for example 0 / 3).
  const state = Object.freeze({
    version: 61,
    retired: true,
    replacement_column: "occupancy",
    revision: 67,
  });
  globalThis[KEY] = state;

  function removeLegacyColumn() {
    document
      .querySelectorAll('[data-chat2api-column-key="occupied_windows"]')
      .forEach(node => node.remove());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", removeLegacyColumn, {once: true});
  } else {
    removeLegacyColumn();
  }
})();
