"""Windows extension diagnosis is strictly bounded and request-terminal independent."""
import asyncio
import base64
import hashlib
import json
import pytest

from app.native_page_windows_bridge import WindowsNativePageBridge, PART_BYTES


class FakeRegistry:
    def __init__(self):
        self.sockets={"ext_abcdef123456":object()}
        self.sent=[]
    async def send(self,client_id,message):
        self.sent.append((client_id,message))


@pytest.mark.asyncio
async def test_windows_chunk_reassembly_owner_bound_and_ordered():
    registry=FakeRegistry()
    bridge=WindowsNativePageBridge(registry)
    report={"schema":"windows-native-page-evidence-v1",
            "boundary_verified":True,"complete":True,
            "probe":{"assistant_role_nodes":2},
            "counts":{"nodes":3,"text_chars":5,"tables":1,"images":0},
            "limitations":["No full-page screenshot"],
            "root":{"tag":"div","children":[{"tag":"#text","text":"Hello"}]}}
    data=json.dumps(report).encode()
    sha=hashlib.sha256(data).hexdigest()
    job="a"*32
    task=asyncio.create_task(bridge.collect("ext_abcdef123456",job,
        "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",timeout=5))
    await asyncio.sleep(0)
    assert registry.sent[0][1]["type"]=="native.page.windows.start"
    await bridge.ingest("ext_other",{
        "type":"native.page.windows.finished","job_id":job,"ok":False,
        "error":"spoofed"})
    assert not task.done()
    for index,offset in enumerate(range(0,len(data),PART_BYTES)):
        await bridge.ingest("ext_abcdef123456",{
            "type":"native.page.windows.chunk","job_id":job,
            "index":index,"count":(len(data)+PART_BYTES-1)//PART_BYTES,
            "size":len(data),"sha256":sha,
            "data":base64.b64encode(data[offset:offset+PART_BYTES]).decode()})
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.finished","job_id":job,
        "ok":True,"sha256":sha,"size":len(data)})
    parsed,raw=await task
    assert parsed["counts"]["tables"]==1
    assert raw==data
    assert not bridge.pending


@pytest.mark.asyncio
async def test_windows_diagnostic_rejects_invalid_base64():
    registry=FakeRegistry()
    bridge=WindowsNativePageBridge(registry)
    job="b"*32
    task=asyncio.create_task(bridge.collect("ext_abcdef123456",job,
        "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",timeout=5))
    await asyncio.sleep(0)
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.chunk","job_id":job,"index":0,
        "count":1,"size":2,"sha256":"a"*64,"data":"not-base64!"})
    with pytest.raises(ValueError):
        await task


@pytest.mark.asyncio
async def test_windows_diagnostic_rejects_chunk_sequence_and_digest():
    registry=FakeRegistry()
    bridge=WindowsNativePageBridge(registry)
    job="c"*32
    task=asyncio.create_task(bridge.collect("ext_abcdef123456",job,
        "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",timeout=5))
    await asyncio.sleep(0)
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.chunk","job_id":job,"index":1,
        "count":2,"size":32769,"sha256":"b"*64,"data":"AA=="})
    with pytest.raises(ValueError):
        await task


@pytest.mark.asyncio
async def test_windows_diagnostic_rejects_finished_size_mismatch():
    registry=FakeRegistry()
    bridge=WindowsNativePageBridge(registry)
    job="d"*32
    payload=b'{"schema":"windows-native-page-evidence-v1"}'
    digest=hashlib.sha256(payload).hexdigest()
    task=asyncio.create_task(bridge.collect("ext_abcdef123456",job,
        "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",timeout=5))
    await asyncio.sleep(0)
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.chunk","job_id":job,
        "index":0,"count":1,"size":len(payload),"sha256":digest,
        "data":base64.b64encode(payload).decode()})
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.finished","job_id":job,
        "ok":True,"sha256":digest,"size":len(payload)+1})
    with pytest.raises(ValueError,match="windows_diagnostic_transfer_incomplete"):
        await task
    assert not bridge.pending


@pytest.mark.asyncio
async def test_windows_visible_progress_is_authenticated_bounded_and_not_chat_terminal():
    registry=FakeRegistry()
    bridge=WindowsNativePageBridge(registry)
    job="e"*32
    timeline=[]
    async def progress(event):
        timeline.append(event)
    task=asyncio.create_task(bridge.collect(
        "ext_abcdef123456",job,
        "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",
        timeout=5,show_tab=True,on_progress=progress))
    await asyncio.sleep(0)
    assert registry.sent[0][1]["show_tab"] is True
    valid={"type":"native.page.windows.progress","job_id":job,
           "stage":"tab_opened","elapsed_ms":53,
           "tab_id":104,"window_id":26,"show_tab":True}
    await bridge.ingest("ext_spoofed",valid)
    await bridge.ingest("ext_abcdef123456",{**valid,"show_tab":False})
    await bridge.ingest("ext_abcdef123456",{**valid,"stage":"chat.completed"})
    await bridge.ingest("ext_abcdef123456",{**valid,"elapsed_ms":"53"})
    assert timeline==[]
    await bridge.ingest("ext_abcdef123456",{**valid,"conversation_url":"https://bad.invalid"})
    assert len(timeline)==1
    assert timeline[0]["stage"]=="tab_opened"
    assert timeline[0]["tab_id"]==104
    assert timeline[0]["window_id"]==26
    assert timeline[0]["elapsed_ms"]==53
    assert "conversation_url" not in timeline[0]
    assert "received_at_ms" in timeline[0]
    await bridge.ingest("ext_abcdef123456",{
        "type":"native.page.windows.finished","job_id":job,
        "ok":False,"error":"worker_busy_visual_diagnosis"})
    with pytest.raises(RuntimeError,match="worker_busy_visual_diagnosis"):
        await task
    assert not bridge.pending


@pytest.mark.asyncio
async def test_windows_diagnostic_rejects_nonboolean_visual_mode():
    bridge=WindowsNativePageBridge(FakeRegistry())
    with pytest.raises(ValueError,match="invalid_visual_diagnostic_option"):
        await bridge.collect("ext_abcdef123456","f"*32,
            "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef",
            show_tab="true")
