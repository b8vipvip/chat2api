(() => {
  const KEY = "__CHAT2API_MODEL_LIBRARY_V145__";
  if (globalThis[KEY]) return;

  const REVISION = 145;
  const OPEN_TIMEOUT_MS = 7000;
  const VERIFY_TIMEOUT_MS = 5000;
  const state = { running: null };
  globalThis[KEY] = state;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(element) {
    if (!element || !(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return String(
      element?.getAttribute?.("aria-label") ||
      element?.getAttribute?.("data-value") ||
      element?.getAttribute?.("title") ||
      element?.innerText ||
      element?.textContent ||
      "",
    ).replace(/[✓✔︎✔√]/g, "").replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").trim();
  }

  function normalize(value) {
    return String(value || "")
      .replace(/[✓✔︎✔√]/g, "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/[–—]/g, "-")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function canonicalModelId(value) {
    let text = normalize(value).replace(/_/g, "-");
    if (!text || /gpt[-\s]?(?:image|live)\b/.test(text)) return "";

    let match = text.match(/\bgpt[-\s]?(\d+(?:\.\d+)?)(?:[-\s]+(astra|pro|sol|terra|luna|mini))?\b/i);
    if (match) return `gpt-${match[1]}${match[2] ? `-${match[2].toLowerCase()}` : ""}`;

    match = text.match(/\b(\d+\.\d+)(?:[-\s]+(astra|pro|sol|terra|luna|mini))?\b/i);
    if (match) return `gpt-${match[1]}${match[2] ? `-${match[2].toLowerCase()}` : ""}`;

    match = text.match(/\b(o\d+(?:[-.][a-z0-9]+)*)\b/i);
    return match ? match[1].toLowerCase() : "";
  }

  function modelIdOf(element) {
    if (!element) return "";
    for (const name of ["data-model-id", "data-model", "data-value", "value", "aria-label", "title"]) {
      const model = canonicalModelId(element.getAttribute?.(name) || "");
      if (model) return model;
    }
    return canonicalModelId(labelOf(element)) ||
      canonicalModelId(element.innerText || element.textContent || "");
  }

  function rejectedControl(element) {
    const text = normalize(`${labelOf(element)} ${element?.getAttribute?.("data-testid") || ""}`);
    return /send|submit|voice|microphone|attach|upload|file|tool|发送|语音|附件|上传/.test(text);
  }

  function modelPicker() {
    const root = [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || document;
    const candidates = [];
    const seen = new Set();
    for (const selector of [
      "button[data-testid*='model' i]",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[class*='composer-pill']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
      // Current ChatGPT header model picker is often outside the composer form.
      "header button", "[data-testid*='model-switcher' i]",
      "[data-testid*='model-picker' i]",
    ]) {
      const roots = root === document ? [document] : [root, document];
      for (const scope of roots) for (const element of scope.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled || rejectedControl(element)) continue;
        seen.add(element);
        const model = modelIdOf(element);
        const cls = String(element.className || "");
        const testId = String(element.getAttribute("data-testid") || "");
        const aria = String(element.getAttribute("aria-label") || "");
        let score = 0;
        if (model) score += 250;
        if (/model/i.test(testId) || /model|模型/i.test(aria)) score += 180;
        if (/composer-pill/i.test(cls)) score += 120;
        if (element.getAttribute("aria-haspopup")) score += 40;
        if (/chatgpt|gpt|模型|model|instant|thinking/i.test(labelOf(element)) &&
            element.closest("header,nav,[data-testid*=header i]")) score += 110;
        if (score) candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.element || null;
  }

  // Reuse this picker identity in the reasoning controller.
  state.pickerControl = modelPicker;
  // A ChatGPT header may only say "ChatGPT" even while the model menu
  // has a concrete checked row. One DOM parser owns both manual validation
  // and post-navigation confirmation; neither may infer from old menu state.
  // This inspect action only opens/closes the picker, never selects a model.
  state.inspectSelectedModel = () => inspectModelSelectionV149();

  function menuRoots() {
    const rows = [];
    const seen = new Set();
    for (const selector of [
      "[role='menu']", "[role='listbox']", "[data-radix-popper-content-wrapper]",
      "[data-radix-menu-content]", "[data-state='open']", "[class*='popover' i]",
    ]) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 70 || rect.height < 25) continue;
        seen.add(element);
        rows.push(element);
      }
    }
    // ChatGPT sometimes portals model rows without a role=menu/listbox or a
    // radix wrapper. Only semantic *interactive* rows are eligible here, and
    // modelChoices still rejects wrappers containing multiple model IDs.
    if (!rows.length) {
      const detached = [...document.querySelectorAll(
        "[role='menuitem'],[role='menuitemradio'],[role='option']"
      )].filter(item => visible(item) && distinctModelIds(labelOf(item)).length === 1);
      if (detached.length) rows.push({
        querySelectorAll: () => detached,
      });
    }
    return rows;
  }

  function interactiveAncestor(element, boundary) {
    let current = element;
    for (let i = 0; current && i < 7; i += 1, current = current.parentElement) {
      if (current.matches?.("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item]")) {
        return current;
      }
      if (current === boundary) break;
    }
    return null;
  }

  // A model menu may contain an entire nested menu AND a power slider.
  // Treating its wrapper as "GPT-6" because the wrapper contains text for
  // several models caused v0.22.133 to advertise unclickable phantom choices.
  function distinctModelIds(label) {
    const text = normalize(label);
    const matches = text.match(/\bgpt[-\s]?\d+(?:\.\d+)?(?:[-\s]+(?:astra|pro|sol|terra|luna|mini))?\b/g) || [];
    return [...new Set(matches.map(value => canonicalModelId(value)).filter(Boolean))];
  }

  function isDisabledModel(element) {
    return Boolean(element?.disabled ||
      element?.getAttribute?.("aria-disabled") === "true" ||
      element?.hasAttribute?.("disabled") ||
      element?.hasAttribute?.("data-disabled") ||
      element?.closest?.("[aria-disabled='true'],[data-disabled]"));
  }

  function modelChoices() {
    const byId = new Map();
    for (const root of menuRoots()) {
      for (const raw of root.querySelectorAll(
        "button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[data-model-id],[data-model],div,span"
      )) {
        if (!visible(raw)) continue;
        const candidate = interactiveAncestor(raw, root);
        if (!candidate || !visible(candidate) || candidate === root) continue;
        const visibleLabel = String(candidate.innerText || candidate.textContent || "").replace(/\s+/g," ").trim();
        const label = distinctModelIds(visibleLabel).length ? visibleLabel : labelOf(candidate);
        // Never promote a menu/popup wrapper containing all picker models.
        // At least one precise ID or one isolated visible model label is needed.
        const ids = distinctModelIds(label);
        const explicit = canonicalModelId(candidate.getAttribute?.("data-model-id") ||
          candidate.getAttribute?.("data-model") || "");
        if (ids.length > 1 || (!explicit && ids.length !== 1)) continue;
        const model = explicit || ids[0];
        if (!model) continue;
        const enabled = !isDisabledModel(candidate);
        const selected = candidate.getAttribute("aria-checked") === "true" ||
          candidate.getAttribute("aria-selected") === "true" ||
          /selected|checked/i.test(candidate.getAttribute("data-state") || "");
        const found = {element: candidate,id:model,
          label:label.slice(0,130) || model,selected,enabled,
          sunset_notice:/\bleaving on\b|\bretiring\b|即将下线|即将移除/i.test(label)};
        const prior = byId.get(model);
        if (!prior || (Number(found.enabled)*8+Number(found.selected)*2+Number(label.length < prior.label.length)) >
          (Number(prior.enabled)*8+Number(prior.selected)*2)) byId.set(model,found);
      }
    }
    return [...byId.values()];
  }

  function expansionChoice() {
    const pattern = /^(advanced|models?|more|legacy|高级|模型|更多|其他模型)(?:\s|$)/i;
    for (const root of menuRoots()) {
      for (const raw of root.querySelectorAll("button,[role='menuitem'],[role='option'],[data-radix-collection-item],div,span")) {
        if (!visible(raw)) continue;
        const label = labelOf(raw);
        if (!label || !pattern.test(label)) continue;
        const element = interactiveAncestor(raw, root);
        if (visible(element) && !modelIdOf(element)) return element;
      }
    }
    return null;
  }

  async function waitFor(predicate, timeout = OPEN_TIMEOUT_MS, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        const value = await predicate();
        if (value) return value;
      } catch (_) {}
      await delay(interval);
    }
    return null;
  }

  function closeMenus() {
    for (const type of ["keydown", "keyup"]) {
      document.dispatchEvent(new KeyboardEvent(type, { key: "Escape", code: "Escape", bubbles: true }));
    }
  }

  async function openModelMenu() {
    closeMenus();
    const picker = await waitFor(() => modelPicker(), OPEN_TIMEOUT_MS, 120);
    if (!picker) throw new Error("ChatGPT model picker was not found");
    // A header may render before the conversation composer has hydrated.
    // A picker click on that transitional page can be ignored entirely.
    await waitFor(() => document.querySelector(
      "#prompt-textarea,textarea,[contenteditable='true']"
    ), OPEN_TIMEOUT_MS, 120);
    let choices = null;
    // One bounded re-open after a missing/unfinished popup, not a tab-close
    // loop and never a second model-selection authority.
    for (let attempt = 0; attempt < 2 && !choices; attempt += 1) {
      if (attempt) {
        closeMenus();
        await delay(250);
      }
      const currentPicker = modelPicker();
      if (!currentPicker) break;
      currentPicker.click();
      choices = await waitFor(() => {
        const rows = modelChoices();
        return rows.length ? rows : null;
      }, OPEN_TIMEOUT_MS, 100);
    }
    if (!choices) throw new Error("ChatGPT model menu did not expose concrete model choices");
    const expansion = expansionChoice();
    if (expansion) {
      expansion.click();
      const nested = await waitFor(() => {
        const rows = modelChoices();
        return rows.length > choices.length ? rows : null;
      }, 900, 100) || modelChoices();
      const byId = new Map([...choices, ...nested].map(item => [item.id, item]));
      choices = [...byId.values()];
    }
    if (!choices.length) throw new Error("ChatGPT model menu did not expose concrete model choices");
    return choices;
  }

  async function currentModel() {
    const direct = modelIdOf(modelPicker());
    if (direct) return direct;
    try {
      const choices = await openModelMenu();
      const selected = choices.find(item => item.selected)?.id || "";
      closeMenus();
      return selected;
    } catch (_) {
      closeMenus();
      return "";
    }
  }

  async function selectAndVerify(modelId) {
    let choices;
    try {
      choices = await openModelMenu();
    } catch (error) {
      const failure = new Error("Model picker unavailable: "+String(error?.message||error));
      failure.code = "model_picker_unavailable";
      failure.stage = "open-picker";
      throw failure;
    }
    const compact = choices.map(item => ({
      id:item.id,enabled:item.enabled,selected:item.selected,sunset_notice:item.sunset_notice,
      label:item.label.slice(0,100),
    }));
    const choice = choices.find(item=>item.id===modelId);
    if (!choice) {
      closeMenus();
      const failure = new Error("Requested model option is not present in live ChatGPT picker: "+
        modelId+"; observed="+compact.map(x=>x.id).join(","));
      failure.code = "model_option_missing";
      failure.stage = "read-model-picker";
      failure.evidence = compact;
      throw failure;
    }
    if (!choice.enabled) {
      closeMenus();
      const failure = new Error("Requested model option is disabled in ChatGPT picker: "+modelId+
        (choice.sunset_notice?" (retirement notice shown)":""));
      failure.code = "model_option_disabled";
      failure.stage = "read-model-picker";
      failure.evidence = compact;
      throw failure;
    }
    choice.element.scrollIntoView?.({block:"nearest",inline:"nearest"});
    choice.element.click();
    await delay(350);
    // React may rebuild the control on selection. Reopen it at most once:
    // reopening/closing the menu during each polling iteration can undo a
    // pending selection and previously hid the actual failing phase.
    const direct = await waitFor(()=>modelIdOf(modelPicker())===modelId,1600,120);
    if(direct) {
      closeMenus();
      return {model:modelId,verification:"selected-picker-label",evidence:compact};
    }
    let reopened = [];
    try {
      reopened = await openModelMenu();
    } catch (_) { /* report original observed picker evidence below */ }
    const confirmed = reopened.some(item=>item.id===modelId&&item.selected);
    closeMenus();
    if(confirmed) return {model:modelId,verification:"selected-menu-state",evidence:compact};
    const failure = new Error("ChatGPT picker did not confirm model after click: "+modelId+
      "; previously observed="+compact.map(x=>x.id+(x.enabled?"":"[disabled]")).join(",")+
      "; after click="+reopened.map(x=>x.id+(x.selected?"[selected]":"")).join(",")+
      (choice.sunset_notice?"; retirement notice shown":""));
    failure.code = "model_selection_unconfirmed";
    failure.stage = "confirm-model-selection";
    failure.evidence = compact;
    failure.after = reopened.map(item=>({
      id:item.id,selected:item.selected,enabled:item.enabled,
    }));
    throw failure;
  }

  function requestActive() {
    return Boolean(
      globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active ||
      globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active ||
      globalThis.__CHAT2API_CONTENT__?.active ||
      document.querySelector("button[data-testid='stop-button'],button[aria-label*='Stop generating'],button[aria-label*='停止生成']")
    );
  }

  async function validateModels() {
    if (state.running) return state.running;
    if (requestActive()) {
      const error = new Error("Worker model validation deferred while a ChatGPT request is active");
      error.code = "model_validation_busy";
      throw error;
    }
    state.running = (async () => {
      const startedAt = new Date().toISOString();
      // Model-switch actions can navigate/reload ChatGPT, which destroys the
      // content-script's asynchronous reply channel. Observe the entire picker
      // without switching pages; only the *currently selected* concrete item
      // qualifies as a verified picker selection. Other choices remain observed,
      // not routable, until a later explicit selection is confirmed.
      const initialChoices = await openModelMenu();
      const labels = new Map();
      const enabledMap = new Map();
      const selectedInMenu = initialChoices.find(item => item.selected)?.id || "";
      for (const choice of initialChoices) {
        if (choice.id && !labels.has(choice.id)) { labels.set(choice.id, choice.label || choice.id); enabledMap.set(choice.id, choice.enabled); }
      }
      const pickerSelected = modelIdOf(modelPicker());
      const selected = [selectedInMenu, pickerSelected].find(id => labels.has(id)) || "";
      closeMenus();
      const observed = [...labels].map(([id, label]) => ({
        id, label, source: "chatgpt-model-picker", validated: false, enabled: enabledMap.get(id) === true,
        observed_at: new Date().toISOString(),
      }));
      const validated = selected && enabledMap.get(selected) ? [{
        id: selected,
        label: labels.get(selected) || selected,
        family: selected,
        reasoning: null,
        selected: true,
        capabilities: ["text", "vision", "file-understanding"],
        reasoning_efforts: ["low", "medium", "high"],
        validated: true,
        validation_revision: REVISION,
        validated_at: new Date().toISOString(),
        validation_method: "chatgpt-picker-current-selected-state",
      }] : [];
      return {
        models: validated,
        observed_models: observed,
        current_model: selected || null,
        validation_state: validated.length ? "validated" : "observed-unverified",
        validation_revision: REVISION,
        validation_started_at: startedAt,
        validated_at: new Date().toISOString(),
        candidate_count: observed.length,
        validated_count: validated.length,
      };
    })().finally(() => { state.running = null; });
    return state.running;
  }

  async function selectModel(modelId) {
    if (requestActive()) throw new Error("Cannot switch model while a ChatGPT request is active");
    const model = canonicalModelId(modelId);
    if (!model) throw new Error(`Invalid model id: ${modelId}`);
    const proof = await selectAndVerify(model);
    // Do not conflate an available menu option with a confirmed selection.
    return { model, current_model: model, validated: true, validation_revision: REVISION,
      validation_method: proof.verification, observed_choices: proof.evidence };
  }


  // v149: split model switching into prepare -> acknowledged commit -> fresh
  // document inspection. A model click can navigate ChatGPT and destroy this
  // content script's pending asynchronous sendResponse promise.
  function pickerFailure(message, code, stage, evidence = []) {
    const error = new Error(message);
    error.code = code;
    error.stage = stage;
    error.evidence = evidence;
    return error;
  }

  function pickerEvidence(choices) {
    return choices.map(item => ({
      id: item.id, label: String(item.label || item.id).slice(0, 110),
      enabled: item.enabled === true, selected: item.selected === true,
      sunset_notice: Boolean(item.sunset_notice),
    }));
  }

  async function prepareModelSelectionV149(modelId) {
    if (requestActive()) throw pickerFailure(
      "Cannot change ChatGPT model while a request is active",
      "model_selection_busy", "prepare-model-selection",
    );
    const model = canonicalModelId(modelId);
    if (!model) throw pickerFailure("Invalid requested model: " + String(modelId),
      "invalid_model", "prepare-model-selection");
    const choices = await openModelMenu();
    const evidence = pickerEvidence(choices);
    const target = choices.find(item => item.id === model);
    if (!target) {
      closeMenus();
      throw pickerFailure("Requested model option not present: " + model,
        "model_option_missing", "prepare-model-selection", evidence);
    }
    if (!target.enabled) {
      closeMenus();
      throw pickerFailure("Requested model option disabled: " + model,
        "model_option_disabled", "prepare-model-selection", evidence);
    }
    const before = choices.find(item => item.selected)?.id || "";
    const alreadySelected = before === model;
    const ticket = "v149-" + (++state.selectionSequence);
    state.pendingSelection = alreadySelected ? null : {
      ticket, model, element: target.element, preparedAt: Date.now(),
    };
    if (alreadySelected) closeMenus();
    return {
      ticket, model, before, already_selected: alreadySelected,
      evidence, prepared_at: new Date().toISOString(),
    };
  }

  // IMPORTANT: send the acknowledgement BEFORE clicking. For a navigation,
  // Chrome destroys this frame immediately after click and drops async replies.
  function commitModelSelectionV149(ticket, sendResponse) {
    const pending = state.pendingSelection;
    if (!pending || pending.ticket !== ticket || Date.now() - pending.preparedAt > 15000) {
      sendResponse({ ok: false, code: "model_selection_ticket_expired",
        stage: "commit-model-selection", error: "Prepared model selection has expired" });
      return;
    }
    state.pendingSelection = null;
    sendResponse({ ok: true, model: pending.model, click_acknowledged: true });
    queueMicrotask(() => {
      try {
        pending.element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
        pending.element.click();
      } catch (error) {
        console.warn("Model click failed after commit acknowledgement", String(error?.message || error));
      }
    });
  }

  async function inspectModelSelectionV149() {
    let choices;
    try {
      choices = await openModelMenu();
    } catch (error) {
      throw pickerFailure("Unable to inspect ChatGPT model picker: " + String(error?.message || error),
        "model_inspection_unavailable", "inspect-model-selection");
    }
    const selectedInMenu = choices.find(item => item.selected)?.id || "";
    const pickerModel = modelIdOf(modelPicker());
    const models = pickerEvidence(choices);
    closeMenus();
    // Selected row is authoritative. Header fallback only applies to an exact,
    // visible model label when the menu exposes no selected row at all.
    const selected = selectedInMenu || (models.some(row => row.id === pickerModel) ? pickerModel : "");
    return {
      selected_model: selected || null,
      selected_in_menu: selectedInMenu || null,
      picker_model: pickerModel || null,
      evidence_source: selectedInMenu ? "live-menu-selected-row" :
        selected ? "live-picker-header-and-menu" : "no-checked-model-in-live-menu",
      models, inspected_at: new Date().toISOString(),
    };
  }

  state.selectionSequence = 0;
  state.pendingSelection = null;

  state.validate = validateModels;
  state.select = selectModel;

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "chat2api.model.prepare.v149") {
      prepareModelSelectionV149(message.model)
        .then(data => sendResponse({ok:true,data}))
        .catch(error => sendResponse({ok:false, code:String(error?.code || "model_prepare_failed"),
          stage:String(error?.stage || "prepare-model-selection"),
          evidence:error?.evidence || [], error:String(error?.message || error)}));
      return true;
    }
    if (message?.type === "chat2api.model.commit.v149") {
      commitModelSelectionV149(message.ticket, sendResponse);
      return false;
    }
    if (message?.type === "chat2api.model.inspect.v149") {
      inspectModelSelectionV149()
        .then(data => sendResponse({ok:true,data}))
        .catch(error => sendResponse({ok:false,code:String(error?.code || "model_inspect_failed"),
          stage:String(error?.stage || "inspect-model-selection"),
          error:String(error?.message || error)}));
      return true;
    }
    if (message?.type === "chat2api.models.validate.v145") {
      validateModels()
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, code: String(error?.code || ""), stage: String(error?.stage || ""), evidence: error?.evidence || [], after: error?.after || [], error: String(error?.message || error) }));
      return true;
    }
    if (message?.type === "chat2api.model.select.v145") {
      selectModel(message.model)
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, code: String(error?.code || ""), stage: String(error?.stage || ""), evidence: error?.evidence || [], after: error?.after || [], error: String(error?.message || error) }));
      return true;
    }
    return false;
  });
})();
