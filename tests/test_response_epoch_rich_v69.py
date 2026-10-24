from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_manifest_installs_request_v6_as_epoch_terminal_owner():
    manifest=json.loads(read("chrome_extension/manifest.json")); scripts=manifest["content_scripts"][1]["js"]
    assert scripts.index("content_request_v5.js") < scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js")
    assert scripts.index("content_request_v6.js") < scripts.index("content_chatgpt_choice_policy_v143.js") < scripts.index("content_interruption_guard_v72.js")
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_terminal_integrity_v89.js" not in scripts

def test_request_v6_binds_reply_to_current_prompt_turn_not_assistant_count():
    source=read("chrome_extension/content_request_v6.js")
    for token in ('const KEY = "__CHAT2API_REQUEST_CONTENT_V6__"',"function currentUserTurn(active)","function promptMatchesTurn(turn, active)",'reason: "after-current-user-turn"','"historical-turn"',"turnFollows(currentUser, turnOf(node))","active.baselineNodes = new Set(nodes)","v5.active = active","v5.listener = listener",'request_controller: "request-v6"',"response_epoch_revision: 69",'response_format: "markdown"'):
        assert token in source
    assert "nodes.length > active.baselineCount" not in source

def test_choice_policy_defaults_ab_reply_to_reply1_and_stays_in_chat_mode():
    source=read("chrome_extension/content_chatgpt_choice_policy_v143.js")
    for token in ('const KEY = "__CHAT2API_CHATGPT_CHOICE_POLICY_V143__"',"function dualReplyPair(active)",'dual_reply_policy: "reply1"',"dual_reply_feedback_clicked: false",'work_mode_offer_action: "stay-in-chat"',"留在聊天模式","stay in chat mode"):
        assert token in source
    assert "改用工作模式" not in source

def test_rich_serializer_preserves_semantic_answer_content_and_excludes_ui_chrome():
    source=read("chrome_extension/content_rich_response_v69.js")
    for token in ('format: "markdown"','"button"','"[data-testid*=\'copy\']"',"clone.querySelectorAll(ACTION_SELECTOR)","tableMarkdown(table)","listItemText(li, depth, ordered, index)",'if (tag === "pre")','if (tag === "img")',"![${alt.replace","MAX_INLINE_IMAGES = 4","MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024","captureFinalMarkdown"):
        assert token in source

def test_manual_playground_waits_for_exact_final_reply_and_safely_renders_markdown():
    source=read("app/admin_playground_chat_v69.js"); patch=read("app/playground_chat_patch.py")
    assert 'PATCH_ID = "playground-chat-v3"' in patch
    assert 'with_name("admin_playground_chat_v69.js")' in patch
    assert "stream:false" in source.replace(" ","")
    assert "payload.choices?.[0]?.message?.content" in source
    assert "function renderMarkdown(markdown)" in source
    assert "innerHTML = assistantText" not in source

