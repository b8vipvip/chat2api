(() => {
  const button = document.getElementById("pageSmoke");
  const message = document.getElementById("message");
  if (!button || !message) return;

  const yesNo = value => value ? "是" : "否";

  button.addEventListener("click", async () => {
    message.textContent = "正在对已绑定 ChatGPT 页面执行只读 Smoke Test，不会切换模型或推理强度…";
    try {
      const response = await chrome.runtime.sendMessage({ type: "popup.pageSmoke" });
      if (!response?.ok) {
        message.textContent = `页面 Smoke Test 执行失败：${response?.error || "未知错误"}`;
        return;
      }

      const data = response.data || {};
      const checks = data.checks || {};
      const finalProbe = data.final_probe || {};
      const summary = data.ok
        ? "页面 Smoke Test 通过"
        : `页面 Smoke Test 未通过（${data.code || "unknown"}）`;
      const lines = [
        summary,
        `Adapter：${yesNo(checks.adapter_loaded)}${checks.adapter_version ? ` v${checks.adapter_version}` : ""}`,
        `Page Driver：${yesNo(checks.driver_loaded)}${checks.driver_version ? ` v${checks.driver_version}` : ""}`,
        `Model Controller：${yesNo(checks.model_controller_loaded)}`,
        `Reasoning Controller：${yesNo(checks.reasoning_controller_loaded)}`,
        `Composer：${checks.composer_found && checks.composer_visible ? "可用" : "未就绪"}`,
        `期望状态：${data.expected_model || "未指定"}${data.expected_reasoning ? ` / ${data.expected_reasoning}` : ""}`,
        `Driver 状态：${data.current_state?.family || "未知"}${data.current_state?.reasoning ? ` / ${data.current_state.reasoning}` : ""}`,
        `最终 Probe：${data.final_probe_ok === null || data.final_probe_ok === undefined ? "未执行" : data.final_probe_ok ? "通过" : "失败"}${finalProbe.actual_family ? `（${finalProbe.actual_family}${finalProbe.actual_reasoning ? ` / ${finalProbe.actual_reasoning}` : ""}）` : ""}`,
      ];
      message.textContent = lines.join("\n");
      console.info("chat2api page smoke v22", data);
    } catch (error) {
      message.textContent = `页面 Smoke Test 执行失败：${String(error?.message || error)}`;
    }
  });
})();
