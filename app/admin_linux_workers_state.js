(() => {
  const openDetails = new Set();

  const installIdForRow = row => {
    if (!(row instanceof HTMLTableRowElement)) return "";
    const copyButton = row.querySelector("[data-copy-install]");
    return String(copyButton?.dataset?.copyInstall || "");
  };

  const detailKey = details => {
    if (!(details instanceof HTMLDetailsElement)) return "";
    const row = details.closest("tr");
    const installId = installIdForRow(row);
    if (!installId) return "";
    if (details.querySelector("[data-copy-install]")) return `command:${installId}`;
    const cell = details.closest("td");
    if (cell && row?.children?.[2] === cell) return `progress:${installId}`;
    return "";
  };

  const findCopyButton = (rows, installId) => {
    for (const button of rows.querySelectorAll("[data-copy-install]")) {
      if (String(button.dataset.copyInstall || "") === installId) return button;
    }
    return null;
  };

  const restoreOpenDetails = () => {
    const rows = document.getElementById("linuxWorkerRows");
    if (!rows || !openDetails.size) return;
    for (const key of openDetails) {
      const separator = key.indexOf(":");
      if (separator < 0) continue;
      const kind = key.slice(0, separator);
      const installId = key.slice(separator + 1);
      const copyButton = findCopyButton(rows, installId);
      const row = copyButton?.closest("tr");
      let details = null;
      if (kind === "command") details = copyButton?.closest("details") || null;
      if (kind === "progress") details = row?.children?.[2]?.querySelector("details") || null;
      if (details && !details.open) details.open = true;
    }
  };

  document.addEventListener("toggle", event => {
    const details = event.target;
    const key = detailKey(details);
    if (!key) return;
    if (details.open) openDetails.add(key);
    else openDetails.delete(key);
  }, true);

  const attach = () => {
    const rows = document.getElementById("linuxWorkerRows");
    if (!rows) {
      setTimeout(attach, 50);
      return;
    }
    new MutationObserver(() => restoreOpenDetails()).observe(rows, {
      childList: true,
      subtree: true,
    });
    restoreOpenDetails();
  };

  attach();
})();
