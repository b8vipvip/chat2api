(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.14.2";

  const docs = document.querySelector("#view-docs .panel");
  if (docs && !docs.querySelector("[data-chat2api-v14-time]")) {
    const block = document.createElement("div");
    block.dataset.chat2apiV14Time = "1";
    block.innerHTML = `
      <h2>时间标准</h2>
      <p>chat2api 的服务端记录、测试报告、扩展状态和运行日志中的人可读时间统一使用 <code>Asia/Shanghai</code>（北京时间，<code>+08:00</code>）。例如 <code>2026-08-11T19:55:12.000+08:00</code>。</p>
      <p>服务端控制台只显示北京时间，不再把已经是北京时间的表格值当作 UTC 再转换一次。<code>Date.now()</code> / Unix epoch 毫秒用于 120 秒封账等绝对截止时间，<code>performance.now()</code> 用于耗时统计；这两类数值与时区无关，不执行“加 8 小时”。</p>
      <h2>模型切换验证</h2>
      <p>扩展会优先使用当前 composer 的被动 DOM 状态验证模型与推理强度。模型菜单二次打开无法显示已选模型时，如果 composer 已明确显示例如 <code>5.5 高</code>，允许通过被动状态恢复验证；GPT-5.6 Sol 若在成功切换后把组合控件从 <code>5.5 极速</code> 收缩为仅 <code>极速</code>，或在未指定推理强度时收缩为 <code>智能/自动</code>，扩展会结合“已知旧 family + 精确目标 family 点击 + 控件发生可观察变化”恢复 family 状态。</p>
      <p>推理强度滑块不假定只有三个键盘步进；<code>极速</code> 使用 Home、<code>高</code> 使用 End、<code>中</code> 会逐步移动并以页面实际状态确认后再发送提示词。</p>
      <p>请求诊断导出中的 <code>server_version</code> 始终由当前服务端补写，不再保留旧版诊断中间件的历史版本号。</p>`;
    docs.appendChild(block);
  }
})();
