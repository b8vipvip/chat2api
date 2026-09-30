from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    data = p.read_text(encoding="utf-8")
    count = data.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement target, found {count}")
    p.write_text(data.replace(old, new, 1), encoding="utf-8")


pool_path = "chrome_extension/conversation_persistent_pool_v132.js"
login_path = "chrome_extension/background_login_v27.js"
test_path = "tests/test_persistent_window_pool_v132.py"

replace_once(
    pool_path,
    '  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";\n  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";\n  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";',
    '  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";\n  const INIT_TAB_KEY = "chat2apiInitializationTabIdV32";\n  const LOGIN_PROBE_TAB_KEY = "chatgptLoginProbeTabId";\n  const LOGIN_PROBE_WINDOW_KEY = "chatgptLoginProbeWindowId";\n  const LOGIN_PROBE_ADOPTABLE_KEY = "chatgptLoginProbeAdoptable";\n  const ROUTE_ALARM_PREFIX = "chat2api-route-close:";',
)

bootstrap_helpers = '''  async function trackLoginProbe(row, adoptable = true) {
    if (!Number.isInteger(row?.window_id) || !Number.isInteger(row?.tab_id)) return;
    await chrome.storage.local.set({
      [LOGIN_PROBE_TAB_KEY]: row.tab_id,
      [LOGIN_PROBE_WINDOW_KEY]: row.window_id,
      [LOGIN_PROBE_ADOPTABLE_KEY]: adoptable === true,
    }).catch(() => {});
  }

  async function createBootstrapSurface(reason = "login-bootstrap", { focused = false, userVisible = false } = {}) {
    const createWindow = typeof globalThis.chat2apiCreateWindowStaggered === "function"
      ? globalThis.chat2apiCreateWindowStaggered
      : chrome.windows.create.bind(chrome.windows);
    const created = await createWindow(
      { url: NEW_CHAT_URL, focused: Boolean(focused), type: "normal" },
      { reason: `persistent-window-pool-v132:login-bootstrap:${reason}` },
    );
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create the ChatGPT login bootstrap window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw new Error("ChatGPT login bootstrap window contains no usable tab");
    }
    const url = tab.url || tab.pendingUrl || NEW_CHAT_URL;
    const row = {
      window_id: created.id,
      tab_id: tab.id,
      url,
      status: String(tab.status || ""),
      routable: isChatGpt(url) && !isAuthSurface(url),
      runtime_ready: false,
      focused: Boolean(focused),
    };
    await trackLoginProbe(row, !userVisible && !focused);
    state.created += 1;
    return row;
  }

  async function ensureLoginSurface({ focused = false, userVisible = false, reason = "login-surface" } = {}) {
    await ensureLoaded();
    const rows = await physicalWindows();
    let row = rows[0] || null;
    const existing = Boolean(row);
    if (!row) {
      row = await createBootstrapSurface(reason, { focused, userVisible });
    } else {
      await trackLoginProbe(row, !userVisible && !focused);
      if (focused || userVisible) {
        try { await chrome.windows.update(row.window_id, { focused: true }); } catch (_) {}
        try { await chrome.tabs.update(row.tab_id, { active: true }); } catch (_) {}
      }
    }
    return { ...row, existing };
  }

'''
replace_once(
    pool_path,
    '  async function createStandby(reason = "persistent-pool-warm") {',
    bootstrap_helpers + '  async function createStandby(reason = "persistent-pool-warm") {',
)

old_reconcile = '''    let opened = 0;
    let lastError = "";
    if (ready && !isDisabled) {
      while (standbyRows(rows, value).length < target) {
        try {
          const row = await createStandby(reason);
          rows.push(row);
          opened += 1;
        } catch (error) {
          lastError = String(error?.message || error);
          break;
        }
      }
    }

    const currentStandby = standbyRows(rows, value).length;
    const pendingReason = isDisabled
      ? "worker_disabled"
      : (auth.explicit_logout
          ? "login_required"
          : (!ready
              ? "login_not_ready"
              : (reduced.deferred ? "protected_standby" : (lastError ? "warm_failed" : ""))));'''
