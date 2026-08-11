(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.15.0";

  const docs = document.querySelector("#view-docs .panel");
  if (!docs) return;

  for (const paragraph of docs.querySelectorAll("p")) {
    if (paragraph.textContent.includes("省略该参数时保留当前 ChatGPT 页面强度")) {
      paragraph.innerHTML = paragraph.innerHTML.replace(
        "省略该参数时保留当前 ChatGPT 页面强度",
        "省略该参数时统一按 <code>medium</code>（中）执行",
      );
    }
  }

  if (!docs.querySelector("[data-chat2api-v15-reasoning-default]")) {
    const block = document.createElement("div");
    block.dataset.chat2apiV15ReasoningDefault = "1";
    block.innerHTML = `
      <h2>默认推理强度</h2>
      <p>所有 OpenAI 兼容文本入口统一采用确定性默认值：调用方未传 <code>reasoning_effort</code>，或 Responses API 未传 <code>reasoning.effort</code> 时，chat2api 自动按 <code>medium</code> 执行，对应 ChatGPT 页面中的“中”。</p>
      <p>显式传入 <code>low</code> / <code>medium</code> / <code>high</code> 时仍分别映射为“极速 / 中 / 高”。网页端的“智能/自动”只保留为兼容旧页面状态的防御性识别，不再作为 API 省略参数时的默认行为。</p>`;
    docs.appendChild(block);
  }
})();
