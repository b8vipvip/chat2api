/* Account-scoped Web Chat image drag/drop adapter.
 * This module only transports File objects to the existing validated picker
 * callback; MIME/size/key ownership remains in chat_client_v1.js and FileStore.
 */
(() => {
  "use strict";

  function isFileDrag(event) {
    const types = event && event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.from(types).includes("Files");
  }

  function install(target, documentRef, handlers) {
    if (!target || typeof target.addEventListener !== "function") {
      throw new TypeError("A composer drop target is required");
    }
    if (!documentRef || typeof documentRef.addEventListener !== "function") {
      throw new TypeError("A document event target is required");
    }
    const onFiles = handlers && typeof handlers.onFiles === "function" ? handlers.onFiles : () => {};
    const onActive = handlers && typeof handlers.onActive === "function" ? handlers.onActive : () => {};
    let depth = 0;
    let destroyed = false;

    function activate(value) {
      try { onActive(value); } catch { /* UI decoration must not break file handling. */ }
    }
    function enter(event) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      depth += 1;
      activate(true);
    }
    function over(event) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    }
    function leave(event) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      depth = Math.max(0, depth - 1);
      if (depth === 0) activate(false);
    }
    function drop(event) {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      depth = 0;
      activate(false);
      const files = Array.from(event.dataTransfer.files || []);
      if (files.length) {
        try {
          const result = onFiles(files);
          if (result && typeof result.catch === "function") {
            result.catch(() => { /* The existing picker owns user-facing errors. */ });
          }
        } catch { /* The existing picker owns user-facing errors. */ }
      }
    }
    function preventBrowserOpen(event) {
      if (isFileDrag(event)) event.preventDefault();
    }

    target.addEventListener("dragenter", enter);
    target.addEventListener("dragover", over);
    target.addEventListener("dragleave", leave);
    target.addEventListener("drop", drop);
    documentRef.addEventListener("dragover", preventBrowserOpen);
    documentRef.addEventListener("drop", preventBrowserOpen);

    return function destroy() {
      if (destroyed) return;
      destroyed = true;
      depth = 0;
      activate(false);
      target.removeEventListener("dragenter", enter);
      target.removeEventListener("dragover", over);
      target.removeEventListener("dragleave", leave);
      target.removeEventListener("drop", drop);
      documentRef.removeEventListener("dragover", preventBrowserOpen);
      documentRef.removeEventListener("drop", preventBrowserOpen);
    };
  }

  Object.defineProperty(window, "Chat2APIImageDropV1", {
    value:Object.freeze({install, isFileDrag}),
    writable:false,
    configurable:false,
  });
})();