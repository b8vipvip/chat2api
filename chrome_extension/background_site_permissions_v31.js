(() => {
  const KEY = "__CHAT2API_SITE_PERMISSIONS_V31__";
  if (globalThis[KEY]) return;

  const CHATGPT_PATTERNS = [
    "https://chatgpt.com/*",
    "https://www.chatgpt.com/*",
    "https://chat.openai.com/*",
  ];

  const state = {
    applied: false,
    appliedAt: 0,
    failures: [],
  };

  async function setForAll(contentSetting, value) {
    const setting = chrome.contentSettings?.[contentSetting];
    if (!setting?.set) throw new Error(`content setting unavailable: ${contentSetting}`);
    for (const primaryPattern of CHATGPT_PATTERNS) {
      await setting.set({ primaryPattern, setting: value, scope: "regular" });
    }
  }

  async function apply() {
    const failures = [];
    const operations = [
      ["microphone", "allow"],
      ["notifications", "block"],
    ];
    for (const [name, value] of operations) {
      try {
        await setForAll(name, value);
      } catch (error) {
        failures.push(`${name}:${String(error?.message || error).slice(0, 160)}`);
      }
    }
    state.applied = failures.length === 0;
    state.appliedAt = Date.now();
    state.failures = failures;
    return { ...state };
  }

  chrome.runtime.onInstalled.addListener(() => { apply().catch(() => {}); });
  chrome.runtime.onStartup.addListener(() => { apply().catch(() => {}); });
  apply().catch(() => {});

  globalThis[KEY] = Object.freeze({ state, apply, patterns: CHATGPT_PATTERNS.slice() });
})();
