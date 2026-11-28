"""Crash continuation through the actual conversation/lease/result SQLite store."""
import asyncio
import json
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from app import web_chat_plugin_recovery_v1 as recovery
from app import web_chat_conversations_v1 as conversations


def _fixture(tmp_path, monkeypatch):
    clock=[1000.0]
    monkeypatch.setattr(conversations.time,"time",lambda:clock[0])
    uid,cid,mid="alice","c2a_recovery_task_01","message_assistant_01"
    store=conversations.WebChatConversationStoreV1(tmp_path)
    conversation={"id":cid,"title":"resume","model":"gpt-6","apiKeyId":"owner-key",
        "createdAt":"2026-11-28T01:00:00Z","updatedAt":"2026-11-28T01:00:00Z",
        "messages":[{"id":"message_user_001","role":"user","content":"Inspect owner/repo #12",
                     "createdAt":"2026-11-28T01:00:00Z","requestId":"","pending":False,"recoveryStatus":"","lastEventId":0},
                    {"id":mid,"role":"assistant","content":"","createdAt":"2026-11-28T01:00:00Z",
                     "requestId":"","pending":True,"recoveryStatus":"tool_planning","lastEventId":0}]}
    assert store.upsert(uid,conversation,0)[0]
    key=(uid,cid,mid)
    assert store.plugin_task(*key,"claim","holder_first")["granted"]
    binding="a"*64
    checkpoint={"rounds":1,"tool_calls":1,"tool_events":[],
                "auto_resume":True,"resume_connections":{"github":binding},
                "plan":{"request_id":"req_first_plan_123","model":"gpt-6","start_tool_calls":0,
                        "calls":[{"name":"github.list_issues","arguments":{"repository":"owner/repo"}}]}}
    assert store.plugin_task(*key,"checkpoint","holder_first",checkpoint)["granted"]
    result_key=(*key,"holder_first",1,"github.list_issues",{"repository":"owner/repo"})
    assert store.plugin_result(*result_key)["state"]=="claimed"
    assert store.plugin_result(*result_key,{"status":200,"body":{"data":[{"number":12,"title":"Existing issue"}]},
                                            "connection_binding":binding})["state"]=="completed"
    clock[0]+=181
    monkeypatch.setattr(recovery,"_account",lambda app,user_id: {"user_id":uid,"enabled":True} if user_id==uid else None)
    async def identity(app,user,key_id=None):
        assert user["user_id"]==uid and key_id=="owner-key"
        return user,SimpleNamespace(key_id="owner-key")
    monkeypatch.setattr(recovery,"resolve_owned_web_chat_principal",identity)
    monkeypatch.setattr(recovery,"web_chat_model_decision",lambda *a:{"allowed":True})
    monkeypatch.setattr(recovery,"plugin_connection_binding",lambda *a:binding)
    monkeypatch.setattr(recovery,"_request",lambda *a:None)
    return clock,key,store,binding


def test_detached_scheduler_resumes_results_plans_and_finishes_conversation(tmp_path,monkeypatch):
    clock,key,store,binding=_fixture(tmp_path,monkeypatch)
    provider_calls,model_calls=[],[]
    async def provider(user,provider,tool,args):
        provider_calls.append((provider,tool,args))
        return JSONResponse({"data":[{"number":12,"title":"Confirmed","body":"Full issue body"}]})
    async def adapter(body,request,client,rid,principal):
        assert principal.key_id=="owner-key"
        model_calls.append(rid)
        prompt=json.dumps(body.model_dump(),ensure_ascii=False)
        if "req_plugin_plan_" in rid:
            if len(model_calls)==1:
                assert "Existing issue" in prompt
                result='{"tool_calls":[{"name":"github.get_issue","arguments":{"repository":"owner/repo","issue_number":12}}]}'
            else:
                assert "Full issue body" in prompt
                result='{"tool_calls":[]}'
        else:
            assert "Full issue body" in prompt
            result="Verified issue #12."
        return JSONResponse({"choices":[{"message":{"content":result}}]})
    state=SimpleNamespace(web_chat_conversations=store,plugin_oauth_store=object(),
        execute_plugin_provider_tool=provider,chat_completions_adapter=adapter,
        telemetry=SimpleNamespace(get=lambda rid:None,get_many_persisted=lambda ids:{}))
    app=SimpleNamespace(state=state)
    assert store.expired_plugin_candidates()==[key]
    assert asyncio.run(recovery.recover_one(app,key))
    assert provider_calls==[("github","get_issue",{"repository":"owner/repo","issue_number":12})]
    assert len(model_calls)==3 and len(set(model_calls))==3
    saved=store.get_for_user(*key[:2])
    answer=saved["messages"][-1]
    assert answer["content"]=="Verified issue #12." and not answer["pending"]
    assert answer["requestId"]==model_calls[-1]
    assert answer["recoveryStatus"]=="completed"
    assert store.plugin_task(*key,"status")["state"]=="completed"
    assert not asyncio.run(recovery.recover_one(app,key))


def test_reserved_original_model_request_is_never_blindly_replayed(tmp_path,monkeypatch):
    clock,key,store,binding=_fixture(tmp_path,monkeypatch)
    assert store.plugin_task(*key,"takeover","srv_first")["granted"]
    prior=store.reserve_plugin_planner(*key,"srv_first",2)
    assert prior["new"]
    clock[0]+=181
    async def prohibited(*args):
        raise AssertionError("uncertain original model request must not be dispatched")
    app=SimpleNamespace(state=SimpleNamespace(web_chat_conversations=store,plugin_oauth_store=object(),
        execute_plugin_provider_tool=prohibited,chat_completions_adapter=prohibited,
        telemetry=SimpleNamespace(get=lambda rid:None,get_many_persisted=lambda ids:{})))
    assert not asyncio.run(recovery.recover_one(app,key))
    answer=store.get_for_user(*key[:2])["messages"][-1]
    assert not answer["pending"] and answer["recoveryStatus"]=="error"


def test_recovery_planner_rejects_unproven_resources_and_writes():
    allowed={"github":"a"*64,"gmail":"b"*64}
    assert not recovery._allowed("github.get_issue",{"repository":"other/private","issue_number":12},
                                 "owner/repo #12",[],allowed)
    assert not recovery._allowed("gmail.get_message",{"message_id":"guessed"},"read email",[],allowed)
    assert not recovery._allowed("github.create_issue",{"repository":"owner/repo"},"owner/repo",[],allowed)
    assert recovery._allowed("github.get_issue",{"repository":"owner/repo","issue_number":12},
                             "owner/repo #12",[],allowed)