new_reconcile = '''    let opened = 0;
    let lastError = "";
    let bootstrapOpened = false;

    // Break the zero-window login deadlock with exactly one pool-owned ChatGPT
    // surface. A later reconcile may warm the remaining target only after the
    // login detector confirms ready+composer on this bootstrap surface.
    if (!isDisabled && rows.length === 0) {
      try {
        const bootstrap = await ensureLoginSurface({
          focused: false,
          userVisible: false,
          reason: `reconcile:${reason}`,
        });
        rows.push({ ...bootstrap });
        opened += bootstrap.existing ? 0 : 1;
        bootstrapOpened = true;
      } catch (error) {
        lastError = String(error?.message || error);
      }
    }

    if (bootstrapOpened) {
      state.lastResult = {
        ok: true,
        reason,
        target,
        standby_target: standbyTarget,
        standby_before_or_after_total: standbyRows(rows, value).length,
        total_windows: rows.length,
        routed_windows: routeByWindow(value).size,
        standby_semantics_revision: STANDBY_SEMANTICS_REVISION,
        transient_login_state_preserves_standby_target: true,
        login_state: auth.state,
        login_composer_ready: auth.composer_ready,
        login_ready: ready,
        login_explicit_logout: auth.explicit_logout,
        runtime_validation: runtimeValidation,
        opened,
        closed: reduced.closed,
        deferred: reduced.deferred,
        pending_reason: "login_bootstrap",
        error: "",
        at: Date.now(),
      };
      scheduleReconcile("login-bootstrap-follow-up", 1200);
      return snapshotFrom(rows, value, auth, isDisabled);
    }

    if (ready && !isDisabled && !lastError) {
      while (standbyRows(rows, value).length < target) {
        try {
          const row = await createStandby(reason);
          rows.push(row);
          opened += 1;
        } catch (error) {
          lastError = String(error?.message || error);
          break;
        }
      }
    }

    const currentStandby = standbyRows(rows, value).length;
    const pendingReason = isDisabled
      ? "worker_disabled"
      : (auth.explicit_logout
          ? "login_required"
          : (lastError
              ? "window_open_failed"
              : (!ready
                  ? "login_not_ready"
                  : (reduced.deferred ? "protected_standby" : ""))));'''
replace_once(pool_path, old_reconcile, new_reconcile)

replace_once(
    pool_path,
    '      transient_login_state_preserves_standby_target: true,\n      online_integrity_interval_ms: ONLINE_INTEGRITY_INTERVAL_MS,',
    '      transient_login_state_preserves_standby_target: true,\n      login_bootstrap_single_surface: true,\n      login_bootstrap_authority: "persistent-window-pool-v132",\n      online_integrity_interval_ms: ONLINE_INTEGRITY_INTERVAL_MS,',
)
replace_once(
    pool_path,
    '  state.loginStatus = loginStatus;\n  state.workerOnlineEligibility = workerOnlineEligibility;',
    '  state.loginStatus = loginStatus;\n  state.ensureLoginSurface = ensureLoginSurface;\n  state.workerOnlineEligibility = workerOnlineEligibility;',
)

replace_once(
    login_path,
    '  const WARM_POOL_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";\n  const CACHE_KEY = "chatgptLoginReadinessV27";',
    '  const WARM_POOL_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";\n  const PERSISTENT_POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";\n  const CACHE_KEY = "chatgptLoginReadinessV27";',
)
replace_once(login_path, '  const PROBE_URL = "https://chatgpt.com/";\n', '')

old_retire = '''  async function retireAutomaticProbeIfReady(snapshot) {
    if (snapshot?.state !== "ready" || snapshot?.composer_ready !== true) return snapshot;
    const probe = await trackedProbe();
    if (!probe?.adoptable || probe.tab_id !== snapshot.tab_id) return snapshot;

    state.suppressedRemovedTabs.add(probe.tab_id);
    await clearTrackedProbe();
    const stable = await persist({
      ...snapshot,
      strategy: `${snapshot.strategy}+startup-readiness-confirmed`,
      tab_id: null,
      window_id: null,
      checked_at_ms: Date.now(),
    });
    try { await chrome.windows.remove(probe.window_id); } catch (_) {}
    return stable;
  }'''
new_retire = '''  async function retireAutomaticProbeIfReady(snapshot) {
    if (snapshot?.state !== "ready" || snapshot?.composer_ready !== true) return snapshot;
    const probe = await trackedProbe();
    if (!probe?.adoptable || probe.tab_id !== snapshot.tab_id) return snapshot;

    // Persistent Window Pool v132 owns this physical surface. Keep it alive so
    // reconcile can validate and adopt it as standby #1 instead of reopening it.
    await chrome.storage.local.set({ chatgptLoginProbeAdoptable: false }).catch(() => {});
    return persist({
      ...snapshot,
      strategy: `${snapshot.strategy}+persistent-pool-bootstrap-adopted`,
      checked_at_ms: Date.now(),
    });
  }'''
replace_once(login_path, old_retire, new_retire)

