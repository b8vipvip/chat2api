/* P0 read-only request-scoped Worker evidence supervisor.
 * No timer or message listener may complete, fail, dispatch or cancel a request.
 * Never exports prompt text, DOM HTML, image URLs, cookies or media bytes.
 */
(() => {
  "use strict";
  if (globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__) return;

  const tools = ["media_delivery_v1", "conversation_dom_v1",
    "network_evidence_v1", "model_tool_state_v1", "selector_compat_v1",
    "abcd_richtext_v1", "abcd_images_v1", "abcd_files_v1",
    "abcd_context_v1", "response_boundary_v1"];
  const limits = {maxSnapshots: 8, thresholdMs: 20000};
  const delayByStage = {
    request_pre_submit: 0,
    request_submitted: 0,
    media_handoff: 0,
    unresolved_after_threshold: 0,
    before_terminal: 0,
    progress_checkpoint: 0,
    deadline_checkpoint: 0,
  };
  const allowedRoots = ["main", "[role='main']", "#thread", "[data-testid='conversation-turns']"];

  function safeVisible(node) {
    try {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none"
        && style.visibility !== "hidden";
    } catch (_) { return false; }
  }

  // Read-only page topology: ordered tags, branch ordinal, size, finish-action placement.
  // No page text, HTML, URLs, prompts, credentials or browser state are exported.
  function responseTopology(active, scope, changed, independent, candidate, actions) {
    const finish = actions.filter(node => !(active?.baselineFinishActions instanceof Set)
      || !active.baselineFinishActions.has(node));
    const first = candidate || independent[0] || null;
    const branch = node => {
      if (!node) return -1;
      let current = node, hops = 0;
      while (current?.parentElement && current.parentElement !== scope && hops++ < 32)
        current = current.parentElement;
      if (!current?.parentElement) return -1;
      return Array.prototype.indexOf.call(current.parentElement.children, current);
    };
    const after = (a,b) => Boolean(a && b && a !== b &&
      (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
    const all = [...new Set([...independent, ...(candidate ? [candidate] : []), ...finish])]
      .filter(Boolean).sort((left,right) => after(left,right) ? -1 : after(right,left) ? 1 : 0);
    const tags = {P:'p',DIV:'d',H1:'h',H2:'h',H3:'h',H4:'h',H5:'h',H6:'h',
      SECTION:'s',ARTICLE:'a',PRE:'c',TABLE:'t',UL:'u',OL:'o',LI:'l',BUTTON:'b',SPAN:'n'};
    const topo = all.slice(0,110).map(node => {
      const tag = tags[node.tagName] || 'x';
      const kind = finish.includes(node) ? 'f' : node===candidate ? 'r' : 'i';
      const length = Math.min(99999,String(node.innerText||node.textContent||'').length);
      return kind + Math.min(999,Math.max(0,branch(node))) + tag + length;
    }).join(';');
    return {
      scene_reply_topology: topo,
      scene_current_branch: branch(first),
      scene_new_action_count: finish.length,
      scene_new_actions_after_candidate: finish.filter(node=>after(candidate,node)).length,
      scene_new_actions_after_all: finish.filter(button=>
        independent.every(node=>after(node,button))).length,
      scene_new_actions_same_branch: finish.filter(node=>branch(node)===branch(first)).length,
      scene_changed_block_branches: new Set(changed.map(branch)).size,
    };
  }

  // Data-minimized scene evidence: only counters and lengths, never prompt
  // contents, generated code, model text, file names, URLs or DOM markup.
  function sceneFacts(active, scope) {
    const allBlocks = [...scope.querySelectorAll(
      "[data-message-content],.markdown,[class*='markdown'],[class*='prose'],pre,table,article,section,p,h1,h2,h3,h4,h5,h6,li,blockquote,math,.katex-display"
    )];
    const sceneScanOverflow = allBlocks.length > 1600;
    // Preserve the first sections in probe evidence too, rather than hiding
    // them by taking only the last 280 blocks as older probes did.
    const blocks = allBlocks.slice(0, 1600).filter(safeVisible);
    const text = node => String(node.innerText || node.textContent || "").trim();
    const lengths = blocks.map(node => text(node).length);
    const baseline = active?.baselineRenderedText instanceof Map
      ? active.baselineRenderedText : null;
    const changed = baseline ? blocks.filter(node =>
      baseline.get(node) !== text(node).replace(/\s+/g, " ").slice(-5000)) : [];
    const candidates = changed.filter(node => !changed.some(other =>
      other !== node && other.contains(node)));
    const promptStart = String(active?.promptNormalized || "").slice(0, 100);
    const promptEnd = String(active?.promptNormalized || "").slice(-100);
    const independent = candidates.filter(node => {
      const value = text(node);
      const tag = String(node.tagName || "").toLowerCase();
      const shortSemantic = /^(?:p|h[1-6]|li|math|blockquote)$/.test(tag)
        || Boolean(node.classList?.contains("katex-display"));
      return value.length >= (shortSemantic ? 2 : 120)
        && !(promptStart && value.includes(promptStart))
        && !(promptEnd && value.includes(promptEnd));
    });
    const sceneShortSemanticBlocks = independent.filter(node =>
      text(node).length < 120).length;
    const current = active?.lastCandidateNode || null;
    const uncovered = current && independent.length
      ? independent.filter(node => node !== current && !current.contains(node)
          && !node.contains(current)) : [];
    const numbers = nodes => nodes.length;
    const namedButtons = [...scope.querySelectorAll("button")].filter(safeVisible);
    const completed = namedButtons.filter(node =>
      /copy|复制|regenerate|重新生成|good response|bad response/i.test(
        String(node.getAttribute("aria-label") || "")+" "+
        String(node.getAttribute("data-testid") || "")));
    const fileTags = {pdf:0,xlsx:0,pptx:0,zip:0,json:0};
    for (const link of [...scope.querySelectorAll("a[download],a[href]")].slice(-200)) {
      const name = String(link.getAttribute("download") || link.textContent || "")
        .trim().toLowerCase().slice(-200);
      for (const ext of Object.keys(fileTags)) {
        if (new RegExp("\\."+ext+"(?:$|[\\s）)])").test(name)) fileTags[ext]++;
      }
    }

    // Request-scoped DOM topology: bounded, text-free ancestry and ordinal
    // evidence. This records WHY a finish toolbar cannot cover a multi-block
    // response, without collecting prompt bodies, private page HTML or URLs.
    const pageBranch = node => {
      let child=node,parent=child?.parentElement,depth=0;
      while(parent && parent!==scope && depth<32) {
        child=parent;parent=parent.parentElement;depth++;
      }
      return parent===scope ? [...scope.children].indexOf(child) : -1;
    };
    const nodeOrder = node => allBlocks.indexOf(node);
    const candidateBranch = current && current.isConnected ? pageBranch(current) : -1;
    const freshActions = completed.filter(node =>
      active?.baselineFinishActions instanceof Set
        && !active.baselineFinishActions.has(node));
    const actionEvidence = freshActions.slice(-10).map(node => ({
      branch:pageBranch(node), afterCandidate: Boolean(current && current.isConnected
        && (current.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)),
      afterAll: independent.every(part =>
        part !== node && Boolean(part.compareDocumentPosition(node)
          & Node.DOCUMENT_POSITION_FOLLOWING)),
    }));
    const topology = [...new Set([
      ...independent.slice(0,36), ...independent.slice(-140),
      ...uncovered.slice(-80), ...(current?.isConnected ? [current] : []),
    ])].filter(Boolean).map(node => {
      const mark=node===current?"C":uncovered.includes(node)?"U":"I";
      return [Math.max(0,nodeOrder(node)),String(node.tagName||"node").toLowerCase().slice(0,8),
        pageBranch(node),Math.min(99999,text(node).length),mark].join(":");
    }).join(";");
        const stream = String(active?.networkObservedText || "");
    const trailing = String(active?.networkTerminalText || "");
    return {
      ...responseTopology(active, scope, changed, independent, current, completed),
      scene_rich_block_count: blocks.length,
      scene_structural_scan_nodes: allBlocks.length,
      scene_structural_scan_overflow: sceneScanOverflow,
      scene_short_semantic_blocks: sceneShortSemanticBlocks,
      scene_changed_block_count: changed.length,
      scene_independent_block_count: independent.length,
      scene_uncovered_block_count: uncovered.length,
      scene_uncovered_block_chars: uncovered.reduce((sum,node)=>sum+text(node).length,0),
      scene_rich_max_chars: Math.max(0,...lengths),
      scene_rich_total_chars: lengths.reduce((sum,n)=>sum+n,0),
      scene_candidate_chars: Number(active?.lastCandidateTextLength || 0),
      scene_capture_chars: String(active?.lastCapturedText || "").length,
      scene_network_candidate_chars: stream.length,
      scene_network_terminal_chars: trailing.length,
      scene_network_transport_error: Boolean(active?.networkTerminalTransportErrorAt),
      scene_network_clean_terminal: Boolean(active?.networkTerminalTransportDoneAt
        && active?.networkTerminalAt
        && active.networkTerminalTransportDoneAt >= active.networkTerminalAt),
      scene_markdown_table_count: numbers(scope.querySelectorAll("table")),
      scene_markdown_row_count: numbers(scope.querySelectorAll("table tr")),
      scene_code_block_count: numbers(scope.querySelectorAll("pre")),
      scene_inline_code_count: numbers(scope.querySelectorAll("code:not(pre code)")),
      scene_math_node_count: numbers(scope.querySelectorAll(
        "math,.katex,.katex-display,.MathJax,[data-testid*='math']")),
      scene_heading_count: numbers(scope.querySelectorAll("h1,h2,h3,h4,h5,h6")),
      scene_list_count: numbers(scope.querySelectorAll("ul,ol")),
      scene_final_action_count: completed.length,
      scene_new_final_action_count: active?.baselineFinishActions instanceof Set
        ? completed.filter(node=>!active.baselineFinishActions.has(node)).length : 0,
      scene_current_branch: candidateBranch,
      scene_new_action_count: freshActions.length,
      scene_new_actions_after_candidate: actionEvidence.filter(x=>x.afterCandidate).length,
      scene_new_actions_after_all: actionEvidence.filter(x=>x.afterAll).length,
      scene_new_actions_same_branch: actionEvidence.filter(x=>x.branch>=0&&x.branch===candidateBranch).length,
      scene_changed_block_branches: new Set(independent.map(pageBranch)).size,
      scene_reply_topology: topology.slice(0,5600),
      scene_finish_after_all_blocks: Boolean(current
        && active?.baselineFinishActions instanceof Set
        && completed.some(button => !active.baselineFinishActions.has(button)
          && independent.concat([current]).every(node => node !== button
            && Boolean(node.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING)))),
      scene_image_count: numbers(scope.querySelectorAll("img")),
      scene_image_large_count: [...scope.querySelectorAll("img")].filter(node=>
        safeVisible(node) && node.getBoundingClientRect().width>=96).length,
      scene_picture_count: numbers(scope.querySelectorAll("picture")),
      scene_canvas_count: numbers(scope.querySelectorAll("canvas")),
      scene_file_card_count: numbers(scope.querySelectorAll(
        "[data-testid*='file'],[data-testid*='attachment'],[data-testid*='download']")),
      scene_file_input_count: numbers(scope.querySelectorAll("input[type='file']")),
      scene_file_pdf_count: fileTags.pdf,
      scene_file_xlsx_count: fileTags.xlsx,
      scene_file_pptx_count: fileTags.pptx,
      scene_file_zip_count: fileTags.zip,
      scene_file_json_count: fileTags.json,
      scene_context_turn_count: numbers(scope.querySelectorAll(
        "[data-testid^='conversation-turn'],[data-message-id]")),
      scene_context_user_role_count: numbers(scope.querySelectorAll(
        "[data-message-author-role='user']")),
      scene_context_assistant_role_count: numbers(scope.querySelectorAll(
        "[data-message-author-role='assistant']")),
      scene_context_request_has_user_anchor: Boolean(active?.baselineUserTurns),
      scene_context_prior_assistants: Number(active?.baselineCount || 0),
      scene_context_submit_confirmed: Boolean(active?.submitStartedAt),
      scene_context_generation_active: Boolean(document.querySelector(
        "button[data-testid='stop-button'],button[aria-label='Stop generating']")),
    };
  }

  function observerFacts(active = null) {
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
    // Structural counts only. Never export download URLs, filenames, element
    // text or DOM HTML: these facts identify redesigned file cards safely.
    const downloadLinks = [...scope.querySelectorAll("a[href]")].slice(-200);
    const downloadLinkStats = {https: 0, blob: 0, sandbox: 0, relative: 0, other: 0, named: 0};
    for (const link of downloadLinks) {
      const href = String(link.getAttribute("href") || "");
      if (href.startsWith("https://")) downloadLinkStats.https += 1;
      else if (href.startsWith("blob:")) downloadLinkStats.blob += 1;
      else if (href.startsWith("sandbox:")) downloadLinkStats.sandbox += 1;
      else if (href.startsWith("/")) downloadLinkStats.relative += 1;
      else downloadLinkStats.other += 1;
      if (link.hasAttribute("download") || /download/i.test(
        String(link.getAttribute("data-testid") || ""))) downloadLinkStats.named += 1;
    }
    const richTextCandidateCount = scope.querySelectorAll(
      "[data-message-content],.markdown,[class*='markdown'],[class*='prose']"
    ).length;
    const buttons = [...scope.querySelectorAll("button")].slice(0, 100);
    const attr = node => (
      String(node.getAttribute("aria-label") || node.getAttribute("title") || "").toLowerCase()
    );
    const downloadControls = buttons.filter(node => /(download|下载)/i.test(attr(node)));
    const urlAttrNames = ["href", "data-href", "data-download-url", "data-file-url", "data-url"];
    const downloadControlWithUrl = downloadControls.filter(node =>
      urlAttrNames.some(name => Boolean(node.getAttribute(name))));
    // Observational only: a JS-managed download button is NOT a transferable
    // URL. Record the distinction without activating controls or logging file
    // names, URLs, text, HTML, cookies, or generated bytes.
    const fileCardNodes = [...scope.querySelectorAll(
      "[data-testid*='file'],[data-testid*='attachment'],[data-testid*='download']"
    )].slice(-160);
    const fileCardsWithLinks = fileCardNodes.filter(node =>
      node.matches?.("a[href]") || Boolean(node.querySelector?.("a[href]")));
    const fileCardsWithButtons = fileCardNodes.filter(node =>
      node.matches?.("button") || Boolean(node.querySelector?.("button")));
    // ChatGPT's new Office artifacts may use preview actions (not conventional
    // data-testid file cards). Count only suffix classes, never filenames or
    // URLs. These are observational and never imply transferable file bytes.
    const previewCandidates = [...scope.querySelectorAll(
      "a,button,[role='button'],[tabindex='0'],[data-testid*='artifact'],[data-testid*='file']"
    )].slice(-240).filter(safeVisible);
    const previewLabel = node => String([
      node.getAttribute?.("aria-label"), node.getAttribute?.("title"),
      node.getAttribute?.("data-testid"), node.textContent,
    ].filter(Boolean).join(" ")).trim().slice(0, 350);
    const isOfficePreview = node => {
      const label = previewLabel(node);
      return /\.(?:xlsx|pptx|json|zip)(?:\b|[\s）)]|$)/i.test(label)
        && /(?:open preview of|打开预览|预览文件|excel|powerpoint|code|文件|artifact)/i.test(label);
    };
    const previewControls = previewCandidates.filter(isOfficePreview);
    const previewExtCount = ext => previewControls.filter(node =>
      new RegExp("\\." + ext + "(?:\\b|[\\s）)]|$)", "i")
        .test(previewLabel(node))).length;
    const previewConversationLinks = previewControls.filter(node => {
      const href = String(node.getAttribute?.("href") || "");
      try {
        const uri = new URL(href, location.href);
        return /(?:^|\.)chatgpt\.com$/i.test(uri.hostname)
          && /^\/c\/[a-f0-9-]{20,}$/i.test(uri.pathname);
      } catch (_) { return false; }
    });

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
      ...sceneFacts(active, scope),
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
      download_buttons: downloadControls.length,
      download_button_url_attr_count: downloadControlWithUrl.length,
      download_button_click_only_count: downloadControls.length - downloadControlWithUrl.length,
      file_card_candidates_count: fileCardNodes.length,
      file_card_href_count: fileCardsWithLinks.length,
      file_card_button_count: fileCardsWithButtons.length,
      file_preview_control_count: previewControls.length,
      file_preview_xlsx_count: previewExtCount("xlsx"),
      file_preview_pptx_count: previewExtCount("pptx"),
      file_preview_json_count: previewExtCount("json"),
      file_preview_zip_count: previewExtCount("zip"),
      file_preview_conversation_link_count: previewConversationLinks.length,
      file_preview_click_only_count: previewControls.filter(node =>
        !node.getAttribute?.("href")
        && !node.getAttribute?.("data-download-url")
        && !node.getAttribute?.("data-file-url")).length,
      download_link_https_count: downloadLinkStats.https,
      download_link_blob_count: downloadLinkStats.blob,
      download_link_sandbox_count: downloadLinkStats.sandbox,
      download_link_relative_count: downloadLinkStats.relative,
      download_link_other_count: downloadLinkStats.other,
      download_link_explicit_count: downloadLinkStats.named,
      rich_text_candidate_count: richTextCandidateCount,
      share_buttons: buttons.filter(x => /(share|分享)/i.test(attr(x))).length,
      composer_found: Boolean(document.querySelector("[contenteditable='true'],textarea")),
      page_ready: document.readyState === "complete",
      selector_main_count: document.querySelectorAll("main,[role='main']").length,
      selector_assistant_count: document.querySelectorAll("[data-message-author-role='assistant']").length,
      selector_image_count: document.querySelectorAll("main img,[role='main'] img,#thread img").length,
      selector_chat_thread_count: document.querySelectorAll("#thread,[data-testid='conversation-turns']").length,
      // Metadata only: no SSE body, raw network payload, URL, HTML or model
      // labels are sent to the evidence store.
      network_stream_seen: typeof active?.networkStreamSeen === "boolean"
        ? active.networkStreamSeen : null,
      network_stream_chunks: Number.isFinite(active?.networkStreamChunks)
        ? Math.min(1000000, Math.max(0, active.networkStreamChunks)) : null,
      network_stream_bytes: Number.isFinite(active?.networkStreamBytes)
        ? Math.min(999999999, Math.max(0, active.networkStreamBytes)) : null,
      network_stream_terminal_seen: typeof active?.networkTerminalAt === "number"
        ? Boolean(active.networkTerminalAt) : null,
      network_stream_unverified_end: typeof active?.networkStreamEndedWithoutTerminalAt === "number"
        ? Boolean(active.networkStreamEndedWithoutTerminalAt) : null,
      network_stream_last_age_ms: Number.isFinite(active?.networkObservedAt) && active.networkObservedAt > 0
        ? Math.min(999999999, Math.max(0, Date.now() - active.networkObservedAt)) : null,
      network_terminal_chunks: Number.isFinite(active?.networkTerminalChunks)
        ? Math.min(1000000, Math.max(0, active.networkTerminalChunks)) : null,
      model_picker_count: document.querySelectorAll(
        "button[data-testid*='model-selector'],[aria-label*='Model'],[aria-label*='模型']"
      ).length,
      reasoning_selector_count: document.querySelectorAll(
        "button[data-testid*='reasoning'],[aria-label*='Thinking'],[aria-label*='推理']"
      ).length,
      tool_selector_count: document.querySelectorAll(
        "button[data-testid*='tool'],button[aria-label*='Tools'],button[aria-label*='工具']"
      ).length,
      file_upload_selector_count: document.querySelectorAll(
        "input[type='file'],button[aria-label*='Attach'],button[aria-label*='附件']"
      ).length,
      image_editor_count: document.querySelectorAll(
        "button[data-testid*='edit'],button[aria-label*='Edit image']"
      ).length,
      selector_composer_count: document.querySelectorAll(
        "#prompt-textarea,[contenteditable='true'],textarea"
      ).length,
      selector_send_button_count: document.querySelectorAll(
        "button[data-testid='send-button'],button[aria-label='Send prompt']"
      ).length,
      selector_stop_button_count: document.querySelectorAll(
        "button[data-testid='stop-button'],button[aria-label='Stop generating']"
      ).length,
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
      // Full baseline and final evidence for every enabled tool. During the
      // request, spend the remaining event budget on the 3 most diagnostic
      // tools instead of exhausting 10 slots per early snapshot.
      maxEvents: 32, terminalSlots: enabled.length,
      progressTools: ["abcd_files_v1","response_boundary_v1","network_evidence_v1"]
        .filter(id => enabled.includes(id)),
      handoffReported: false, thresholdReported: false, lastSentAt: 0,
      checkpointsReported: 0, deadlineReported: false,
    };
    active.probeSession = session;
    capture(active, "request_pre_submit", {});
    return session;
  }

  function capture(active, stage, extra = {}) {
    const session = active?.probeSession;
    if (!session || !Object.prototype.hasOwnProperty.call(delayByStage, stage)) return Promise.resolve();
    if (stage === "before_terminal" && session.terminalReported) return Promise.resolve();
    // Probes are observers, not owners. Reserve an event for each enabled
    // tool at before_terminal, and dedicate intermediate slots to file,
    // boundary, and network evidence, including late (>10min) checkpoints.
    // Never exceed the server's hard 32-event request budget.
    const terminal = stage === "before_terminal";
    const baseline = stage === "request_pre_submit";
    const deadline = stage === "deadline_checkpoint";
    const selectedTools = baseline || terminal || deadline ? [...session.enabled]
      : (session.progressTools.length ? session.progressTools : [...session.enabled]);
    // On server-enforced 900s timeout, the content controller may never
    // receive cancellation/terminal. Spend the final reserve at 13.5min.
    const available = session.maxEvents - session.sequence - (terminal || deadline ? 0 : session.terminalSlots);
    const sampledTools = selectedTools.slice(0, Math.max(0, available));
    if (!sampledTools.length) return Promise.resolve();
    if (stage === "media_handoff" && session.handoffReported) return Promise.resolve();
    if (stage === "unresolved_after_threshold" && session.thresholdReported) return Promise.resolve();
    if (terminal) session.terminalReported = true;
    if (stage === "media_handoff") session.handoffReported = true;
    if (stage === "unresolved_after_threshold") session.thresholdReported = true;
    let facts;
    try { facts = observerFacts(active); }
    catch (_) {
      facts = {main_found: false, candidate_image_count: null};
    }
    const elapsed = Math.max(0, Date.now() - session.started_at);
    facts.request_elapsed_ms = elapsed;
    if (typeof extra.generating === "boolean") facts.generating = extra.generating;
    if (typeof extra.handoff === "boolean") facts.media_handoff_observed = extra.handoff;
    if (typeof extra.newCandidates === "number") facts.new_image_candidate_count = extra.newCandidates;
    if (typeof extra.captured === "number") facts.image_transfer_successes = extra.captured;
    if (stage === "before_terminal" && typeof extra.deliveredText === "string") {
      const output = extra.deliveredText;
      const lines = output.split(/\r?\n/);
      facts.scene_delivered_chars = output.length;
      facts.scene_delivered_table_rows = lines.filter(line => /^\s*\|.*\|\s*$/.test(line)).length;
      facts.scene_delivered_code_fences = lines.filter(line => /^\s*`{3,}/.test(line)).length;
      facts.scene_delivered_formula_markers = ["$", "\\[", "\\]", "\\(", "\\)"]
        .reduce((sum, marker) => sum + output.split(marker).length - 1, 0);
      facts.scene_delivered_heading_count = lines.filter(line => /^#{1,6}\s+/.test(line)).length;
    }
    // Scene evidence is sampled by the existing Worker tool supervisor. No
    // extra request listeners or terminal owners are introduced.
    if (active?.lastCandidateNode?.isConnected === false) active.lastCandidateNode = null;
    let classification = "baseline";
    if (stage === "before_terminal") classification = extra.completed
      ? "completed_observed" : "request_error_observed";
    else if (extra.handoff && !facts.candidate_image_count)
      classification = "handoff_without_visible_media";
    else if (!facts.assistant_turn_nodes)
      classification = Number(facts.scene_changed_block_count || 0) > 0
        || Number(facts.scene_new_final_action_count || 0) > 0
        ? "rendered_reply_without_role" : "no_assistant_turn";
    else if (Number(extra.newCandidates || 0) > 0)
      classification = "media_candidate_detected";
    else if (facts.visible_large_images)
      classification = "visible_media_unattributed";
    else if (stage === "unresolved_after_threshold")
      classification = "waiting_for_result";
    if (deadline) classification = "approaching_server_deadline";
    const sends = [];
    for (const tool_id of sampledTools) {
      if (session.sequence >= 32) break;
      // Snapshot the unique sequence *before* the asynchronous send. Reading
      // session.sequence inside Promise.then gave every tool in the same
      // stage the last sequence number instead of its own assigned number.
      const sequence = ++session.sequence;
      sends.push(Promise.resolve().then(() => chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "probe.event", request_id: session.request_id,
          tool_id, stage, sequence,
          classification, facts,
        },
      })).catch(() => {}));
    }
    // The terminal controller awaits at most 900ms for these events to reach
    // the authenticated Worker transport, without allowing probes to own status.
    return Promise.race([Promise.allSettled(sends), new Promise(resolve => setTimeout(resolve, 900))]);
  }

  function observe(active, {handoff = false, generating = false,
                             newCandidates = 0, captured = 0} = {}) {
    const session = active?.probeSession;
    if (!session) return;
    if (handoff && !session.handoffReported) capture(active, "media_handoff",
      {handoff, generating, newCandidates, captured});
    // A 900-second C run needs post-generation evidence, not just 60s data.
    // With ten tools: 10 baseline + four three-tool checkpoints + 10 final
    // = 32. If a media handoff consumes slots, later samples yield before
    // violating the cap; final capture still has the reserved slots.
    const elapsed = Date.now() - session.started_at;
    const milestones = [60000, 180000, 420000, 720000];
    if (session.enabled.size < 5 && !session.thresholdReported
        && elapsed >= session.threshold) {
      capture(active, "unresolved_after_threshold",
        {handoff, generating, newCandidates, captured});
    }
    while (session.checkpointsReported < milestones.length
      && elapsed >= milestones[session.checkpointsReported]) {
      session.checkpointsReported += 1;
      capture(active, "progress_checkpoint",
        {handoff, generating, newCandidates, captured});
    }
    if (!session.deadlineReported && elapsed >= 810000) {
      session.deadlineReported = true;
      capture(active, "deadline_checkpoint",
        {handoff, generating, newCandidates, captured});
    }
  }

  globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__ = Object.freeze({
    begin, capture, observe, snapshot: observerFacts,
  });
})();
