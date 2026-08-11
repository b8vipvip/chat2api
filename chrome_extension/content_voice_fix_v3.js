(() => {
  const KEY = "__CHAT2API_VOICE_TRIGGER_FIX_V3__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { observer: null, activeUntil: 0, last: null };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const labelOf = el => normalize(`${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.title || ""} ${el?.innerText || el?.textContent || ""}`);

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function voiceReady() {
    if ([...document.querySelectorAll("[data-testid='voice-floating-orb'],[data-testid*='voice-orb']")].some(visible)) return true;
    const body = normalize(document.body?.innerText || "");
    return /(准备好了，?\s*随时开始|ready when you are|start speaking|可以开始说话)/i.test(body);
  }

  function score(button) {
    if (!visible(button) || button.disabled) return -10000;
    const label = labelOf(button);
    const testid = String(button.dataset?.testid || "");
    if (/(添加文件|附件|upload|attach|composer-plus|添加照片|听写|dictat|microphone|麦克风|send|发送)/i.test(label + " " + testid)) return -10000;
    let value = 0;
    if (/composer-speech-button/i.test(testid)) value += 400;
    if (/voice|speech/i.test(testid)) value += 240;
    if (/启动语音功能|start voice|voice mode|开始语音|启动语音|语音模式/i.test(label)) value += 360;
    if (/voice|语音/i.test(label)) value += 180;
    return value;
  }

  function strictTrigger() {
    const root = composerRoot();
    if (!root) return null;
    const rows = [...root.querySelectorAll("button")]
      .map(button => ({ button, label: labelOf(button), score: score(button) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score);
    return rows[0] || null;
  }

  function annotate(item) {
    if (!item?.button) return null;
    const button = item.button;
    if (!button.dataset.chat2apiVoiceOriginalAria) button.dataset.chat2apiVoiceOriginalAria = button.getAttribute("aria-label") || "";
    button.dataset.chat2apiVoiceTrigger = "v3";
    button.setAttribute("aria-label", `启动语音功能 chat2api voice ${item.label}`.trim());
    state.last = { label: item.label, testid: button.dataset.testid || "", at: Date.now() };
    return state.last;
  }

  function keepAnnotated() {
    if (Date.now() > state.activeUntil) return;
    const item = strictTrigger();
    if (item) annotate(item);
  }

  state.observer = new MutationObserver(() => keepAnnotated());
  state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-label", "data-testid", "disabled"] });

  async function prepare(timeout = 20000) {
    state.activeUntil = Date.now() + Math.max(timeout + 15000, 45000);
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (voiceReady()) return { ready: true, reason: "voice-ui-already-ready", label: "" };
      const item = strictTrigger();
      if (item) return { ready: false, reason: "strict-trigger", ...annotate(item) };
      await delay(120);
    }
    const root = composerRoot() || document;
    const labels = [...root.querySelectorAll("button")].filter(visible).map(labelOf).filter(Boolean).slice(0, 30);
    throw new Error(`ChatGPT Voice button was not found by strict selector. Visible composer buttons: ${labels.join(" | ")}`);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.voice.trigger.prepare.v3") {
      prepare(Number(message.timeout_ms || 20000))
        .then(data => sendResponse({ ok: true, data, controller: "voice-trigger-v3" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-trigger-v3" }));
      return true;
    }
    return false;
  });
})();
