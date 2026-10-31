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
