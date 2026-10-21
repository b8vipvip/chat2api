/* P0 read-only request-scoped Worker evidence supervisor.
 * No timer or message listener may complete, fail, dispatch or cancel a request.
 * Never exports prompt text, DOM HTML, image URLs, cookies or media bytes.
 */
(() => {
  "use strict";
  if (globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__) return;

  const tools = ["media_delivery_v1", "conversation_dom_v1"];
  const limits = {maxSnapshots: 8, thresholdMs: 20000};
  const delayByStage = {
    request_pre_submit: 0,
    request_submitted: 0,
    media_handoff: 0,
    unresolved_after_threshold: 0,
    before_terminal: 0,
  };
  const allowedRoots = ["main", "[role='main']", "#thread", "[data-testid='conversation-turns']"];

  function observerFacts() {
    const at = performance.now();
    const root = allowedRoots.map(selector => document.querySelector(selector)).find(Boolean);
    const scope = root || document;
    const allImages = [...scope.querySelectorAll("img")].slice(0, 120);
    const conversationNodes = [...scope.querySelectorAll(
      "[data-testid^='conversation-turn'],[data-message-author-role]"
    )].slice(-24);
    let visibleLarge = 0, blobs = 0, data = 0, https = 0, untrusted = 0;
    let loaded = 0, unloaded = 0, dimensions = 0, noDimensions = 0;
    let maxWidth = 0, maxHeight = 0;
    for (const node of allImages) {
      const source = String(node.currentSrc || node.getAttribute("src") || "");
      const naturalWidth = Math.min(12000, Math.max(0, Number(node.naturalWidth) || 0));
      const naturalHeight = Math.min(12000, Math.max(0, Number(node.naturalHeight) || 0));
      maxWidth = Math.max(maxWidth, naturalWidth);
      maxHeight = Math.max(maxHeight, naturalHeight);
      const box = node.getBoundingClientRect();
      const visible = box.width >= 96 && box.height >= 96
        && getComputedStyle(node).display !== "none"
        && getComputedStyle(node).visibility !== "hidden";
      if (visible) visibleLarge += 1;
      if (source.startsWith("blob:")) blobs += 1;
      else if (source.startsWith("data:image/")) data += 1;
      else if (source.startsWith("https:")) https += 1;
      else if (source) untrusted += 1;
      if (node.complete && naturalWidth > 0) loaded += 1;
      else unloaded += 1;
      if (naturalWidth && naturalHeight) dimensions += 1;
      else noDimensions += 1;
    }
    const buttons = [...scope.querySelectorAll("button")].slice(0, 100);
    const attr = node => (
      String(node.getAttribute("aria-label") || node.getAttribute("title") || "").toLowerCase()
    );
    const roleStats = conversationNodes.reduce((summary, node) => {
      const role = String(node.getAttribute("data-message-author-role") || "").toLowerCase();
      if (role === "assistant") summary.assistant += 1;
      if (role === "user") summary.user += 1;
      return summary;
    }, {assistant: 0, user: 0});
    const backgrounds = [...scope.querySelectorAll(
      "[style*='background-image'],[style*='background:']"
    )].slice(0, 120).length;
    return {
      main_found: Boolean(root),
      main_selector: root ? (root.id === "thread" ? "#thread" : root.tagName.toLowerCase()) : null,
      assistant_turn_nodes: roleStats.assistant,
      user_turn_nodes: roleStats.user,
      conversation_turn_nodes: conversationNodes.length,
      candidate_image_count: allImages.length,
      visible_large_images: visibleLarge,
      picture_nodes: scope.querySelectorAll("picture").length,
      canvas_nodes: scope.querySelectorAll("canvas").length,
      css_background_nodes: backgrounds,
      blob_image_nodes: blobs,
      data_image_nodes: data,
      https_image_nodes: https,
      untrusted_image_nodes: untrusted,
      image_complete_nodes: loaded,
      image_unloaded_nodes: unloaded,
      image_with_dimensions: dimensions,
      image_without_dimensions: noDimensions,
      image_natural_width_max: maxWidth,
      image_natural_height_max: maxHeight,
      edit_buttons: buttons.filter(x => /\b(edit|编辑)\b/i.test(attr(x))).length,
      download_buttons: buttons.filter(x => /(download|下载)/i.test(attr(x))).length,
      share_buttons: buttons.filter(x => /(share|分享)/i.test(attr(x))).length,
      composer_found: Boolean(document.querySelector("[contenteditable='true'],textarea")),
      page_ready: document.readyState === "complete",
      selector_main_count: document.querySelectorAll("main,[role='main']").length,
      selector_assistant_count: document.querySelectorAll("[data-message-author-role='assistant']").length,
      selector_image_count: document.querySelectorAll("main img,[role='main'] img,#thread img").length,
      selector_chat_thread_count: document.querySelectorAll("#thread,[data-testid='conversation-turns']").length,
      capture_cost_ms: Math.round((performance.now() - at) * 10) / 10,
    };
  }

  function begin(active) {
    const policy = active?.options?.probe_policy;
    if (!policy || policy.schema_version !== 1 || !policy.tools) return null;
    const enabled = tools.filter(tool => policy.tools[tool]?.enabled === true);
    if (!enabled.length) return null;
    const session = {
      request_id: String(active.requestId || ""),
      started_at: Date.now(), sequence: 0, count: 0,
      enabled: new Set(enabled),
      threshold: Math.max(5000, Math.min(120000,
        Number(policy.tools.media_delivery_v1?.unresolved_after_ms) || limits.thresholdMs)),
      cap: Math.max(1, Math.min(8, Number(policy.tools.media_delivery_v1?.max_snapshots) || limits.maxSnapshots)),
      handoffReported: false, thresholdReported: false, lastSentAt: 0,
    };
    active.probeSession = session;
    capture(active, "request_pre_submit", {});
    return session;
  }

  function capture(active, stage, extra = {}) {
    const session = active?.probeSession;
    if (!session || !delayByStage.hasOwnProperty(stage) || session.count >= session.cap) return;
    if (stage === "media_handoff" && session.handoffReported) return;
    if (stage === "unresolved_after_threshold" && session.thresholdReported) return;
    if (stage === "media_handoff") session.handoffReported = true;
    if (stage === "unresolved_after_threshold") session.thresholdReported = true;
    session.count += 1;
    let facts;
    try { facts = observerFacts(); }
    catch (_) {
      facts = {main_found: false, candidate_image_count: null};
    }
    const elapsed = Math.max(0, Date.now() - session.started_at);
    facts.request_elapsed_ms = elapsed;
    if (typeof extra.generating === "boolean") facts.generating = extra.generating;
    if (typeof extra.handoff === "boolean") facts.media_handoff_observed = extra.handoff;
    if (typeof extra.newCandidates === "number") facts.new_image_candidate_count = extra.newCandidates;
    if (typeof extra.captured === "number") facts.image_transfer_successes = extra.captured;
    let classification = "baseline";
    if (stage === "before_terminal") classification = extra.completed
      ? "completed_observed" : "request_error_observed";
    else if (extra.handoff && !facts.candidate_image_count)
      classification = "handoff_without_visible_media";
    else if (!facts.assistant_turn_nodes)
      classification = "no_assistant_turn";
    else if (facts.visible_large_images)
      classification = "media_candidate_detected";
    else if (stage === "unresolved_after_threshold")
      classification = "waiting_for_result";
    for (const tool_id of session.enabled) {
      if (session.sequence >= 32) break;
      session.sequence += 1;
      // Never block the active browser request when the socket is slow.
      Promise.resolve().then(() => chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "probe.event", request_id: session.request_id,
          tool_id, stage, sequence: session.sequence,
          classification, facts,
        },
      })).catch(() => {});
    }
  }

  function observe(active, {handoff = false, generating = false,
                             newCandidates = 0, captured = 0} = {}) {
    const session = active?.probeSession;
    if (!session) return;
    if (handoff && !session.handoffReported) capture(active, "media_handoff",
      {handoff, generating, newCandidates, captured});
    if (!session.thresholdReported && Date.now() - session.started_at >= session.threshold)
      capture(active, "unresolved_after_threshold",
        {handoff, generating, newCandidates, captured});
  }

  globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__ = Object.freeze({
    begin, capture, observe, snapshot: observerFacts,
  });
})();
