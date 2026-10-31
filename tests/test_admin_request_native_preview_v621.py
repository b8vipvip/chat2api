"""Admin preview is separate from the canonical API reply and requires asset ownership."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE=(ROOT/"app"/"request_history_v94_patch.py").read_text("utf-8")
SERVER=(ROOT/"app"/"main.py").read_text("utf-8")

def test_request_dialog_has_explicit_native_preview_switch():
    assert 'requestConversationNativeMode' in SOURCE
    assert 'requestConversationMarkdownMode' in SOURCE
    assert 'requestHistoryLoadNativePreview(requestId,row,sequence)' in SOURCE
    assert '/api/admin/requests/' in SOURCE and "'/native-content'" in SOURCE
    assert '@app.get("/api/admin/requests/{request_id}/native-content", dependencies=[Depends(require_admin_key)])' in SERVER

def test_unmapped_images_fail_closed_to_canonical_markdown():
    assert 'summary.media_mapping_complete!==true' in SOURCE
    assert 'Number(summary.images)!==media.length' in SOURCE
    assert 'requestHistoryNativeNode(snapshot.root,assets,{count:0})' in SOURCE
    assert "nativeButton.disabled=true" in SOURCE
    assert "预览不等于像素级 1:1 验收" in SOURCE

def test_archived_media_list_is_not_capped_at_four_and_supports_med_id():
    assert "row.response_media.slice(0,40)" in SOURCE
    assert "row?.response_media)?row.response_media.slice(0,40)" in SOURCE
    assert "response_media.slice(0,4)" not in SOURCE
    assert "requestHistoryOwnedImageUrl(item?.owned_url||item?.url)" in SOURCE
    assert "requestHistoryMediaUrls.push(imageUrl)" in SOURCE
    assert 'fileId' in SOURCE

def test_native_preview_builds_dom_from_approved_elements_without_raw_html():
    assert "requestHistoryNativeTags" in SOURCE
    assert "requestHistoryNativeStyles" in SOURCE
    assert "requestHistoryNativeNode" in SOURCE
    assert "node=document.createElement(value.tag)" in SOURCE
    assert "node.textContent=value.text" in SOURCE
    assert "node.style[key]=raw" in SOURCE
    assert "node.innerHTML" not in SOURCE
    assert "node.style.cssText=raw" not in SOURCE


def test_request_detail_refreshes_deferred_files_without_overwriting_terminal_history():
    assert "current = dict(row)" in SERVER
    assert 'row.get("response_generated_files")' in SERVER
    assert "await asyncio.to_thread(" in SERVER
    assert "deferred_generated_files.status, matched.group(1), matched.group(2)" in SERVER
    assert 'current["response_generated_files_live_checked"] = True' in SERVER
    assert "current[\"response_generated_files\"] = live" in SERVER


def test_native_preview_renderer_matches_current_3200_node_capture_limit():
    # 49070880: Worker successfully captured 1311 native nodes but the old
    # admin renderer threw once its local counter exceeded 1200.
    assert "++budget.count>3200" in SOURCE
    assert "++budget.count>1200" not in SOURCE


def test_pending_generated_files_refresh_without_changing_request_terminal():
    # req_49070880 exported four processing files; a one-shot read remains stale.
    assert "async function requestHistoryMonitorGeneratedFiles(requestId,initialFiles,sequence)" in SOURCE
    assert "void requestHistoryMonitorGeneratedFiles(requestId,files,sequence);" in SOURCE
    assert "row?.response_generated_files_live_checked!==true" in SOURCE
    assert "f?.status==='processing'||f?.status==='pending'" in SOURCE
    assert "sequence!==requestHistoryMediaSequence" in SOURCE
    assert "setTimeout(resolve,4000)" in SOURCE
    assert "attempt<45" in SOURCE
    assert "if(++errors>=3)" in SOURCE
    assert "requestHistoryGeneratedFileStatusLabel(files)" in SOURCE


def test_33da_admin_native_preview_supports_111_siblings():
    assert "if(children.length>160)throw new Error('原生快照子节点超限');" in SOURCE
    assert "if(children.length>80)throw new Error('原生快照子节点超限');" not in SOURCE
