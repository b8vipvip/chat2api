(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.12";

  const doc = document.querySelector("#view-docs .panel");
  if (doc && !doc.querySelector("[data-chat2api-v12-docs]")) {
    const block = document.createElement("div");
    block.dataset.chat2apiV12Docs = "1";
    block.innerHTML = `
      <h2>按 API Key 复用 ChatGPT 会话</h2>
      <p>服务端只向扩展发送非秘密的 <code>api_key_id</code>。同一 Key 的自动化窗口仍然打开时，扩展继续复用当前 ChatGPT 会话；窗口一旦关闭，就不再恢复旧的 <code>/c/&lt;conversation-id&gt;</code> 历史页面，下一次请求直接进入新的聊天。</p>
      <p>默认会话性能预算：32 个完成回合、累计 96,000 个提示/回答字符、16 个附件；任一达到即在当前窗口切换到新聊天。窗口关闭后历史路由会被清空，避免下次请求再次加载旧会话。</p>
      <p>扩展自动创建的每 Key 单标签窗口在请求完成后空闲 300 秒自动关闭。扩展同时维护 1 个已预热的新聊天备用窗口；备用窗口被请求认领后，会立即在后台补建新的备用窗口。</p>`;
    doc.appendChild(block);
  }
})();
