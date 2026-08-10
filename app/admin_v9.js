(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.9";

  function installTestLabLayout() {
    if (document.querySelector("style[data-chat2api-v9-lab]")) return;
    const style = document.createElement("style");
    style.dataset.chat2apiV9Lab = "1";
    style.textContent = `
      .labConfig{
        display:grid!important;
        grid-template-columns:170px 180px minmax(330px,1.45fr) minmax(300px,1.25fr) 96px!important;
        gap:12px!important;
        align-items:start!important;
      }
      .labConfig>div{min-width:0!important}
      .labConfig label{min-height:19px;margin-bottom:6px!important}
      .labConfig select,.labConfig input{width:100%!important;min-width:0!important;max-width:100%!important}
      .testKeyBox{display:grid!important;grid-template-columns:minmax(0,1fr)!important;gap:7px!important;min-width:0!important}
      .testKeyHint{grid-column:1!important;margin-top:0!important;line-height:1.4!important}
      #testFiles{height:40px!important;padding:0!important;overflow:hidden!important;white-space:nowrap!important}
      #testFiles::file-selector-button{height:100%;margin:0 9px 0 0;padding:0 12px;border:0;border-right:1px solid var(--line);background:#26344d;color:var(--text);cursor:pointer}
      .labConfig>button#startTest{width:96px!important;min-width:96px!important;height:40px!important;margin-top:25px!important;align-self:start!important}
      @media(max-width:1450px){
        .labConfig{grid-template-columns:repeat(12,minmax(0,1fr))!important}
        .labConfig>div:nth-child(1){grid-column:span 3}
        .labConfig>div:nth-child(2){grid-column:span 3}
        .labConfig>div:nth-child(3){grid-column:span 6}
        .labConfig>div:nth-child(4){grid-column:span 10}
        .labConfig>button#startTest{grid-column:span 2;width:100%!important;min-width:0!important}
      }
      @media(max-width:900px){
        .labConfig{grid-template-columns:1fr!important}
        .labConfig>div,.labConfig>button#startTest{grid-column:1!important;width:100%!important}
        .labConfig>button#startTest{margin-top:0!important}
      }
    `;
    document.head.appendChild(style);
  }

  function removeDictationUi() {
    const typeSelect = $("testType");
    if (typeSelect) {
      [...typeSelect.options].filter(option => option.value === "dictation").forEach(option => option.remove());
      if (typeSelect.value === "dictation") typeSelect.value = "all";
    }

    document.querySelectorAll("#view-docs li,#view-docs p,#view-docs h2,#view-docs h3").forEach(node => {
      const text = String(node.textContent || "");
      if (/gpt-dictation|\/v1\/audio\/transcriptions|听写\s*\/\s*语音转文字|ChatGPT Dictation/i.test(text)) node.remove();
    });
  }

  installTestLabLayout();
  removeDictationUi();

  const testLabels = {
    text: "文本",
    vision: "视觉理解",
    file: "文件理解",
    image_generation: "图片生成",
    voice_generation: "语音生成",
    voice_conversation: "语音对话",
  };

  $("startTest").onclick = async () => {
    if (!key()) return status("请先连接管理员主密钥", "bad");
    let credential;
    try {
      credential = await resolveTestCredential();
    } catch (error) {
      return status("测试 Key 读取失败：" + error.message, "bad");
    }

    const type = $("testType").value;
    if (!Object.prototype.hasOwnProperty.call(testLabels, type) && type !== "all") {
      return status(`当前测试类型已移除或不受支持：${type}`, "bad");
    }
    const model = $("testModel").value;
    const files = [...$("testFiles").files];
    const kinds = type === "all"
      ? ["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation"]
      : [type];
    const rows = [];
    $("testState").textContent = "运行中";
    redraw(rows);
    status(`正在使用：${credential.label}`, "ok");
    const startedAt = new Date().toISOString();
    const start = performance.now();

    for (const kind of kinds) {
      rows.push({ kind, label: testLabels[kind] || kind, status: "running", message: "执行中" });
      redraw(rows);
      rows[rows.length - 1] = await oneTest(kind, model, files, credential.token);
      redraw(rows);
    }

    const failed = rows.some(item => item.status === "failed");
    const warn = rows.some(item => item.status === "warning");
    const passed = rows.some(item => item.status === "passed");
    const reportStatus = failed ? "failed" : warn ? "warning" : passed ? "passed" : "skipped";
    const runId = "testrun_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    const report = {
      run_id: runId,
      test_type: type,
      status: reportStatus,
      model,
      started_at: startedAt,
      finished_at: new Date().toISOString(),
      duration_ms: performance.now() - start,
      summary: `${rows.filter(item => item.status === "passed").length} passed, ${rows.filter(item => item.status === "warning").length} warning, ${rows.filter(item => item.status === "failed").length} failed, ${rows.filter(item => item.status === "skipped").length} skipped`,
      results: rows,
      quality: {
        api_key_label: credential.label,
        api_key_source: credential.source,
        api_key_id: credential.key_id,
        bugs: rows.filter(item => item.status === "failed").map(item => item.message),
        latency_warnings: rows.flatMap(item => item.quality?.issues || []).filter(issue => /延迟|耗时|超时/.test(issue)),
      },
    };

    try {
      await api("/api/admin/tests", { method: "POST", body: report });
      $("testState").textContent = reportStatus;
      await loadTests();
    } catch (error) {
      status("保存测试报告失败：" + error.message, "bad");
    }
  };
})();
