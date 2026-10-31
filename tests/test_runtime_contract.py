import json
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import app as package
from app.live_voice_patch import LIVE_PROTOCOL_VERSION
from app.runtime_contract import (
    ADMIN_EXTENSION_COLUMNS_ASSET,
    ADMIN_VERSION_ASSET,
    CHROME_BRIDGE_BUNDLE_VERSION,
    RELEASE_VERSION,
    CHROME_BRIDGE_VERSION,
    PACKAGE_VERSION,
    PRODUCTION_ENTRYPOINT,
    SERVER_RUNTIME_VERSION,
    install_runtime_contract,
    version_contract_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def test_python_package_versions_are_aligned():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert package.__version__ == PACKAGE_VERSION == pyproject["project"]["version"]


def test_chrome_bridge_contract_matches_manifest_bundle():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert RELEASE_VERSION == SERVER_RUNTIME_VERSION == CHROME_BRIDGE_VERSION == CHROME_BRIDGE_BUNDLE_VERSION
    assert RELEASE_VERSION == "0.22.259"
    assert manifest["version"] == RELEASE_VERSION


def test_v144_model_evidence_runtime_owners_are_in_manifest():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    main_scripts = manifest["content_scripts"][0]["js"]
    isolated_scripts = manifest["content_scripts"][1]["js"]
    assert main_scripts.index("network_stream_main_v55.js") < main_scripts.index("model_evidence_main_v144.js")
    assert "content_model_evidence_v144.js" in isolated_scripts


def test_v145_worker_model_library_runtime_owners_are_registered():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    isolated_scripts = manifest["content_scripts"][1]["js"]
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert "content_model_library_v145.js" in isolated_scripts
    assert entry.index('"conversation_route_recovery_v136.js"') < entry.index('"background_model_library_v145.js"')
    assert (ROOT / "app" / "worker_model_library_v145_patch.py").exists()
    server_entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_worker_model_library_v145_patch(app)" in server_entry
    assert server_entry.index("install_model_observability_v144_patch(app)") < server_entry.index(
        "install_worker_model_library_v145_patch(app)"
    )
    # A package containing the validator is not enough: production must
    # actually activate the Worker catalog and reset it on reconnect.


def test_realtime_protocol_contract_matches_live_bridge():
    assert LIVE_PROTOCOL_VERSION == "chat2api-live-v1"


def test_v148_release_contract_keeps_broker_terminal_authority():
    from pathlib import Path
    from app.runtime_contract import RELEASE_VERSION, CHROME_BRIDGE_BUNDLE_VERSION
    assert RELEASE_VERSION == "0.22.259"
    assert CHROME_BRIDGE_BUNDLE_VERSION == RELEASE_VERSION
    root = Path(__file__).resolve().parents[1]
    watchdog = (root / "app/request_stall_patch.py").read_text(encoding="utf-8")
    reaper = (root / "app/request_recovery_patch.py").read_text(encoding="utf-8")
    assert 'accepted = await broker.publish(' in watchdog
    assert 'await broker.release(state.request_id)' in watchdog
    assert 'await broker.release(str(state.request_id))' in reaper
    assert 'await broker.release(str(request_id))' in reaper


def test_v147_key_quota_replaces_per_key_concurrency_contract():
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["api_key_token_quota"] is True
    assert payload["features"]["api_key_concurrency_controls"] is False
    assert payload["features"]["server_side_same_api_fifo_v58"] is False
    assert payload["features"]["same_api_parallel_requests"] is True


def test_runtime_contract_payload_names_each_version_surface():
    runtime = FastAPI(version=SERVER_RUNTIME_VERSION)
    payload = version_contract_payload(runtime)
    assert payload["object"] == "chat2api.version"
    assert payload["server"]["package_version"] == PACKAGE_VERSION
    assert payload["server"]["runtime_version"] == SERVER_RUNTIME_VERSION
    assert payload["server"]["runtime_aligned"] is True
    assert payload["server"]["entrypoint"] == PRODUCTION_ENTRYPOINT
    assert payload["chrome_bridge"]["version"] == CHROME_BRIDGE_VERSION
    assert payload["chrome_bridge"]["bundle_version"] == CHROME_BRIDGE_BUNDLE_VERSION
    assert payload["chrome_bridge"]["persistent_window_pool_runtime_ready_revision"] == 146
    assert payload["features"]["persistent_window_runtime_preflight_v146"] is True
    assert payload["features"]["runtime_ready_standby_semantics_v146"] is True
    assert payload["protocols"]["realtime_voice"] == LIVE_PROTOCOL_VERSION


def test_runtime_contract_installs_once_and_after_latest_patch():
    runtime = FastAPI(version=SERVER_RUNTIME_VERSION)
    install_runtime_contract(runtime)
    install_runtime_contract(runtime)
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == "/version") == 1
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == ADMIN_VERSION_ASSET) == 1
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == ADMIN_EXTENSION_COLUMNS_ASSET) == 1

    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_runtime_contract(app)" in entry
    assert entry.index("install_v21_4_model_contract_patch(app)") < entry.index("install_v21_5_patch(app)")
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_runtime_contract(app)")


