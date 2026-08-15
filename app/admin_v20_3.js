(() => {
  const VERSION = "0.20.3";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function patchDocs() {
    const panel = document.querySelector("#view-docs .panel");
    if (!panel || panel.querySelector("[data-model-affinity-v203]")) return;
    const block = document.createElement("div");
    block.dataset.modelAffinityV203 = "1";
    block.className = "docSection";
    block.innerHTML = `
      <h3>常用模型双槽预热</h3>
      <p>Chrome Bridge 每 10 分钟读取最近调用统计，选择最多两个常用的文本模型 + 推理强度组合，并让备用窗口提前完成对应模型状态准备。请求到来时优先领取完全匹配的备用窗口；领取后立即补建对应槽位，不等待当前回答结束。</p>
      <p><code>gpt-5.5-mini</code> 在 Free 账户保持默认模型零 UI 操作；在付费账户按既有规则预热为 <code>gpt-5.5 + 极速</code>。</p>
    `;
    panel.appendChild(block);
  }

  patchDocs();
  const docs = document.getElementById("view-docs");
  if (docs) new MutationObserver(() => patchDocs()).observe(docs, { childList: true, subtree: true });
})();
