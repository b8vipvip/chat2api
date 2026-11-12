"""W-04 recovery contract: owner-scoped snapshots and no duplicate dispatch."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_recovery_api import install_web_chat_recovery_api


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_JS = ROOT / "app" / "chat_stream_recovery_v1.js"


def make_app(data_dir: Path):
    app = create_app(Settings(CHAT2API_API_KEY="w04-test-key", CHAT2API_DATA_DIR=str(data_dir)))
    install_user_console_v104_patch(app)
    install_web_chat_recovery_api(app)
    install_web_chat_recovery_api(app)
    return app


def register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/user/register",
        json={"email": email, "password": "secure-password-123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]["user_id"]


def remember(app, request_id: str, user_key: str, **changes):
    app.state.telemetry._remember({
        "request_id": request_id,
        "api_key_id": user_key,
        "status": "running",
        "updated_at": "2026-11-12T08:00:00+00:00",
        **changes,
    })


def test_completed_snapshot_is_returned_only_to_key_owner_and_never_cached(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_12345678"
    with TestClient(app) as alice, TestClient(app) as bob:
        alice_id = register(alice, "alice@example.org")
        bob_id = register(bob, "bob@example.org")
        app.state.user_accounts.attach_key(alice_id, "key_alice")
        app.state.user_accounts.attach_key(bob_id, "key_bob")
        remember(
            app, request_id, "key_alice", status="completed",
            response_text="Final assistant answer",
            response_text_available=True,
        )

        response = alice.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert response.json()["terminal"] is True
        assert response.json()["response_text"] == "Final assistant answer"
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in response.headers["vary"].split(",")}

        denied = bob.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in denied.headers["vary"].split(",")}



def test_cancel_signal_is_owner_scoped_idempotent_and_not_a_terminal_owner(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_cancel123"
    sent = []

    async def fake_send(client_id, payload):
        sent.append((client_id, dict(payload)))

    with TestClient(app) as alice, TestClient(app) as bob:
        alice_id = register(alice, "cancel-alice@example.org")
        bob_id = register(bob, "cancel-bob@example.org")
        app.state.user_accounts.attach_key(alice_id, "key_cancel_alice")
        app.state.user_accounts.attach_key(bob_id, "key_cancel_bob")
        remember(app, request_id, "key_cancel_alice")
        state = SimpleNamespace(
            client_id="ext_worker123",
            terminal_event_type=None,
            diagnostics={},
        )
        app.state.broker.requests[request_id] = state
        app.state.registry.send = fake_send
        path = f"/api/user/chat/requests/{request_id}/cancel"

        missing_origin = alice.post(path)
        assert missing_origin.status_code == 403
        assert missing_origin.headers["cache-control"] == "private, no-store"

        denied = bob.post(path, headers={"Origin": "http://testserver"})
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"
        assert sent == []

        cancelled = alice.post(path, headers={"Origin": "http://testserver"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancel_requested"] is True
        assert cancelled.json()["terminal"] is False
        assert cancelled.json()["active"] is True
        assert cancelled.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in cancelled.headers["vary"].split(",")}
        assert sent == [("ext_worker123", {"type": "chat.cancel", "request_id": request_id})]
        assert state.terminal_event_type is None

        repeated = alice.post(path, headers={"Origin": "http://testserver"})
        assert repeated.status_code == 200
        assert repeated.json()["already_requested"] is True
        assert len(sent) == 1
        assert state.terminal_event_type is None


def test_terminal_request_cancel_is_idempotent_and_never_signals_worker(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_done1234"
    with TestClient(app) as client:
        user_id = register(client, "cancel-terminal@example.org")
        app.state.user_accounts.attach_key(user_id, "key_cancel_terminal")
        remember(
            app, request_id, "key_cancel_terminal", status="completed",
            response_text="final", response_text_available=True,
        )
        sent = []

        async def fake_send(client_id, payload):
            sent.append((client_id, payload))

        app.state.registry.send = fake_send
        response = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert response.json()["terminal"] is True
        assert response.json()["cancel_requested"] is False
        assert sent == []


def test_cancel_delivery_failure_does_not_leave_false_idempotency_marker(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_cancelfail"
    with TestClient(app) as client:
        user_id = register(client, "cancel-failure@example.org")
        app.state.user_accounts.attach_key(user_id, "key_cancel_failure")
        remember(app, request_id, "key_cancel_failure")
        state = SimpleNamespace(
            client_id="ext_worker123",
            terminal_event_type=None,
            diagnostics={},
        )
        app.state.broker.requests[request_id] = state

        async def fail_send(client_id, payload):
            raise RuntimeError("worker offline")

        app.state.registry.send = fail_send
        response = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 409
        assert response.headers["cache-control"] == "private, no-store"
        assert "web_chat_cancel_requested" not in state.diagnostics

def test_running_request_is_pollable_but_stale_running_state_is_not_replayed(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_abcdefgh"
    with TestClient(app) as client:
        user_id = register(client, "running@example.org")
        app.state.user_accounts.attach_key(user_id, "key_running")
        remember(app, request_id, "key_running")
        app.state.broker.requests[request_id] = SimpleNamespace(terminal_event_type=None)
        active = client.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert active.status_code == 200
        assert active.json()["recoverable"] is True
        assert active.json()["active"] is True
        assert active.json()["phase"] == "generating"
        assert active.json()["response_text"] == ""
        assert active.json()["response_text_available"] is False
        del app.state.broker.requests[request_id]

        stale = client.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert stale.status_code == 200
        assert stale.json()["status"] == "unknown"
        assert stale.json()["recoverable"] is False
        assert stale.json()["response_text"] == ""


def test_queued_admission_and_failed_partial_text_are_not_misreported(tmp_path):
    app = make_app(tmp_path)
    queued_id, failed_id = "req_queued123", "req_failed123"
    with TestClient(app) as client:
        user_id = register(client, "states@example.org")
        app.state.user_accounts.attach_key(user_id, "key_states")
        remember(app, queued_id, "key_states", status="queued")
        queued = client.get(f"/api/user/chat/requests/{queued_id}/recovery").json()
        assert queued["recoverable"] is True and queued["phase"] == "admission"
        assert queued["response_text"] == ""
        assert queued["response_text_available"] is False

        remember(
            app, failed_id, "key_states", status="stalled",
            response_text="",
            response_text_available=False,
            response_text_provisional="unverified partial output",
        )
        failed = client.get(f"/api/user/chat/requests/{failed_id}/recovery").json()
        assert failed["terminal"] is True
        assert failed["status"] == "stalled"
        assert failed["response_text"] == ""
        assert failed["response_text_available"] is False



def test_recovery_respects_bounded_telemetry_retention_window(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_oldrecord1"
    with TestClient(app) as client:
        user_id = register(client, "retention@example.org")
        app.state.user_accounts.attach_key(user_id, "key_retention")
        remember(
            app, request_id, "key_retention", status="completed",
            response_text="old final answer", response_text_available=True,
        )
        for index in range(500):
            remember(
                app, f"req_new{index:08d}", "key_retention",
                status="completed", response_text=f"answer {index}",
                response_text_available=True,
            )
        assert client.get(
            f"/api/user/chat/requests/{request_id}/recovery"
        ).status_code == 404

def test_unknown_and_malformed_ids_are_not_disclosed(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        register(client, "unknown@example.org")
        unknown = client.get("/api/user/chat/requests/req_12345678/recovery")
        malformed = client.get("/api/user/chat/requests/not-a-request/recovery")
        assert unknown.status_code == 404
        assert malformed.status_code == 404
        assert unknown.headers["cache-control"] == "private, no-store"
        assert malformed.headers["cache-control"] == "private, no-store"
    with TestClient(app) as anonymous:
        unauthorized = anonymous.get("/api/user/chat/requests/req_12345678/recovery")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "private, no-store"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the recovery state-machine harness")
def test_client_polls_same_request_with_get_and_never_redispatches():
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const src = fs.readFileSync(process.argv[1], "utf8");
const window = {};
const calls = [];
const updates = [];
const replies = [
  {status:200, ok:true, json:async()=>({
    request_id:"req_12345678", status:"running", terminal:false,
    recoverable:true, active:true, phase:"generating", retry_after_ms:250
  })},
  {status:200, ok:true, json:async()=>({
    request_id:"req_12345678", status:"completed", terminal:true,
    recoverable:false, active:false, response_text_available:true,
    response_text:"final answer"
  })}
];
const fetcher = async (url, options) => {
  calls.push({url, method:options.method, credentials:options.credentials});
  return replies.shift();
};
vm.runInNewContext(src, {
  window, AbortController, Date, Promise, Math,
  setTimeout:fn=>{fn();return 1;}, clearTimeout:()=>{}
});
const api = window.Chat2APIStreamRecoveryV1;
assert(api && api.isRequestId("req_12345678"));
assert(!api.isRequestId("bad"));
assert(/^req_[A-Za-z0-9_-]{8,120}$/.test(api.createRequestId({randomUUID:()=>"12345678-1234-1234-1234-123456789012"})));
(async()=>{
  const result = await api.create({
    fetcher, onUpdate:value=>updates.push(value), pollIntervalMs:250
  }).resume({request_id:"req_12345678", assistant_message_id:"message_123456"});
  assert.strictEqual(result.status, "completed");
  assert.strictEqual(result.response_text, "final answer");
  assert.strictEqual(calls.length, 2);
  assert(calls.every(call=>call.method==="GET"));
  assert(calls.every(call=>call.credentials==="same-origin"));
  assert(calls.every(call=>call.url==="/api/user/chat/requests/req_12345678/recovery"));
  assert(updates.some(value=>value.status==="running"));
  assert(updates.some(value=>value.status==="completed"));
  const inconsistent = await api.create({
    fetcher:async()=>({status:200,ok:true,json:async()=>({
      request_id:"req_12345678", status:"completed", terminal:false,
      recoverable:false, active:false, response_text_available:true,
      response_text:"must not be accepted"
    })})
  }).resume({request_id:"req_12345678", assistant_message_id:"message_123456"});
  assert.strictEqual(inconsistent.status, "invalid_response");

  const cancelCalls = [];
  const cancelController = api.create({
    fetcher:async(url, options)=>{
      cancelCalls.push({url,method:options.method,credentials:options.credentials});
      return {status:200,ok:true,json:async()=>({
        request_id:"req_12345678", status:"cancellation_requested",
        terminal:false, active:true, cancel_requested:true
      })};
    }
  });
  const cancelResult = await cancelController.cancel({
    request_id:"req_12345678", assistant_message_id:"message_123456"
  });
  assert.strictEqual(cancelResult.status, "cancellation_requested");
  assert.strictEqual(cancelResult.cancel_requested, true);
  assert.strictEqual(cancelCalls.length, 1);
  assert.strictEqual(cancelCalls[0].method, "POST");
  assert.strictEqual(cancelCalls[0].credentials, "same-origin");
  assert.strictEqual(cancelCalls[0].url, "/api/user/chat/requests/req_12345678/cancel");
  assert(!cancelCalls.some(call=>call.url.includes("/v1/chat/completions")));

  const unavailable = await api.create({
    fetcher:async()=>({status:409,ok:false})
  }).cancel({request_id:"req_12345678",assistant_message_id:"message_123456"});
  assert.strictEqual(unavailable.status, "cancel_unavailable");

  const finalizing = await api.create({
    fetcher:async()=>({status:200,ok:true,json:async()=>({
      request_id:"req_12345678", status:"finalizing",
      terminal:false, active:true, cancel_requested:false,
      already_terminal:true
    })})
  }).cancel({request_id:"req_12345678",assistant_message_id:"message_123456"});
  assert.strictEqual(finalizing.status, "finalizing");
  assert.strictEqual(finalizing.terminal, false);
  console.log("recovery-harness-passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(RECOVERY_JS)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recovery-harness-passed" in result.stdout
