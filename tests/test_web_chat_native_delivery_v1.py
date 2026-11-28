from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _install_shared_web_chat(app):
    install_control_plane_v1(app)
    shared_key, _shared_token = asyncio.run(app.state.api_keys.create("Web Chat native delivery test shared"))
    app.state.control_plane.update_client({"api_key_id": shared_key["key_id"]})
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    return shared_key


def test_native_renderer_rejects_active_nodes_and_untrusted_urls() -> None:
    node = shutil.which("node")
    if node is None:
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/web_chat_native_renderer_v1.mjs"],
        cwd=root, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native-renderer-validation-passed" in result.stdout



def test_native_interaction_worker_dispatch_uses_original_tab():
    node = shutil.which("node")
    if node is None:
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/native_interaction_dispatch_v1.mjs"],
        cwd=root, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native-interaction-original-worker-tab-dispatch-passed" in result.stdout


def test_user_native_content_adapter_enforces_key_owner_and_preserves_validated_svg(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-native-test-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    native_snapshot = {
        "version": 1,
        "revision": 4,
        "root": {
            "tag": "svg",
            "attrs": {"viewBox": "0 0 10 10", "width": "10", "height": "10"},
            "children": [{"tag": "path", "attrs": {"d": "M0 0 L10 10", "stroke": "#123456"}}],
        },
        "assets": [],
        "files": [],
        "finality": "verified-final",
    }

    async def snapshot_loader(request_id, owner, *, watch_pending_archive=False, preserve_native_tree=False):
        if request_id != "req_nativeproof1234" or not owner:
            return None
        result = {**native_snapshot}
        if watch_pending_archive:
            result["_pending_archive_pollable"] = False
        return result

    app.state.native_response_snapshot = snapshot_loader
    shared_key = _install_shared_web_chat(app)

    with TestClient(app) as client:
        first = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-owner@example.test", "password": "native-owner-password"},
        )
        assert first.status_code == 200, first.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        owner_session = client.get("/api/user/chat/session")
        assert owner_session.status_code == 200, owner_session.text
        owner_key_id = owner_session.json()["key_id"]

        client.cookies.clear()
        second = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-other@example.test", "password": "native-other-password"},
        )
        assert second.status_code == 200, second.text
        other_cookie = client.cookies.get(USER_SESSION_COOKIE)

        asyncio.run(app.state.telemetry.upsert({
            "request_id": "req_nativeproof1234",
            "api_key_id": owner_key_id,
            "shared_api_key_id": shared_key["key_id"],
            "web_chat_mode": True,
            "status": "completed",
            "response_media": [],
            "response_generated_files": [],
        }))

        client.cookies.clear()
        unauthenticated = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["cache-control"] == "private, no-store"

        client.cookies.set(USER_SESSION_COOKIE, other_cookie)
        hidden = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert hidden.status_code == 404

        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        response = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["finality"] == "verified-final"
        assert body["root"]["tag"] == "svg"
        assert body["root"]["attrs"]["viewBox"] == "0 0 10 10"
        assert body["root"]["children"][0]["attrs"]["d"] == "M0 0 L10 10"
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in response.headers["vary"].split(",")}

        events = client.get("/api/user/chat/requests/req_nativeproof1234/native-content/events")
        assert events.status_code == 200, events.text
        assert "event: native.snapshot" in events.text
        assert '"finality":"verified-final"' in events.text
        assert "event: native.end" in events.text
        assert events.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in events.headers["vary"].split(",")}


def test_mode1_chat_ingress_uses_session_and_server_shared_key(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="mode1-web-adapter-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    shared_key = _install_shared_web_chat(app)
    calls = []

    async def shared_adapter(body, request, x_chat2api_client, request_id, principal):
        calls.append({
            "request_id": request_id,
            "api_key_id": principal.key_id,
            "auth_key_id": principal.auth_key_id,
            "principal_kind": principal.kind,
            "mode1": request.scope.get("chat2api_mode1_web_chat") is True,
            "model": body.model,
        })
        from fastapi.responses import JSONResponse
        return JSONResponse({"mode": "mode1", "request_id": request_id, "api_key_id": principal.key_id})

    app.state.chat_completions_adapter = shared_adapter
    with TestClient(app) as client:
        client.headers.update({"Origin": "http://testserver"})
        registered = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "mode1-owner@example.test", "password": "mode1-owner-password"},
        )
        assert registered.status_code == 200, registered.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        owner_session = client.get("/api/user/chat/session")
        assert owner_session.status_code == 200, owner_session.text
        identity_key_id = owner_session.json()["key_id"]

        client.cookies.clear()
        missing_session = client.post(
            "/api/user/chat/completions",
            headers={"Origin": "http://testserver", "Authorization": "Bearer ignored-without-session", "X-Chat2API-Request-ID": "req_mode1test0001"},
            json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        assert missing_session.status_code == 401

        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        response = client.post(
            "/api/user/chat/completions",
            headers={"Origin": "http://testserver", "X-Chat2API-Request-ID": "req_mode1test0003"},
            json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "mode": "mode1", "request_id": "req_mode1test0003", "api_key_id": identity_key_id,
        }
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in response.headers["vary"].split(",")}
        assert calls == [{
            "request_id": "req_mode1test0003",
            "api_key_id": identity_key_id,
            "auth_key_id": shared_key["key_id"],
            "principal_kind": "web_chat",
            "mode1": True,
            "model": "gpt-5.6-sol",
        }]