old_probe_create = '''    const created = await chrome.windows.create({ url: PROBE_URL, focused: Boolean(focused), type: "normal" });
    if (!Number.isInteger(created?.id)) throw new Error("Chrome did not create the ChatGPT login window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item?.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item?.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT login window contains no usable tab");

    await chrome.storage.local.set({
      chatgptLoginProbeTabId: tab.id,
      chatgptLoginProbeWindowId: created.id,
      chatgptLoginProbeAdoptable: !userVisible && !focused,
    }).catch(() => {});
    state.bootValidated = false;
    await persist({
      state: "checking",
      confidence: "low",
      strategy: userVisible ? "manual-login-window-created" : "startup-readiness-window-created",
      composer_ready: false,
      document_ready: false,
      checked_at_ms: Date.now(),
      tab_id: tab.id,
      window_id: created.id,
    });
    return { tab, tab_id: tab.id, window_id: created.id, adoptable: !userVisible && !focused, existing: false };'''
new_probe_create = '''    let pool = globalThis[PERSISTENT_POOL_KEY];
    for (let attempt = 0; attempt < 40 && typeof pool?.ensureLoginSurface !== "function"; attempt += 1) {
      await sleep(50);
      pool = globalThis[PERSISTENT_POOL_KEY];
    }
    if (typeof pool?.ensureLoginSurface !== "function") {
      throw new Error("Persistent window pool authority is unavailable for ChatGPT login bootstrap");
    }

    const surface = await pool.ensureLoginSurface({
      focused: Boolean(focused),
      userVisible: Boolean(userVisible),
      reason: userVisible ? "manual-login" : "startup-readiness",
    });
    if (!Number.isInteger(surface?.window_id) || !Number.isInteger(surface?.tab_id)) {
      throw new Error("Persistent window pool returned no usable ChatGPT login surface");
    }
    const tab = await chrome.tabs.get(surface.tab_id).catch(() => null);
    if (!tab?.id) throw new Error("The ChatGPT login window contains no usable tab");

    await chrome.storage.local.set({
      chatgptLoginProbeTabId: tab.id,
      chatgptLoginProbeWindowId: surface.window_id,
      chatgptLoginProbeAdoptable: !userVisible && !focused,
    }).catch(() => {});
    state.bootValidated = false;
    await persist({
      state: "checking",
      confidence: "low",
      strategy: userVisible ? "manual-login-window-created" : "startup-readiness-window-created",
      composer_ready: false,
      document_ready: false,
      checked_at_ms: Date.now(),
      tab_id: tab.id,
      window_id: surface.window_id,
    });
    return {
      tab,
      tab_id: tab.id,
      window_id: surface.window_id,
      adoptable: !userVisible && !focused,
      existing: surface.existing === true,
    };'''
replace_once(login_path, old_probe_create, new_probe_create)

tests = Path(test_path).read_text(encoding="utf-8")
marker = "def test_zero_window_worker_bootstraps_one_pool_owned_login_surface() -> None:"
if marker in tests:
    raise SystemExit("bootstrap regression tests already present unexpectedly")
tests += '''\n\n\ndef test_zero_window_worker_bootstraps_one_pool_owned_login_surface() -> None:\n    source = text("chrome_extension/conversation_persistent_pool_v132.js")\n    assert 'const LOGIN_PROBE_TAB_KEY = "chatgptLoginProbeTabId"' in source\n    assert 'async function createBootstrapSurface(' in source\n    assert 'async function ensureLoginSurface(' in source\n    assert 'if (!isDisabled && rows.length === 0)' in source\n    assert 'pending_reason: "login_bootstrap"' in source\n    assert 'scheduleReconcile("login-bootstrap-follow-up", 1200)' in source\n    assert 'login_bootstrap_single_surface: true' in source\n    assert 'login_bootstrap_authority: "persistent-window-pool-v132"' in source\n    assert 'state.ensureLoginSurface = ensureLoginSurface;' in source\n\n\ndef test_login_detector_delegates_bootstrap_window_lifecycle_to_persistent_pool() -> None:\n    login = text("chrome_extension/background_login_v27.js")\n    assert 'const PERSISTENT_POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__"' in login\n    assert 'pool.ensureLoginSurface({' in login\n    assert 'persistent-pool-bootstrap-adopted' in login\n    assert 'chrome.windows.create' not in login\n    assert 'chrome.windows.remove' not in login\n\n\ndef test_bootstrap_is_adopted_before_remaining_standby_windows_are_warmed() -> None:\n    source = text("chrome_extension/conversation_persistent_pool_v132.js")\n    bootstrap = source.index('if (!isDisabled && rows.length === 0)')\n    early_return = source.index('return snapshotFrom(rows, value, auth, isDisabled);', bootstrap)\n    warm = source.index('while (standbyRows(rows, value).length < target)', early_return)\n    assert bootstrap < early_return < warm\n    assert 'if (ready && !isDisabled && !lastError)' in source\n'''
Path(test_path).write_text(tests, encoding="utf-8")

