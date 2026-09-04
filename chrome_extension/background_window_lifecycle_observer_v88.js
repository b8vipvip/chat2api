(() => {
  const KEY = "__CHAT2API_WINDOW_LIFECYCLE_OBSERVER_V88__";
  if (globalThis[KEY]) return;

  const MANAGER_KEY = "__CHAT2API_WINDOW_MANAGER_V88__";
  const state = {
    revision: 88,
    observed_creates: 0,
  };
  globalThis[KEY] = state;

  const baseCreate = chrome.windows.create.bind(chrome.windows);

  function isChatGptUrl(value = "") {
    try {
      return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
    } catch (_) {
      return false;
    }
  }

  function manager() {
    return globalThis[MANAGER_KEY] || null;
  }

  function firstChatGptTab(win) {
    return (win?.tabs || []).find(tab =>
      Number.isInteger(tab?.id) && isChatGptUrl(tab.url || tab.pendingUrl || "")
    ) || null;
  }

  function registerLoading(win, openedAt) {
    const wm = manager();
    if (!wm?.active || !Number.isInteger(win?.id)) return false;
    const tab = firstChatGptTab(win);
    if (!tab) return false;

    let record = wm.active.get(win.id) || null;
    if (!record) {
      record = {
        window_no: Number(wm.nextWindowNo || 1),
        window_id: win.id,
        tab_id: tab.id,
        opened_at_ms: openedAt,
        opened_at: new Date(openedAt).toISOString(),
        status: "loading",
        request_id: null,
        route_key: null,
        source: "creation-observer-v88",
        ready_at_ms: 0,
        last_seen_at_ms: Date.now(),
        screenshot_data_url: null,
        screenshot_at_ms: 0,
        screenshot_at: null,
        screenshot_error: null,
      };
      wm.nextWindowNo = Number(record.window_no) + 1;
      wm.active.set(win.id, record);
    } else {
      record.opened_at_ms = Math.min(Number(record.opened_at_ms || openedAt), openedAt);
      record.opened_at = new Date(record.opened_at_ms).toISOString();
      record.tab_id = tab.id;
      record.status = record.status || "loading";
      record.last_seen_at_ms = Date.now();
    }
    state.observed_creates += 1;
    wm.report?.(true)?.catch?.(() => {});
    wm.reconcile?.(true)?.catch?.(() => {});
    return true;
  }

  chrome.windows.create = async function chat2apiObservedWindowCreate(createData, ...args) {
    const openedAt = Date.now();
    const result = await baseCreate(createData, ...args);
    if (registerLoading(result, openedAt)) return result;

    // Chrome may return before a URL is committed. Check the newly created
    // window again shortly afterward so warm/reserve windows appear in the
    // console as “加载中” instead of materializing only after they become ready.
    if (Number.isInteger(result?.id)) {
      setTimeout(async () => {
        try {
          const hydrated = await chrome.windows.get(result.id, { populate: true });
          registerLoading(hydrated, openedAt);
        } catch (_) {}
      }, 80);
    }
    return result;
  };
})();