def test_owner_scoped_native_interaction_applies_to_original_worker_and_persists(tmp_path: Path) -> None:
    from types import SimpleNamespace
    import time

    settings = Settings(
        CHAT2API_API_KEY="web-chat-native-action-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)

    # Exercise the real terminal finality authority, not a permissive loader.
    shared_key = _install_shared_web_chat(app)

    class AckSocket:
        def __init__(self):
            self.sent = []
            self.recapture = None
            self.media = []

        async def send_json(self, payload):
            self.sent.append(dict(payload))
            if payload.get("type") == "chat.native.interaction":
                await app.state.native_interaction_ack_receiver(
                    "worker-native-action", self, {
                        "type": "chat.native.interaction.ack",
                        "request_id": payload["request_id"],
                        "action_id": payload["action_id"],
                        "revision": payload["revision"],
                        "interaction_id": payload["interaction_id"],
                        "ok": True,
                        **({"recapture_status": "verified", "snapshot": self.recapture,
                            "media":self.media}
                           if self.recapture is not None else {}),
                    },
                )

    with TestClient(app) as client:
        registered = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-action-owner@example.test", "password": "native-action-owner-password"},
        )
        assert registered.status_code == 200, registered.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        session = client.get("/api/user/chat/session")
        assert session.status_code == 200, session.text
        owner_key = session.json()["key_id"]
        request_id = "req_native_action_12345"

        async def record():
            await app.state.telemetry.upsert({
                "request_id": request_id,
                "api_key_id": owner_key,
                "shared_api_key_id": shared_key["key_id"],
                "web_chat_mode": True,
                "client_id": "worker-native-action",
                "status": "completed",
                "diagnostics": {"native_snapshot_terminal_complete": True,
                                "native_capture_revision": 1, "native_snapshot_received_revision": 1,
                                "native_layout_summary": {"revision": 1, "images": 0, "archived_assets": 0}},
                "response_media": [],
                "response_generated_files": [],
            })
        asyncio.run(record())

        initial = {
            "version": 1,
            "revision": 1,
            "root": {"tag": "div", "children": [
                {"tag": "input", "interaction_id": "nc_1_1",
                 "control": {"kind": "checkbox", "label": "Enable", "disabled": False, "checked": False}},
                {"tag": "input", "interaction_id": "nc_1_2",
                 "control": {"kind": "range", "label": "Opacity", "disabled": False,
                             "min": 0, "max": 100, "step": 5, "value": 25}},
                {"tag": "div", "control": {"kind": "tablist", "group": "tg_1"},
                 "children": [
                     {"tag": "button", "interaction_id": "nc_1_3",
                      "control": {"kind": "tab", "label": "Overview", "disabled": False,
                                  "selected": True, "group": "tg_1"}},
                     {"tag": "button", "interaction_id": "nc_1_4",
                      "control": {"kind": "tab", "label": "Details", "disabled": False,
                                  "selected": False, "group": "tg_1"}},
                 ]},
                {"tag": "button", "interaction_id": "nc_1_5",
                 "control": {"kind": "toggle", "label": "Revenue series",
                             "disabled": False, "pressed": False}},
            ]},
        }
        assert app.state.native_content.publish(request_id, owner_key, initial)
        assert app.state.native_content.persist(request_id, owner_key)

        socket = AckSocket()
        registry = app.state.registry
        registry.clients["worker-native-action"] = SimpleNamespace(connection_enabled=True)
        registry.sockets["worker-native-action"] = socket
        registry.native_interaction_routes[request_id] = {
            "client_id": "worker-native-action",
            "websocket": socket,
            "expires_mono": time.monotonic() + 300,
        }

        headers = {"Origin": "http://testserver"}
        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        no_origin = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            json={"revision": 1, "interaction_id": "nc_1_1", "action": "set_checked", "value": True},
        )
        assert no_origin.status_code == 403

        response = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 1, "interaction_id": "nc_1_1", "action": "set_checked", "value": True},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "revision": 2, "persisted": True}
        assert socket.sent[-1]["type"] == "chat.native.interaction"
        assert socket.sent[-1]["request_id"] == request_id
        assert socket.sent[-1]["revision"] == 1
        updated = app.state.native_content.get(request_id, owner_key)
        assert updated["revision"] == 2
        assert updated["root"]["children"][0]["control"]["checked"] is True

        selected_tab = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 2, "interaction_id": "nc_1_4", "action": "set_selected", "value": True},
        )
        assert selected_tab.status_code == 200, selected_tab.text
        assert selected_tab.json() == {"ok": True, "revision": 3, "persisted": True}
        updated = app.state.native_content.get(request_id, owner_key)
        tabs = updated["root"]["children"][2]["children"]
        assert [tab["control"]["selected"] for tab in tabs] == [False, True]

        pressed_toggle = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 3, "interaction_id": "nc_1_5", "action": "set_pressed", "value": True},
        )
        assert pressed_toggle.status_code == 200, pressed_toggle.text
        assert pressed_toggle.json() == {"ok": True, "revision": 4, "persisted": True}
        updated = app.state.native_content.get(request_id, owner_key)
        assert updated["root"]["children"][3]["control"]["pressed"] is True

        stale = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 1, "interaction_id": "nc_1_2", "action": "set_value", "value": 50},
        )
        assert stale.status_code == 409
        invalid = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 4, "interaction_id": "nc_1_1", "action": "set_value", "value": 50},
        )
        assert invalid.status_code == 422
        assert len(socket.sent) == 3
        socket.recapture = {"version": 1, "revision": 5, "root": {"tag": "div", "children": [
            {"tag": "input", "interaction_id": "nc_5_1",
             "control": {"kind": "range", "label": "Opacity", "disabled": False,
                         "min": 0, "max": 100, "step": 5, "value": 50}},
            {"tag": "p", "text": "Updated dynamic content"},
        ]}}
        recaptured = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 4, "interaction_id": "nc_1_2", "action": "set_value", "value": 50},
        )
        assert recaptured.status_code == 200, recaptured.text
        assert recaptured.json() == {"ok": True, "revision": 5, "persisted": True, "recaptured": True}
        latest = client.get(f"/api/user/chat/requests/{request_id}/native-content")
        assert latest.status_code == 200, latest.text
        assert latest.json()["root"]["children"][1]["text"] == "Updated dynamic content"
        assert latest.json()["finality"] == "verified-final"
        proof = app.state.telemetry.get(request_id)["native_interaction_proof"]
        assert proof["base_revision"] == 1 and proof["revision"] == 5

        # An interaction may reveal a new original image which was not part
        # of the immutable terminal Markdown. Archive its actual bytes before
        # publishing revision 6; a second device can hydrate the same image.
        import base64, hashlib, io
        from PIL import Image
        image=Image.new("RGB",(8,8),(20,40,60))
        buffer=io.BytesIO()
        image.save(buffer,"PNG")
        png=buffer.getvalue()
        digest=hashlib.sha256(png).hexdigest()
        socket.recapture={"version":1,"revision":6,"root":{"tag":"div","children":[
            {"tag":"input","interaction_id":"nc_6_1",
             "control":{"kind":"range","label":"Opacity","disabled":False,"min":0,"max":100,"step":5,"value":75}},
            {"tag":"img","media_index":0,"source_sha256":digest,"intrinsic_width":8,"intrinsic_height":8},
        ]}}
        socket.media=[{"sha256":digest,"mime":"image/png","base64":base64.b64encode(png).decode()}]
        response=client.post(f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,json={"revision":5,"interaction_id":"nc_5_1","action":"set_value","value":75})
        assert response.status_code==200,response.text
        assert response.json()["persisted"]
        new=client.get(f"/api/user/chat/requests/{request_id}/native-content").json()
        assert new["revision"]==6 and new["finality"]=="verified-final"
        assert len(new["assets"])==1
        assert client.get(new["assets"][0]).content==png
        assert app.state.telemetry.get(request_id)["response_media"]==[]
        # A guessed digest or missing original must not replace the last
        # committed image. The original request archive remains readable.
        socket.recapture["revision"]=7
        socket.recapture["root"]["children"][0]["interaction_id"]="nc_7_1"
        socket.recapture["root"]["children"][1]["source_sha256"]="0"*64
        socket.media=[]
        invalid=client.post(f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,json={"revision":6,"interaction_id":"nc_6_1","action":"set_value","value":80})
        assert invalid.status_code==409
        assert client.get(f"/api/user/chat/requests/{request_id}/native-content").json()["revision"]==6

        client.cookies.clear()
        other = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-action-other@example.test", "password": "native-action-other-password"},
        )
        assert other.status_code == 200
        cross_owner = client.post(
            f"/api/user/chat/requests/{request_id}/native-content/interactions",
            headers=headers,
            json={"revision": 4, "interaction_id": "nc_1_2", "action": "set_value", "value": 50},
        )
        assert cross_owner.status_code == 404