def test_runtime_contract_is_final_admin_version_owner():
    runtime = FastAPI(version="0.21.4")

    @runtime.get("/admin")
    async def admin_page():
        return HTMLResponse('<html><body><div class="brand"><small>Server Console · v0.21.4</small></div><div id="status">v0.20.0</div><script src="/assets/historical.js"></script></body></html>')

    @runtime.get("/developers")
    async def developers_page():
        return HTMLResponse('<html><body><div class="brand"><small>Server Console · v0.21.4</small></div></body></html>')

    @runtime.get("/api/admin/overview")
    async def overview():
        return {"version": "0.21.4", "online_extensions": 2}

    @runtime.get("/api/admin/extensions")
    async def extensions():
        return {"clients": [{"client_id": "ext_a", "version": CHROME_BRIDGE_VERSION}]}

    install_runtime_contract(runtime)
    assert runtime.version == SERVER_RUNTIME_VERSION

    client = TestClient(runtime)
    html = client.get("/admin").text
    assert f'<script src="{ADMIN_VERSION_ASSET}"></script>' in html
    assert f'<script src="{ADMIN_EXTENSION_COLUMNS_ASSET}?v=0.22.259"></script>' in html
    assert html.index('/assets/historical.js') < html.index(ADMIN_VERSION_ASSET) < html.index(ADMIN_EXTENSION_COLUMNS_ASSET)

    developers_html = client.get("/developers").text
    assert ADMIN_VERSION_ASSET in developers_html
    assert ADMIN_EXTENSION_COLUMNS_ASSET not in developers_html

    script = client.get(ADMIN_VERSION_ASSET).text
    assert f'const VERSION = "{SERVER_RUNTIME_VERSION}"' in script
    assert "__chat2apiRuntimeVersionOwner" in script
    assert 'document.querySelector(".brand small")' in script
    assert 'document.getElementById("status")' in script
    assert "MutationObserver" in script

    columns_script = client.get(ADMIN_EXTENSION_COLUMNS_ASSET).text
    assert 'const VERSION = "0.22.259-worker-list-v157"' in columns_script
    assert 'const COLUMN_SCHEMA_REVISION = 152' in columns_script
    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v3"' in columns_script
    assert 'const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2"' in columns_script
    assert '{key: "bound_api_keys", label: "绑定 API Key 数"}' not in columns_script
    assert '{key: "worker_settings", label: "并发设置"}' in columns_script
    assert '{key: "device_name", label: "设备名称"}' in columns_script
    assert '{key: "occupancy", label: "请求 / 备用窗口"}' in columns_script
    assert 'button.id = "extensionColumnSettingsButton"' in columns_script
    assert 'document.documentElement.dataset.chat2apiWorkerListSingleRenderer = "1"' in columns_script
    assert 'const REMOVED_KEYS = new Set(["concurrency", "reserve_windows", "bound_api_keys", "occupied_windows", "version", "network"])' in columns_script

    overview_payload = client.get("/api/admin/overview").json()
    assert overview_payload["version"] == SERVER_RUNTIME_VERSION

    extension_payload = client.get("/api/admin/extensions").json()
    assert extension_payload["clients"][0]["version"] == CHROME_BRIDGE_VERSION
    assert "version" not in extension_payload


def test_readme_describes_version_contract_and_voice_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Python package：`{PACKAGE_VERSION}`" in readme
    assert "Server runtime / console：`" in readme
    assert "Chrome Bridge：`" in readme
    assert "GET /version" in readme
    assert "完整版本规则见 `docs/VERSIONING.md`" in readme
    assert "语音生成、语音对话尚未实现" not in readme

