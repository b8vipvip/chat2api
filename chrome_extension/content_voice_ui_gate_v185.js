// Shared DOM-only evidence/one-time welcome action for both Voice controllers.
// No Worker routing, request lifecycle, or terminal decision authority here.
(() => {
  const KEY = "__CHAT2API_VOICE_UI_GATE_V185__";
  if (globalThis[KEY]) return;
  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect?.();
    const style = getComputedStyle(element);
    return Boolean(rect?.width > 0 && rect?.height > 0 &&
      style.display !== "none" && style.visibility !== "hidden");
  }
  const text = element => normalize(element?.innerText || element?.textContent || "");
  function label(element) {
    return normalize(element?.getAttribute?.("aria-label") || element?.getAttribute?.("title") || text(element));
  }
  function welcomeDialog() {
    // Never click an arbitrary Continue in the normal chat, account, or billing UI.
    const scopes = [...document.querySelectorAll("[role='dialog'], [aria-modal='true']")];
    for (const scope of scopes) {
      if (!visible(scope) || !/\bMeet Voice\b/i.test(text(scope).slice(0, 700))) continue;
      const button = [...scope.querySelectorAll("button")].find(item =>
        visible(item) && !item.disabled && /^(continue|继续|继续使用)$/i.test(label(item)));
      if (button) return {scope, button, evidence:"dialog"};
    }
    // Some ChatGPT builds omit role=dialog. Accept ONLY an exact onboarding
    // heading whose nearest small ancestor also contains a Continue button.
    const headings = [...document.querySelectorAll("h1,h2,h3,[role='heading']")];
    for (const heading of headings) {
      if (!visible(heading) || !/^Meet Voice$/i.test(text(heading))) continue;
      let parent = heading.parentElement;
      for (let depth=0; parent && depth<6; depth++,parent=parent.parentElement) {
        const button = [...parent.querySelectorAll("button")].find(item =>
          visible(item) && !item.disabled && /^(continue|继续|继续使用)$/i.test(label(item)));
        if (button && text(parent).length < 950) return {scope:parent, button, evidence:"heading"};
      }
    }
    return null;
  }
  function connectionFailure() {
    const candidates = [...document.querySelectorAll(
      "[role='alert'],[role='status'],[data-sonner-toast],[data-testid*='toast'],[data-state='open']"
    )];
    // A toast may lack role=alert. Only examine compact visible text nodes,
    // never document.body (which includes old conversation text).
    for (const node of document.querySelectorAll("p,span,div")) {
      if (candidates.length >= 150) break;
      const value = text(node);
      if (value.length < 175 && /voice couldn.t connect|语音.*(?:无法连接|连接失败)/i.test(value))
        candidates.push(node);
    }
    for (const element of candidates) {
      if (!visible(element)) continue;
      const value = text(element).slice(0, 350);
      if (/voice couldn.t connect|语音.*(?:无法连接|连接失败)/i.test(value))
        return {code:"VOICE_CHATGPT_CONNECT_FAILED",message:value,source:"chatgpt-voice-toast"};
    }
    return null;
  }
  function continueWelcome() {
    const welcome = welcomeDialog();
    if (!welcome) return null;
    welcome.button.click();
    return {clicked:true,evidence:welcome.evidence,label:label(welcome.button)};
  }
  globalThis[KEY]={welcomeDialog,continueWelcome,connectionFailure};
})();