def test_v69_javascript_syntax():
    for path in ("chrome_extension/content_rich_response_v69.js","chrome_extension/content_request_v6.js","chrome_extension/content_chatgpt_choice_policy_v143.js","app/admin_playground_chat_v69.js"):
        result=subprocess.run(["node","--check",str(ROOT/path)],capture_output=True,text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_generated_media_handoff_keeps_single_request_terminal_authority():
    source = read("chrome_extension/content_request_v6.js")
    rich = read("chrome_extension/content_rich_response_v69.js")
    assert 'function isInternalMediaHandoff(value)' in source
    assert 'function hasTransferableImage(value)' in source
    assert 'captureMedia && (sameTurnRoot || sourceMedia.length) && sourceStable && !generating' in source
    assert 'active.baselineUserTurns = new Set' in source
    assert 'response_terminal_source: "verified-request-media-dom-v176"' in source
    assert 'networkTerminalText && !captureMedia' in source
    assert '!promptStillPresent(active)' in source
    assert 'isLocalImageBuildCommand(networkTerminalText)' in source
    assert 'networkText && !captureMedia && !fileCreationRequested && !imageCreationRequested' in source
    assert '!networkTerminalAt && !captureMedia' in source
    assert 'internal_tool_handoff_without_media: true' in source
    assert 'if (mediaOutsideMarkdown) return node;' in rich
    assert 'if (filesOutsideMarkdown) return node;' in rich
    assert 'image_inlined_count: imageStats.inlined' in rich
    assert source.count('type: "chat.completed"') >= 2


def test_worker_probe_is_read_only_extension_observer_not_second_terminal_owner():
    manifest = json.loads(read("chrome_extension/manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_rich_response_v69.js") < scripts.index(
        "content_probe_supervisor_v1.js"
    ) < scripts.index("content_request_v6.js")
    owner = read("chrome_extension/content_request_v6.js")
    probe = read("chrome_extension/content_probe_supervisor_v1.js")
    assert "probeSupervisor?.begin?.(active)" in owner
    assert '__CHAT2API_PROBE_SUPERVISOR_V1__?.observe?.(active' in owner
    assert 'type: "probe.event"' in probe
    assert "chat.completed" not in probe
    assert "chat.error" not in probe
    assert "request_pre_submit" in probe
    assert "unresolved_after_threshold" in probe
    assert owner.count('type: "chat.completed"') >= 2

def test_image_v3_and_chat_v6_flush_probe_before_terminal():
    image = read("chrome_extension/content_image_v3.js")
    chat = read("chrome_extension/content_request_v6.js")
    probe = read("chrome_extension/content_probe_supervisor_v1.js")
    manifest = json.loads(read("chrome_extension/manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_probe_supervisor_v1.js") < scripts.index("content_image_v3.js")
    assert 'options: message.options || {}' in image
    assert '__CHAT2API_PROBE_SUPERVISOR_V1__?.begin?.(active)' in image
    assert '__CHAT2API_PROBE_SUPERVISOR_V1__?.observe?.(active' in image
    assert 'await globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__?.capture?.(' in image
    assert 'await globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__?.capture?.(' in chat
    assert 'event.type === "image.completed"' in image
    assert 'event.type === "chat.completed"' in chat
    assert '"before_terminal"' in probe
    assert "session.terminalReported = true" in probe
    # No new secondary terminal authority or request routing in the observer.
    for action in ("image.completed", "image.error", "chat.completed", "chat.error"):
        assert 'type: "' + action + '"' not in probe


def test_verified_media_handoff_intrinsic_card_still_uses_one_request_owner():
    """TX03 can expose a loaded 1254px blob preview with a sub-96px DOM box."""
    owner = read("chrome_extension/content_request_v6.js")
    rich = read("chrome_extension/content_rich_response_v69.js")
    playground = read("app/admin_playground_chat_v69.js")
    assert "const intrinsicHandoff = Boolean(imageHandoff)" in owner
    assert "active.baselinePageMedia, freshUserAnchor, intrinsicHandoff" in owner
    assert "capturePageImages(sourceMedia, {allowIntrinsic: intrinsicHandoff})" in owner
    assert "allowIntrinsic && img.complete === true" in rich
    assert "Number(img.naturalWidth) >= 256" in rich
    assert "Number(img.naturalHeight) >= 256" in rich
    assert "const keys = new Set(" in rich
    assert "oldKeys.has(candidateMediaKey(img))" in rich
    assert "trustedMediaSource(src)" in rich
    assert "media-new-image-card-found" in owner
    assert "delivered > 0 && (hasTransferableImage(captured) || hasTransferableFile(captured))" in owner
    assert 'response_terminal_source: "verified-request-media-dom-v176"' in owner
    assert "stream:false,timeout:300" not in playground
    assert 'response_format:"b64_json",n:1,timeout:600' in playground
    # No separate terminal controller was created for probe-discovered bitmaps.
    probe = read("chrome_extension/content_probe_supervisor_v1.js")
    assert 'type: "chat.completed"' not in probe
    assert 'type: "chat.error"' not in probe


def test_tx03_image_recipe_is_not_a_successful_image_terminal():
    owner = read("chrome_extension/content_request_v6.js")
    rich = read("chrome_extension/content_rich_response_v69.js")
    assert "function isLocalImageBuildCommand(value)" in owner
    assert "function isImageCreationRequest(prompt)" in owner
    assert "const imageBuildArtifact = imageCreationRequested" in owner
    assert "const intrinsicHandoff = Boolean(imageHandoff) || imageBuildArtifact" in owner
    assert "|| visibleTurnMedia || visibleTurnFiles || fileCreationRequested || pageMediaFound" in owner
    assert "media_last_baseline_excluded" in owner
    assert "ChatGPT returned an image-building command, but no verified transferable image was captured" in owner
    assert "media_excluded_by_gate" in owner
    assert "excluded_by_media_gate" in rich
    assert "excluded_by_baseline" in rich
    # A read-only probe must not become another completion/error owner.
    probe = read("chrome_extension/content_probe_supervisor_v1.js")
    assert 'type: "chat.completed"' not in probe
    assert 'type: "chat.error"' not in probe


def test_tool_argument_json_cannot_be_final_and_rich_markup_stays_grouped():
    """TX03 video/reference-image request must not complete with image tool args."""
    owner = read("chrome_extension/content_request_v6.js")
    rich = read("chrome_extension/content_rich_response_v69.js")
    assert "function combinePageMediaText(prose, mediaText)" in owner
    assert "const captured = combinePageMediaText(assistantProse, finalMedia?.text);" in owner
    assert "function isImageToolArguments(value)" in owner
    assert "const imageToolArguments = imageCreationRequested" in owner
    assert "|| imageToolArguments;" in owner
    assert "&& !internalToolPayload(networkTerminalText)" in owner
    assert "&& !internalToolPayload(networkText)" in owner
    assert "function explicitCreationIntent(prompt, subjects)" in owner and "参考图" in owner
    assert "不需要|不用|不要求|不应该" in owner
    assert "function renderedCurrentRequestCandidate(active)" in owner
    assert "async function reconcileResponsesTerminal(active, networkText)" in owner
    assert "incomplete-short-sse-rich-reply-v192" in owner
    assert "verified-short-sse-rich-dom-v192" in owner
    assert 'response_terminal_dom_reconciled: ["validated-same-turn-dom-v163"' in owner
    assert "renderedCurrentRequestCandidate(active)" in owner
    assert "same-request-rendered-dom-baseline-v191" in owner
    assert "baseline.get(node) === text.slice(-5000)" in owner
    assert 'const FILE_ANCHORS = "a[href]"' in rich
    assert "let inline = \"\";" in rich
    assert "inline += inlineMarkdown(child);" in rich
    assert ".map(cell => inlineChildren(cell)" in rich
    # Exercise the real JS serializer and parser helpers (not only static tokens).
    result = subprocess.run(
        ["node", "tests/rich_multimodal_handoff_v195.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_file_handoff_uses_existing_single_terminal_owner():
    owner = read("chrome_extension/content_request_v6.js")
    rich = read("chrome_extension/content_rich_response_v69.js")
    main = read("app/main.py")
    assert "function hasTransferableFile(value)" in owner
    assert "function isFileCreationRequest(prompt)" in owner
    assert "rich?.hasCandidateFile?.(sameTurnRoot)" in owner
    assert "const captured = combinePageMediaText(assistantProse, finalMedia?.text);" in owner
    assert "hasTransferableFile(captured)" in owner
    assert "file_inlined_count: fileStats.count" in rich
    assert "generated_files = GeneratedFileArchive(media_archive)" in main
    assert "archive_final_markdown, raw_final" in main
    assert "archive_final_markdown, text" in main
    assert "type: \"chat.completed\"" not in rich
    relay = read("chrome_extension/background_media_fetch_v175.js")
    assert 'type !== "chat2api.file.fetch-v188"' in relay
    assert 'credentials: "omit", redirect: "error"' in relay
    assert 'MAX_FILE_RELAY_BYTES = 4 * 1024 * 1024' in relay
    assert 'type: "chat2api.file.fetch-v188"' in rich
    assert 'const preservedProse = candidate.replace(' in owner
    assert 'return [preservedProse, imageMarkdown].filter(Boolean).join' in owner
    result = subprocess.run(
        ["node", "tests/generated_file_handoff_v196.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tx03_short_rich_editor_drift_uses_existing_v6_single_send_terminal_owner():
    """v0.22.175 request failures must not add another click or terminal arbiter."""
    source = read("chrome_extension/content_request_v6.js")
    assert "function editorLayoutWhitespaceEvidence(observed, target, source)" in source
    assert "promptMatchEvidence(composerText(current), active)" in source
    assert 'return "rich-editor-layout-whitespace"' in source
    assert "send_prompt_nonwhitespace_equal" in source
    assert "send_prompt_layout_allowed" in source
    assert 'send_prompt_match: ready.promptMatch' in source
    assert 'send_prompt_match_source: ready.promptMatchSource' in source
    assert 'promptMatch: renderedMatch !== "none" ? renderedMatch : contentMatch' in source
    # The existing v6 request still owns the sole native rewrite, click and
    # chat.error completion. Telemetry and media origin remain read-only.
    assert source.count("ready.button.click()") == 1
    assert source.count("promptRepairAttempts += 1") == 1
    assert 'await rewriteDriftedDraft(active, current)' in source
    assert 'function classifySubmissionState(active)' in source
    assert 'async function submitAndConfirm(active)' in source
    assert 'async function runRequest(message)' in source
    assert 'type: "chat.error"' in source
    assert 'v5.listener = listener' in source
    result = subprocess.run(
        ["node", "tests/composer_readiness_reconcile_v126.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
