(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.12";

  const doc = document.querySelector("#view-docs .panel");
  if (doc && !doc.querySelector("[data-chat2api-v12-docs]")) {
    const block = document.createElement("div");
    block.dataset.chat2apiV12Docs = "1";
    block.innerHTML = `
      <h2>按 API Key 复用 ChatGPT 会话</h2>
      <p>服务端只向扩展发送非秘密的 <code>api_key_id</code>。扩展会保存该 Key 最近一次 ChatGPT <code>/c/&lt;conversation-id&gt;</code> 地址，下次同一 Key 请求优先恢复该会话。</p>
      <p>默认会话性能预算：32 个完成回合、累计 96,000 个提示/回答字符、16 个附件；任一达到即新建聊天。恢复历史会话连续两次达到 8 秒，或单次达到 15 秒，也会立即换新聊天。</p>
      <p>扩展自动创建的每 Key 单标签窗口在请求完成后空闲 120 秒自动关闭；再次请求会重新打开上次保存的会话。用户手动关闭的窗口会被视为已关闭，不重复操作。</p>`;
    doc.appendChild(block);
  }
})();