def test_request_terminal_migration_owns_one_loaded_controller():
    manifest = json.loads((ROOT / "chrome_extension/manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.count("content_request_v6.js") == 1
    assert "content_request_stall_guard_v34.js" not in scripts
    assert "content_request_interruption_bridge_v72.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_guard.js" not in scripts
    bootstrap = (ROOT / "chrome_extension/content_bootstrap.js").read_text(encoding="utf-8")
    assert '"content_request_stall_guard_v34.js"' not in bootstrap
    assert '"content_guard.js"' not in bootstrap
    assert "content_rate_limit_guard_v52.js" in scripts
    quota = (ROOT / "chrome_extension/content_conversation_quota_failover_v95.js").read_text(encoding="utf-8")
    detector = (ROOT / "chrome_extension/content_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    assert "base.listener = listener" not in quota
    assert 'type: "chat.error"' not in detector
    request = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "resolveBlockingInterruption({force:true,phase:\"before-request\"})" in request
    assert 'type: "chat.completed"' in request
    assert "reconcileStrictTerminal" not in request


def test_v245_short_reply_is_owned_by_single_request_controller():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert 'reason:"fresh-roleless-short-answer-v245"' in source
    assert 'rendered.rolelessHeaderVerified ? 3 : 8' in source
    assert 'abortedRenderedCandidateSince >= abortedReplyStableThreshold(rendered)' in source
    assert source.count('type: "chat.completed", request_id: active.requestId') >= 1
    assert 'baselineRolelessHeadings' in source

def test_v246_roleless_rich_reply_preserves_one_terminal_controller():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] /
           "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert '"fresh-roleless-rich-answer-v246"' in src
    assert '"fresh-roleless-compact-div-v248"' in src
    assert "verifiedRolelessRichAbortCandidate(active, current)" in src
    assert "current.latest === current.rolelessHeading.nextElementSibling" in src
    assert "abortedRenderedCandidateSince >= abortedReplyStableThreshold(rendered)" in src
    assert 'response_terminal_source: rendered.rolelessRichVerified' in src

def test_v247_single_worker_terminal_and_file_archive_separation():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    worker = (root / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    backend = (root / "app/main.py").read_text(encoding="utf-8")
    assert "v247-same-request-roleless-rich-DIV-with-fresh-toolbar" in worker
    assert "media_file_ready_since_submit_ms" in worker
    assert "type: \"chat.completed\"" in worker
    assert "deferred_generated_files.rewrite(text, owner, request_id, base_url)" in backend
    assert "/api/generated-files/pending/{file_id}/{access_token}" in backend

def test_v248_compact_div_uses_original_single_terminal_controller():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "fresh-roleless-compact-div-v248" in source
    assert "shortRolelessSourceOverlap(raw, observed)" in source
    assert "verifiedRolelessRichAbortCandidate(active, current)" in source
    assert "abortedRenderedCandidateSince >= abortedReplyStableThreshold(rendered)" in source
    assert 'type: "chat.completed", request_id: active.requestId' in source

def test_v249_native_file_candidate_probe_is_bounded_and_not_a_terminal_owner():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main = (root / "chrome_extension/native_file_main_v210.js").read_text(encoding="utf-8")
    isolated = (root / "chrome_extension/content_native_file_v210.js").read_text(encoding="utf-8")
    controller = (root / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "candidateExclusionEvidence(scope)" in isolated
    assert "media_native_file_candidate_exclusion_counts:" in controller
    assert "source.slice(0,48)" not in isolated  # no source-text logging
    assert 'const nodes=[...scope.querySelectorAll(' in isolated
    assert ".slice(0,48)" in isolated
    assert 'type: "chat.completed"' not in main
    assert 'type: "chat.completed"' not in isolated

def test_v250_abcd_disconnect_is_presentation_recovery_not_worker_terminal():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "app/admin_playground_abcd_v197.js").read_text(encoding="utf-8")
    assert "recoverDisconnectedStream(requestId,update)" in source
    assert '"/outcome?full_text=1"' in source
    assert "response_text_truncated===true" in source
    assert "recovered_after_disconnect:out.recovered===true" in source
    assert "if(!stopped||!done)" in source
    assert "SSE 连接已断开，服务端仍在运行" in source


def test_v251_terminal_recovery_migration_keeps_single_request_controller():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text("utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.count("content_request_v6.js") == 1
    source = (ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    assert "function abortedReplyStableThreshold(rendered)" in source
    assert "return rendered?.shortTextVerified ? 4500 : 8000" in source
    # Externally visible terminal state must remain owned by request-v6,
    # not the native file observer, model inspector or read-only probes.
    for path in ("content_native_file_v210.js", "native_file_main_v210.js",
                 "content_model_evidence_v144.js", "model_evidence_main_v144.js"):
        observer = (ROOT / "chrome_extension" / path).read_text("utf-8")
        assert 'type: "chat.completed"' not in observer
        assert 'type: "chat.error"' not in observer


def test_v252_native_prompt_uses_one_request_v6_editor_writer():
    source = (ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    assert "async function setComposerTextBatched(active, element, text)" in source
    assert "await setComposerTextBatched(active, composer, prompt)" in source
    assert "await setComposerTextBatched(active, editor, active.prompt)" in source
    assert "Native editor batch cancelled or detached" in source
    assert "prompt_batched_native_verified:true" in source
    assert "function abortedReplyStableThreshold(rendered)" in source


def test_roleless_plain_fallback_v252_stays_request_v6_owned():
    source=(ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    assert "function verifiedPlainRolelessCaptureFallback(active, rendered, captured)" in source
    assert "coverage.independent_count>3" in source
    assert "coverage.independent_chars>60" in source
    assert "response_terminal_plain_roleless_fallback" in source
    assert "function abortedReplyStableThreshold(rendered)" in source


def test_file_type_evidence_is_observational_under_only_request_v6_terminal_owner():
    content=(ROOT/"chrome_extension"/"content_request_v6.js").read_text("utf-8")
    assert "function fileHandoffTypeInventory(required, captured)" in content
    assert "media_file_handoff_verified_types: fileHandoffInventory.verified" in content
    assert "media_native_file_unresolved_failures: nativeUnresolvedFailedTypes" in content
    assert "function abortedReplyStableThreshold(rendered)" in content


def test_ec46_long_prompt_rebind_is_single_write_owner():
    source = (ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    assert "prompt-native-verified-but-unbound" in source
    assert "Long prompt verified but not bound to active ChatGPT composer" in source
    assert "send_revalidation_revision: 254" in source
    assert "send_long_prompt_rewrite_blocked:" in source
    assert "&& active.prompt.length < 8192" in source


def test_compact_reply_serializer_recovery_preserves_single_v6_terminal_owner():
    source = (ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    assert "response_recovery_compact_plain_fallback:" in source
    assert "rendered.reason === \"fresh-roleless-compact-div-v248\"" in source
    assert "compact && !shortRolelessSourceOverlap(raw,active.networkObservedText)" in source
    assert "coverage.independent_chars>60" in source
    assert "function abortedReplyStableThreshold(rendered)" in source


def test_v255_native_zip_fallback_selects_only_verified_original_file_types():
    source = (ROOT / "chrome_extension" / "content_native_file_v210.js").read_text("utf-8")
    assert 'session.files.has("chat2api-artifacts.zip")' in source
    assert '["xlsx","pptx","json"].every(ext=>Boolean(byExt[ext]?.data_url))' in source
    assert 'synthesized_zip:session.synthesizedZip===true' in source
    assert 'type: "chat.completed"' not in source


def test_v253_probe_topology_has_server_ingress_contract():
    browser = (ROOT / "chrome_extension" / "content_probe_supervisor_v1.js").read_text("utf-8")
    server = (ROOT / "app" / "worker_probe_store.py").read_text("utf-8")
    for key in (
        "scene_uncovered_between_heading_toolbar_count",
        "scene_uncovered_shared_bounded_ancestor_count",
        "scene_uncovered_outside_candidate_ancestry_count",
    ):
        assert key in browser and key in server


def test_native_dom_evidence_migration_has_one_scoped_owner_without_terminal_authority():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text("utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.count("content_native_dom_evidence_v1.js") == 1
    assert scripts.index("content_native_content_v1.js") < scripts.index("content_native_dom_evidence_v1.js")
    assert scripts.index("content_native_dom_evidence_v1.js") < scripts.index("content_request_v6.js")
    request=(ROOT / "chrome_extension" / "content_request_v6.js").read_text("utf-8")
    evidence=(ROOT / "chrome_extension" / "content_native_dom_evidence_v1.js").read_text("utf-8")
    source=(ROOT / "chrome_extension" / "content_native_content_v1.js").read_text("utf-8")
    assert '__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.capture?.(' in request
    assert 'type:"chat.native.dom_evidence"' in evidence
    assert '__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.replyRoots?.(active)' in source
    for forbidden in ('chat.completed', 'chat.error', 'chat.cancelled', 'request-v6 terminal'):
        assert forbidden not in evidence
    server=(ROOT / "app" / "main.py").read_text("utf-8")
    assert 'message_type == "chat.native.dom_evidence"' in server
    assert "native_dom_evidence.save(request_id,owner,message.get(" in server
