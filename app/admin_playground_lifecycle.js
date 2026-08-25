(() => {
  const RUNNING_STATUSES = new Set(["pending", "running"]);
  const TERMINAL_STATUSES = new Set(["passed", "warning", "failed", "skipped", "cancelled", "stalled"]);
  let starting = false;
  let activeRunId = null;
  let pollTimer = null;

  const previousPill = pill;
  pill = value => RUNNING_STATUSES.has(String(value || ""))
    ? `<span class="pill warn">${esc(value)}</span>`
    : previousPill(value);

  const startButton = document.getElementById("startTest");
  if (!startButton) return;

  if (!document.querySelector("style[data-chat2api-playground-lifecycle]")) {
    const style = document.createElement("style");
    style.dataset.chat2apiPlaygroundLifecycle = "1";
    style.textContent = `
      .labConfig{grid-template-columns:170px 180px minmax(330px,1.45fr) minmax(300px,1.25fr) 96px 96px!important}
      .labConfig>button#cancelTest{height:40px;margin-top:25px;align-self:start}
      @media(max-width:1450px){
        .labConfig{grid-template-columns:repeat(2,minmax(0,1fr))!important}
        .labConfig>div,.labConfig>button#startTest,.labConfig>button#cancelTest{grid-column:auto!important;width:100%!important;min-width:0!important}
        .labConfig>button#startTest,.labConfig>button#cancelTest{margin-top:0!important}
      }
      @media(max-width:900px){
        .labConfig{grid-template-columns:1fr!important}
        .labConfig>div,.labConfig>button#startTest,.labConfig>button#cancelTest{grid-column:1!important;width:100%;margin-top:0}
      }
    `;
    document.head.appendChild(style);
  }

  let cancelButton = document.getElementById("cancelTest");
  if (!cancelButton) {
    cancelButton = document.createElement("button");
    cancelButton.id = "cancelTest";
    cancelButton.className = "action danger";
    cancelButton.textContent = "取消测试";
    cancelButton.style.display = "none";
    startButton.insertAdjacentElement("afterend", cancelButton);
  }

  const statusSelect = document.getElementById("rqStatus");
  if (statusSelect) {
    for (const value of ["running", "cancelled", "stalled"]) {
      if (![...statusSelect.options].some(option => option.value === value)) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        statusSelect.appendChild(option);
      }
    }
  }

  function setControls(run) {
    const running = Boolean(run && RUNNING_STATUSES.has(String(run.status || "")));
    startButton.disabled = starting || running;
    startButton.textContent = starting ? "创建中" : running ? "测试运行中" : "开始测试";
    cancelButton.style.display = running ? "inline-block" : "none";
    cancelButton.disabled = !running;
  }

  function renderRun(run) {
    if (!run) return;
    const results = Array.isArray(run.results) ? run.results : [];
    document.getElementById("testState").textContent = String(run.status || "待命");
    redraw(results);
    document.getElementById("testFail").textContent = results.filter(
      item => ["failed", "stalled"].includes(String(item.status || "")),
    ).length;
    activeRunId = RUNNING_STATUSES.has(String(run.status || "")) ? String(run.run_id || "") : null;
    setControls(run);
  }

  function renderHistory(runs) {
    const body = document.getElementById("testHistory");
    body.innerHTML = (runs || []).map(run => `
      <tr>
        <td>${fmtTime(run.recorded_at || run.started_at || run.finished_at)}</td>
        <td>${esc(run.test_type)}</td>
        <td>${pill(run.status)}</td>
        <td>${esc(run.model || "-")}</td>
        <td class="keyBadge" title="${esc(run.api_key_name || run.quality?.api_key_label || "-")}">${esc(run.api_key_name || run.quality?.api_key_label || "-")}</td>
        <td>${fmtMs(run.duration_ms)}</td>
        <td>${esc(run.summary || "")}</td>
        <td><div class="rowactions"><button class="action" onclick="showTest('${esc(run.run_id)}')">查看报告</button><button class="action" onclick="downloadTest('${esc(run.run_id)}')">下载</button></div></td>
      </tr>`).join("") || '<tr><td colspan="8" class="muted">暂无测试记录</td></tr>';
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    if (!activeRunId) return;
    pollTimer = setTimeout(pollActiveRun, 1000);
  }

  async function pollActiveRun() {
    if (!activeRunId) return;
    try {
      const run = await api(`/api/admin/tests/${encodeURIComponent(activeRunId)}`);
      renderRun(run);
      const listing = await api("/api/admin/tests?limit=50");
      renderHistory(listing.data || []);
      if (TERMINAL_STATUSES.has(String(run.status || ""))) {
        activeRunId = null;
        setControls(null);
        return;
      }
    } catch (error) {
      status("测试状态读取失败：" + String(error?.message || error), "bad");
    }
    schedulePoll();
  }

  loadTests = async function loadPersistentPlaygroundRuns() {
    if (!key()) return;
    try {
      await loadTestKeys();
      const data = await api("/api/admin/tests?limit=50");
      const runs = data.data || [];
      renderHistory(runs);
      const running = runs.find(run => RUNNING_STATUSES.has(String(run.status || "")));
      if (running) {
        renderRun(running);
        schedulePoll();
      } else {
        activeRunId = null;
        setControls(null);
      }
    } catch (error) {
      status(String(error?.message || error), "bad");
    }
  };

  startButton.onclick = async () => {
    if (starting || activeRunId) return;
    if (!key()) return status("请先登录管理员账号", "bad");
    starting = true;
    setControls(null);
    try {
      const credential = await resolveTestCredential();
      const files = [];
      for (const file of [...document.getElementById("testFiles").files]) {
        files.push({
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          data_base64: await fileToBase64(file),
        });
      }
      const model = document.getElementById("testModel").value;
      const body = {
        test_type: document.getElementById("testType").value,
        model,
        reasoning_effort: model === "gpt-5.5-mini" ? null : (document.getElementById("testReasoning")?.value || "medium"),
        files,
      };
      if (credential.source === "managed" && credential.key_id) {
        body.api_key_id = credential.key_id;
      } else {
        body.api_key = credential.token;
      }
      const response = await api("/api/admin/playground/runs", {method: "POST", body});
      renderRun(response.run);
      status(`测试任务已持久化：${response.run.run_id}`, "ok");
      await loadTests();
      schedulePoll();
    } catch (error) {
      activeRunId = null;
      status("启动测试失败：" + String(error?.message || error), "bad");
    } finally {
      starting = false;
      setControls(activeRunId ? {status: "running"} : null);
    }
  };

  cancelButton.onclick = async () => {
    if (!activeRunId) return;
    const runId = activeRunId;
    cancelButton.disabled = true;
    try {
      const response = await api(`/api/admin/playground/runs/${encodeURIComponent(runId)}/cancel`, {method: "POST"});
      renderRun(response.run);
      activeRunId = null;
      await loadTests();
      status(`测试已取消：${runId}`, "ok");
    } catch (error) {
      status("取消测试失败：" + String(error?.message || error), "bad");
      cancelButton.disabled = false;
    }
  };

  setControls(null);
})();
