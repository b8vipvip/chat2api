(() => {
  const KEY = "__CHAT2API_MULTIMODAL_QUOTA_V36__";
  if (globalThis[KEY]) return;

  const ACTIVE_MS = 120000;
  const MAX_RESET_MS = 31 * 24 * 60 * 60 * 1000;
  const state = {
    activeUntil: 0,
    attachmentNames: [],
    lastFingerprint: "",
    observer: null,
    interval: null,
  };

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(node) {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function quotaText(value) {
    const text = normalize(value);
    if (!text || text.length < 8 || text.length > 2400) return false;
    const quota = /(?:reached|hit|exceeded|used up|ran out of).{0,45}(?:limit|quota)|(?:limit|quota).{0,45}(?:reached|exceeded|used up)|too many (?:files|uploads)|file upload limit|upload quota|已达到.{0,36}(?:限制|上限|额度)|(?:限制|上限|额度).{0,36}(?:已用完|已耗尽|已达到)|(?:文件|图片|上传|视觉).{0,36}(?:限制|上限|额度)/i.test(text);
    const recovery = /(?:reset|resets|available again|try again|come back|after|until|in \d+\s*(?:min|minute|hour|hr))|(?:恢复|重置|后重试|再试|分钟后|小时后|明天)/i.test(text);
    return quota && recovery;
  }

  function clampRecovery(candidate, nowMs) {
    const value = Number(candidate || 0);
    if (!Number.isFinite(value) || value <= nowMs + 1000) return null;
    if (value - nowMs > MAX_RESET_MS) return null;
    return Math.round(value);
  }

  function parseRelative(text, nowMs) {
    let match = text.match(/\b(?:in|after)\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\b/i);
    if (match) {
      const amount = Number(match[1]);
      const unit = String(match[2]).toLowerCase();
      const multiplier = unit.startsWith("h") ? 60 * 60 * 1000 : 60 * 1000;
      return clampRecovery(nowMs + amount * multiplier, nowMs);
    }
    match = text.match(/(\d{1,4})\s*(分钟|小时)\s*(?:后|以后)/);
    if (match) {
      const amount = Number(match[1]);
      const multiplier = match[2] === "小时" ? 60 * 60 * 1000 : 60 * 1000;
      return clampRecovery(nowMs + amount * multiplier, nowMs);
    }
    return null;
  }

  function parseClock(text, nowMs) {
    const hasResetContext = /reset|available again|try again|come back|after|until|恢复|重置|后重试|再试|明天/i.test(text);
    if (!hasResetContext) return null;

    const match = text.match(/(?:(tomorrow|明天)\s*)?(?:(上午|下午|晚上|am|pm)\s*)?(\d{1,2})[:：](\d{2})(?:\s*(am|pm))?/i);
    if (!match) return null;

    const tomorrow = Boolean(match[1]);
    const prefix = String(match[2] || "").toLowerCase();
    const suffix = String(match[5] || "").toLowerCase();
    const meridiem = suffix || prefix;
    let hour = Number(match[3]);
    const minute = Number(match[4]);
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || minute > 59 || hour > 23) return null;

    if (/pm|下午|晚上/.test(meridiem) && hour < 12) hour += 12;
    if (/am|上午/.test(meridiem) && hour === 12) hour = 0;
    if ((/am|pm|上午|下午|晚上/.test(meridiem)) && Number(match[3]) > 12) return null;

    const now = new Date(nowMs);
    const candidate = new Date(nowMs);
    candidate.setSeconds(0, 0);
    candidate.setHours(hour, minute, 0, 0);
    if (tomorrow) candidate.setDate(candidate.getDate() + 1);
    else if (candidate.getTime() <= now.getTime() + 1000) candidate.setDate(candidate.getDate() + 1);
    return clampRecovery(candidate.getTime(), nowMs);
  }

  function parseRecoveryAt(value, nowMs = Date.now()) {
    const text = normalize(value);
    if (!quotaText(text)) return null;
    return parseRelative(text, nowMs) || parseClock(text, nowMs) || null;
  }

  function candidateNodes() {
    const selectors = [
      "[role='alert']",
      "[role='dialog']",
      "[aria-modal='true']",
      "[data-sonner-toast]",
      "[data-toast]",
      "[data-testid*='toast']",
      "[data-testid*='limit']",
      "[data-testid*='modal']",
    ];
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        if (seen.has(node) || !visible(node)) continue;
        seen.add(node);
        result.push(node);
      }
    }

    const form = document.querySelector("form[data-type='unified-composer'], form");
    for (const node of [form?.parentElement, form?.parentElement?.parentElement]) {
      if (!node || seen.has(node) || !visible(node)) continue;
      const text = normalize(node.innerText || node.textContent || "");
      if (text.length <= 2400) {
        seen.add(node);
        result.push(node);
      }
    }
    return result;
  }

  async function report(text, recoveryAt) {
    const fingerprint = `${normalize(text).slice(0, 700)}|${Number(recoveryAt || 0)}`;
    if (fingerprint === state.lastFingerprint) return;
    state.lastFingerprint = fingerprint;
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.multimodal.quota.v36",
        data: {
          detected_at_ms: Date.now(),
          recovery_at_ms: recoveryAt,
          source_text: normalize(text).slice(0, 1200),
          attachment_names: [...state.attachmentNames],
          detector: "multimodal-quota-v36",
        },
      });
    } catch (_) {}
  }

  function scan() {
    if (Date.now() > state.activeUntil) return null;
    for (const node of candidateNodes()) {
      const text = normalize(node.innerText || node.textContent || "");
      if (!quotaText(text)) continue;
      const recoveryAt = parseRecoveryAt(text, Date.now());
      report(text, recoveryAt).catch(() => {});
      return { text, recovery_at_ms: recoveryAt };
    }
    return null;
  }

  function markActive(names = []) {
    state.activeUntil = Math.max(state.activeUntil, Date.now() + ACTIVE_MS);
    state.attachmentNames = Array.isArray(names) ? names.map(normalize).filter(Boolean).slice(0, 4) : [];
    queueMicrotask(scan);
    setTimeout(scan, 250);
    setTimeout(scan, 800);
    setTimeout(scan, 1800);
  }

  const listener = (message, _sender, sendResponse) => {
    if (message?.type === "chat2api.multimodal.quota.ping.v36") {
      sendResponse({ ok: true, controller: "multimodal-quota-v36" });
      return false;
    }
    if (message?.type === "chat2api.attach.prepare.v4") {
      markActive((message.attachments || []).map(item => item?.filename || item?.file_id || ""));
      return false;
    }
    if (message?.type === "chat2api.request") {
      const names = message.options?.chat2api_diagnostics?.attachment_names;
      if (Array.isArray(names) && names.length) markActive(names);
      return false;
    }
    return false;
  };

  chrome.runtime.onMessage.addListener(listener);

  if (document.documentElement) {
    state.observer = new MutationObserver(() => {
      if (Date.now() <= state.activeUntil) queueMicrotask(scan);
    });
    state.observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  }
  state.interval = setInterval(scan, 750);

  globalThis[KEY] = {
    state,
    quotaText,
    parseRecoveryAt,
    markActive,
    scan,
    listener,
  };
})();
