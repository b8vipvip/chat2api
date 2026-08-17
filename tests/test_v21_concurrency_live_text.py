from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.live_voice_patch import _input_text_from_control
from app.main import create_app
from app.v21_patch import CAPACITY_UNITS, CAPACITY_WAIT_SECONDS, install_v21_patch, request_weight


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-master-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-password-for-v21-concurrency-test",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=30,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def test_request_weights_match_default_capacity_policy() -> None:
    assert CAPACITY_UNITS == 3
    assert CAPACITY_WAIT_SECONDS == 1.5
    assert request_weight("req_text") == 1
    assert request_weight("req_multimodal") == 1
    assert request_weight("imgreq_image") == 2
    assert request_weight("voicereq_audio") == 2
    assert request_weight("live_session") == 2


def test_one_extension_accepts_three_parallel_text_requests(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(settings(tmp_path))
        install_v21_patch(app)
        broker = app.state.broker
        states = [
            await broker.create("req_a", "ext_one"),
            await broker.create("req_b", "ext_one"),
            await broker.create("req_c", "ext_one"),
        ]
        assert len(states) == 3
        assert broker.client_used_units("ext_one") == 3
        assert broker.capacity_snapshot("ext_one")["active_requests"] == 3
        assert not broker.can_accept("ext_one", 1)
        assert "ext_one" in app.state.registry.busy_clients
        for state in states:
            await broker.release(state.request_id)
        assert broker.client_used_units("ext_one") == 0
        assert broker.can_accept("ext_one", 2)

    asyncio.run(scenario())


def test_image_plus_text_share_three_capacity_units_and_waiter_wakes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(settings(tmp_path))
        install_v21_patch(app)
        broker = app.state.broker
        image = await broker.create("imgreq_a", "ext_one")
        text = await broker.create("req_a", "ext_one")
        assert broker.client_used_units("ext_one") == 3
        assert not broker.can_accept("ext_one", 1)
        assert not broker.can_accept("ext_one", 2)

        waiting = asyncio.create_task(broker.create("req_waiting", "ext_one"))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await broker.release(text.request_id)
        resumed = await asyncio.wait_for(waiting, timeout=0.5)
        assert resumed.request_id == "req_waiting"
        assert broker.client_used_units("ext_one") == 3
        assert resumed.diagnostics["extension_capacity_wait_ms"] >= 40
        await broker.release(resumed.request_id)
        await broker.release(image.request_id)

    asyncio.run(scenario())


def test_live_text_accepts_simple_and_realtime_item_shapes() -> None:
    assert _input_text_from_control({"type": "input_text", "text": "边说边看这句"}) == "边说边看这句"
    assert _input_text_from_control({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "第一行"},
                {"type": "text", "text": "第二行"},
            ],
        },
    }) == "第一行\n第二行"
    assert _input_text_from_control({"type": "conversation.item.create", "item": {"role": "assistant", "content": "x"}}) == ""


def test_extension_uses_three_per_key_workers_after_affinity_warm_pool() -> None:
    workers = (EXTENSION / "conversation_workers_v24.js").read_text(encoding="utf-8")
    dispatch = (EXTENSION / "conversation_dispatch.js").read_text(encoding="utf-8")
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    warm = (EXTENSION / "conversation_warm_pool_v2.js").read_text(encoding="utf-8")

    assert "MAX_WORKERS_PER_KEY = 3" in workers
    assert "::worker${index}" in workers
    assert "logical_api_key_id" in workers
    assert "worker_index" in workers
    assert "extension_worker_router" in workers
    assert "requestTabs: new Map()" in dispatch
    assert '"voice.live.start"' in dispatch
    assert "Serialize only route allocation / page dispatch" in dispatch
    assert "MAX_WARM_SLOTS = 2" in warm
    assert entry.index('"conversation_warm_pool_v2.js"') < entry.index('"conversation_workers_v24.js"')
    assert entry.index('"conversation_workers_v24.js"') < entry.index('"conversation_dispatch.js"')


def test_live_voice_routes_text_to_same_bound_tab_without_stopping_audio() -> None:
    server = (ROOT / "app" / "live_voice_patch.py").read_text(encoding="utf-8")
    routing = (EXTENSION / "audio_routing_live.js").read_text(encoding="utf-8")
    content = (EXTENSION / "content_voice_live_text_v24.js").read_text(encoding="utf-8")

    assert 'control_type in {"input_text", "conversation.item.create"}' in server
    assert '"type": "voice.live.text"' in server
    assert '"type": "input_text.queued"' in server
    assert 'event_type == "input.text.sent"' in server
    assert '"type": "input_text.sent"' in server
    assert 'type === "voice.live.text"' in routing
    assert '"content_voice_live.js", "content_voice_live_text_v24.js"' in routing
    assert "liveTabs.get(requestId)" in routing
    assert 'message.type === "chat2api.voice.live.text"' in content
    assert "ChatGPT Voice text composer" in content
    assert 'emitLive(active, "input.text.sent"' in content
    assert 'postMain("voice.live.stop"' not in content


def test_control_panel_docs_cover_concurrency_and_live_text_access() -> None:
    docs = (ROOT / "app" / "admin_v21.js").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "v21_patch.py").read_text(encoding="utf-8")
    routing_patch = (ROOT / "app" / "v21_routing_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    assert 'const VERSION = "0.21.0"' in docs
    assert "单扩展并发与容量" in docs
    assert "边通话边发文字" in docs
    assert "/v1/audio/realtime" in docs
    assert "conversation.item.create" in docs
    assert "input_text.queued" in docs
    assert "input_text.sent" in docs
    assert 'PATCH_VERSION = "0.21.0"' in patch
    assert '"extension_weighted_concurrency": True' in patch
    assert '"live_voice_text_input": True' in patch
    assert 'ROUTED_REQUEST_TYPES = {"chat.request", "image.request", "voice.request", "voice.live.start"}' in routing_patch
    assert entry.index("install_v20_3_patch(app)") < entry.index("install_v21_patch(app)")
    assert entry.index("install_v21_patch(app)") < entry.index("install_v21_routing_patch(app)")
