"""Verify real app.entry's middleware stack, not just a create_app fixture."""
import json
import subprocess
import sys
import os
import tempfile
from pathlib import Path

def test_production_entry_admin_request_list_acknowledges_sha_and_since():
    code = r'''
import json
from fastapi.testclient import TestClient
from app.entry import app
with TestClient(app) as client:
    login=client.post("/api/admin/auth/login", json={"username":"admin","password":"testFilterAdminStrong99!"})
    assert login.status_code==200,login.text
    path="/api/admin/requests?since=2026-11-01T03%3A00%3A38Z&current_version_only=true&limit=100"
    response=client.get(path)
    payload=response.json()
    print(json.dumps({"status":response.status_code,
        "applied_filters":payload.get("applied_filters"),
        "current_version_commit":payload.get("current_version_commit"),
        "total":payload.get("total"),
        "keys":sorted(payload.keys()),
        "routes":[{"name":getattr(route,"name",""),"module":getattr(getattr(route,"endpoint",None),"__module__",""),
                   "endpoint":getattr(getattr(route,"endpoint",None),"__qualname__","")}
                  for route in app.routes if getattr(route,"path","")=="/api/admin/requests"]},ensure_ascii=False))
'''
    env=os.environ.copy()
    env.update({
        "CHAT2API_API_KEY": "runtime-filter-master",
        "CHAT2API_PAIRING_CODE": "runtime-filter-pair",
        "CHAT2API_BUILD_COMMIT": "a"*40,
        "CHAT2API_ADMIN_PASSWORD": "testFilterAdminStrong99!",
        "CHAT2API_DATA_DIR": tempfile.mkdtemp(prefix="chat2api-filter-entry-"),
    })
    result=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                          env=env,text=True,capture_output=True,timeout=75)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
    data=json.loads(result.stdout.strip().splitlines()[-1])
    assert data["status"]==200,data
    assert data["applied_filters"] is not None, json.dumps(data,ensure_ascii=False)
    assert data["applied_filters"]["current_version_only"] is True,data
    assert data["applied_filters"]["since"]=="2026-11-01T03:00:38+00:00",data
    assert data["current_version_commit"]=="a"*40,data
