# Window Truth v89

`Window Manager` and the Worker list must distinguish persisted telemetry from current physical Chrome windows.

## Contract

- A cached `window_manager_v88.active` row is historical telemetry until the Worker proves it still exists.
- Server runtime v89 sends `window.manager.refresh` before presenting live active rows.
- Worker v0.8.27+ reconciles both v83 physical-window truth and the v88 managed-window registry against `chrome.windows.getAll({populate:true})`, then force-reports a fresh `updated_at_ms`.
- The server waits for that timestamp to advance and only then accepts that Worker's `active` rows as live.
- Offline, old, or timed-out Workers may keep historical telemetry for diagnostics, but cached active rows are suppressed from the live table.
- Worker list `请求 / 实际窗口` means active API requests / freshly verified managed ChatGPT windows. Concurrency limit remains a separate setting.
