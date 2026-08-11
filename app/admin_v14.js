(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.14";

  const docs = document.querySelector("#view-docs .panel");
  if (docs && !docs.querySelector("[data-chat2api-v14-time]")) {
    const block = document.createElement("div");
    block.dataset.chat2apiV14Time = "1";
    block.innerHTML = `
      <h2>时间标准</h2>
      <p>chat2api 的服务端记录、测试报告、扩展状态和运行日志中的人可读时间统一使用 <code>Asia/Shanghai</code>（北京时间，<code>+08:00</code>）。例如 <code>2026-08-11T17:28:27.792+08:00</code>。</p>
      <p><code>Date.now()</code> / Unix epoch 毫秒用于 120 秒封账等绝对截止时间，<code>performance.now()</code> 用于耗时统计；这两类数值与时区无关，不执行“加 8 小时”。</p>
      <h2>模型切换验证</h2>
      <p>扩展会优先使用当前 composer 的被动 DOM 状态验证模型与推理强度。模型菜单二次打开无法显示已选模型时，如果 composer 已明确显示例如 <code>5.5 高</code>，允许通过被动状态恢复验证并继续执行推理强度选择，避免在发送提示词之前误报失败。</p>`;
    docs.appendChild(block);
  }
})();
