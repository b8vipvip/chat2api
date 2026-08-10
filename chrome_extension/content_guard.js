(() => {
  const KEY = "__CHAT2API_GUARD_V2__";
  if (globalThis[KEY]) return;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null };

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  }

  function recentRoots() {
    const turns = [...document.querySelectorAll("article[data-testid^='conversation-turn'], [data-message-author-role]")]
      .map(el => el.closest("article[data-testid^='conversation-turn']") || el)
      .filter((el, i, arr) => el && arr.indexOf(el) === i && visible(el));
    return turns.slice(-3);
  }

  function textOf(el) {
    return String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function assistantCount() {
    return [...document.querySelectorAll("[data-message-author-role='assistant']")].filter(visible).length;
  }

  function generating() {
    return [...document.querySelectorAll("button")].some(button => {
      if (!visible(button)) return false;
      const label = `${button.getAttribute("aria-label") || ""} ${textOf(button)}`;
      return /stop generating|stop streaming|停止生成|停止回答/i.test(label);
    });
  }

  function detectError() {
    const roots = recentRoots();
    const candidates = [];
    const selectors = ["[role='alert']", "[data-testid*='error']", "[class*='error']", "[class*='text-red']", "button"];
    for (const root of roots) {
      for (const selector of selectors) {
        for (const el of root.querySelectorAll(selector)) {
          if (!visible(el)) continue;
          const text = textOf(el);
          if (!text) continue;
          if (/(出了点问题|发生错误|网络错误|上传失败|生成失败|something went wrong|network error|failed to (?:upload|generate)|please retry|请重试)/i.test(text)) {
            candidates.push({ el, text });
          }
        }
      }
      const rootText = textOf(root);
      if (/(出了点问题|something went wrong|请重试|please retry)/i.test(rootText)) candidates.push({ el: root, text: rootText });
    }
    if (!candidates.length) return null;
    candidates.sort((a, b) => a.text.length - b.text.length);
    const match = candidates[0];
    const root = match.el.closest("article[data-testid^='conversation-turn']") || match.el.parentElement || document;
    const retry = [...root.querySelectorAll("button")].find(button => visible(button) && /(重试|retry)/i.test(textOf(button) + " " + (button.getAttribute("aria-label") || ""))) ||
      [...document.querySelectorAll("button")].reverse().find(button => visible(button) && /^(重试|retry)$/i.test(textOf(button)));
    return { text: match.text.slice(0, 500), retry };
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  async function watch(active) {
    const started = Date.now();
    const timeout = Math.max(15000, Number(active.timeoutSeconds || 300) * 1000);
    let completedQuietSince = 0;
    while (state.active === active && Date.now() - started < timeout) {
      const now = Date.now();
      const found = detectError();
      if (found) {
        completedQuietSince = 0;
        if (found.text !== active.lastErrorText) {
          active.lastErrorText = found.text;
          active.errorCount += 1;
          await emit({
            type: "chat.diagnostics",
            request_id: active.requestId,
            diagnostics: {
              ui_error_detected: true,
              ui_error_count: active.errorCount,
              ui_error_text: found.text,
              ui_retry_count: active.retryCount,
            },
          });
        }
        if (now < active.retryGraceUntil) {
          await delay(250);
          continue;
        }
        if (found.retry && active.retryCount < active.maxRetries && now - active.lastRetryAt > 1500) {
          active.retryCount += 1;
          active.lastRetryAt = now;
          active.retryGraceUntil = now + 7000;
          active.lastErrorText = "";
          found.retry.click();
          await emit({
            type: "chat.diagnostics",
            request_id: active.requestId,
            diagnostics: {
              ui_retry_count: active.retryCount,
              ui_retry_last_reason: found.text,
            },
          });
          await delay(900);
          continue;
        }
        await emit({
          type: "chat.error",
          request_id: active.requestId,
          error: `ChatGPT UI error: ${found.text}`,
        });
        if (state.active === active) state.active = null;
        return;
      }

      const responsePresent = assistantCount() > active.baselineAssistantCount;
      if (responsePresent && !generating()) {
        completedQuietSince ||= now;
        if (now - completedQuietSince > 3200) {
          if (state.active === active) state.active = null;
          return;
        }
      } else {
        completedQuietSince = 0;
      }
      await delay(250);
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "chat2api.request") {
      const active = {
        requestId: message.requestId,
        timeoutSeconds: message.options?.timeout_seconds || 300,
        maxRetries: Math.max(0, Math.min(2, Number(message.options?.ui_retry_count ?? 1))),
        retryCount: 0,
        errorCount: 0,
        baselineAssistantCount: assistantCount(),
        lastRetryAt: 0,
        retryGraceUntil: 0,
        lastErrorText: "",
      };
      state.active = active;
      watch(active).catch(() => {});
    } else if (message.type === "chat2api.cancel" && state.active?.requestId === message.requestId) {
      state.active = null;
    }
    return false;
  });

  globalThis[KEY] = state;
})();