version_files = [
    ".github/workflows/production-image-smoke.yml",
    "app/admin_extension_columns.js",
    "app/runtime_contract.py",
    "app/worker_limits_clipboard_v121_patch.py",
    "chrome_extension/background_runtime_preflight_v48.js",
    "chrome_extension/content_bundle_marker_v48.js",
    "chrome_extension/content_bundle_marker_v71.js",
    "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/content_runtime_contract_v71.js",
    "chrome_extension/manifest.json",
    "tests/runtime_preflight_refresh_v71.mjs",
    "tests/test_admin_ui_polish_v138_release_v02288.py",
    "tests/test_bootstrap_container_packaging_v22_4.py",
    "tests/test_capacity_queue_v57.py",
    "tests/test_capacity_runtime_v22_24.py",
    "tests/test_extension_column_layout_v21_7.py",
    "tests/test_generation_backend_health_v54.py",
    "tests/test_linux_worker_bootstrap_runtime_recovery_v22_7.py",
    "tests/test_linux_worker_bridge_binding_v22_3.py",
    "tests/test_linux_worker_bridge_central_sync_v36.py",
    "tests/test_linux_worker_extension_autoload_v22_16.py",
    "tests/test_linux_worker_install_lifecycle_v22_5.py",
    "tests/test_linux_worker_isolated_manager_v126.py",
    "tests/test_linux_worker_login_diagnostics_v22_22.py",
    "tests/test_linux_worker_online_upgrade_v22_25.py",
    "tests/test_linux_worker_pairing_ui_v22_18.py",
    "tests/test_linux_worker_proxy_control_v22_1.py",
    "tests/test_linux_worker_proxy_login_gate_v22_8.py",
    "tests/test_linux_worker_remote_login_v22_2.py",
    "tests/test_linux_worker_repair_command_v22_21.py",
    "tests/test_linux_worker_ui_hygiene_v22_15.py",
    "tests/test_login_readiness_v27.py",
    "tests/test_login_ready_release_v02287.py",
    "tests/test_model_evidence_v144.py",
    "tests/test_multimodal_main_world_v78.py",
    "tests/test_network_stream_progress_v54.py",
    "tests/test_persistent_window_pool_release_v02285_v0836.py",
    "tests/test_persistent_window_pool_v132.py",
    "tests/test_qnbot_recovery_release_v02286_v0837.py",
    "tests/test_rate_limit_window_guard_v52.py",
    "tests/test_release_proxy_health_v22_38.py",
    "tests/test_release_v02246_contract.py",
    "tests/test_request_hygiene_worker_toggle_v46.py",
    "tests/test_request_terminal_windows_worker_v022101.py",
    "tests/test_reserve_window_telemetry_v29.py",
    "tests/test_response_capture_v41.py",
    "tests/test_response_progress_v22_34.py",
    "tests/test_response_stream_tool_isolation_v48.py",
    "tests/test_responses_protocol_integrity_v110.py",
    "tests/test_responses_tool_stream_window_cap_v111.py",
    "tests/test_route_recovery_release_v02290_v0839.py",
    "tests/test_runtime_contract.py",
    "tests/test_runtime_lifecycle_multimodal_v79.py",
    "tests/test_server_worker_sync_v22_36.py",
    "tests/test_single_response_observer_v53.py",
    "tests/test_single_window_authority_release_v02293_v0842.py",
    "tests/test_user_console_v104.py",
    "tests/test_v02249_linux_response_lifecycle.py",
    "tests/test_v02250_attachment_window_telemetry.py",
    "tests/test_v02251_physical_window_truth.py",
    "tests/test_v02252_multimodal_upload_ready_gate.py",
    "tests/test_v02253_vision_affinity_window_stagger.py",
    "tests/test_v02254_worker_lifecycle_affinity_prompt_v86.py",
    "tests/test_v02273_strict_api_fifo.py",
    "tests/test_worker_lifecycle_retry_quarantine_v50.py",
    "tests/test_worker_runtime_refresh_v71.py",
    "tests/test_worker_runtime_standby_release_v02298.py",
]

changed = []
for name in version_files:
    path = Path(name)
    if not path.exists():
        raise SystemExit(f"missing expected release-contract file: {name}")
    data = path.read_text(encoding="utf-8")
    if "0.22.104" not in data:
        continue
    data = data.replace("0.22.104", "0.22.105")
    if "0.22.104" in data:
        raise SystemExit(f"{name}: stale 0.22.104 remains after alignment")
    path.write_text(data, encoding="utf-8")
    changed.append(name)

if len(changed) < 10:
    raise SystemExit(f"unexpectedly small release alignment set: {len(changed)} files")
print(f"aligned {len(changed)} release-contract files to 0.22.105")
