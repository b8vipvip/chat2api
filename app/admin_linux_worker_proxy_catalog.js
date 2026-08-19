(() => {
  const section = document.getElementById("view-linux-workers");
  const proxyDialog = document.getElementById("linuxWorkerProxyDialog");
  const workerRows = document.getElementById("linuxWorkerRows");
  if (!section || !proxyDialog || !workerRows || document.getElementById("linuxProxyCatalogDialog")) return;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const request = async (path, options = {}) => {
    const {headers = {}, ...rest} = options;
    const response = await fetch(path, {credentials:"same-origin",cache:"no-store",...rest,headers:{"Content-Type":"application/json",...headers}});
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
    return payload;
  };

  const toolbar = section.querySelector(".toolbar");
  const refreshButton = document.getElementById("refreshLinuxWorkers");
  const addProxyButton = document.createElement("button");
  addProxyButton.className = "action";
  addProxyButton.id = "manageLinuxProxies";
  addProxyButton.type = "button";
  addProxyButton.textContent = "添加代理";
  toolbar.insertBefore(addProxyButton, refreshButton || null);

  const catalogDialog = document.createElement("dialog");
  catalogDialog.id = "linuxProxyCatalogDialog";
  catalogDialog.style.cssText = "width:min(980px,calc(100vw - 32px));max-width:none;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e5e7eb;padding:0;box-shadow:0 24px 80px rgba(0,0,0,.6)";
  catalogDialog.innerHTML = `<div style="padding:20px"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><div><div style="font-size:18px;font-weight:700">代理管理</div><div style="margin-top:4px;color:#94a3b8;font-size:12px">保存后的节点可在任意 Linux Worker 的“代理”按钮中直接选择。节点链接会保存在中心服务器 data 目录的受限文件中。</div></div><button class="action" id="closeLinuxProxyCatalog" type="button">关闭</button></div><div style="margin-top:16px;padding:14px;border:1px solid #334155;border-radius:10px;background:#020617"><div style="font-weight:700;margin-bottom:10px">新增代理</div><div style="display:grid;grid-template-columns:minmax(160px,240px) 1fr;gap:10px"><input id="linuxCatalogNewName" autocomplete="off" placeholder="代理名称（可选）" style="box-sizing:border-box;width:100%;padding:10px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb"><textarea id="linuxCatalogNewLink" autocomplete="off" spellcheck="false" placeholder="vless://、vmess://、trojan:// 或 ss://" style="box-sizing:border-box;width:100%;min-height:74px;padding:10px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb;resize:vertical"></textarea></div><div style="display:flex;justify-content:flex-end;margin-top:10px"><button class="action good" id="createLinuxCatalogProxy" type="button">新增并保存</button></div><div id="linuxCatalogResult" style="min-height:20px;margin-top:8px;color:#94a3b8"></div></div><div id="linuxProxyCatalogList" style="margin-top:14px"></div></div>`;
  document.body.appendChild(catalogDialog);

  proxyDialog.innerHTML = `<div style="padding:20px"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><div><div style="font-size:18px;font-weight:700">配置 Worker 代理</div><div id="linuxProxyWorkerName" style="margin-top:4px;color:#94a3b8"></div></div><button class="action" id="closeLinuxProxy" type="button">关闭</button></div><div style="margin-top:14px;color:#94a3b8;line-height:1.6">从已保存代理中直接选择并应用；如果还没有保存节点，也可以在下方新增并保存，保存后会自动同步到“代理管理”。代理应用成功前不会打开 ChatGPT 登录窗口。</div><div style="margin-top:14px;padding:14px;border:1px solid #334155;border-radius:10px;background:#020617"><div style="font-weight:700;margin-bottom:9px">已保存代理</div><div style="display:flex;gap:8px;align-items:center"><select id="linuxSavedProxySelect" style="min-width:0;flex:1;padding:10px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb"></select><button class="action" id="openLinuxProxyCatalog" type="button">管理代理</button><button class="action good" id="applyLinuxProxy" type="button">应用所选代理</button></div><div id="linuxSavedProxyHint" style="margin-top:8px;color:#94a3b8;font-size:12px"></div></div><div style="margin-top:14px;padding:14px;border:1px solid #334155;border-radius:10px;background:#020617"><div style="font-weight:700;margin-bottom:9px">新增代理并应用</div><input id="linuxProxyNewName" autocomplete="off" placeholder="代理名称（可选）" style="box-sizing:border-box;width:100%;padding:10px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb"><textarea id="linuxProxyShareLink" autocomplete="off" spellcheck="false" placeholder="粘贴 vless://、vmess://、trojan:// 或 ss:// 节点链接" style="box-sizing:border-box;width:100%;min-height:96px;margin-top:9px;padding:10px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb;resize:vertical"></textarea><div style="display:flex;justify-content:flex-end;margin-top:9px"><button class="action good" id="linuxSaveApplyProxy" type="button">保存并应用</button></div></div><div id="linuxProxyResult" style="min-height:22px;margin-top:12px;color:#94a3b8"></div><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:12px"><button class="action" id="testLinuxProxy" type="button">测试当前代理</button></div></div>`;

  let catalog = [];
  let selectedWorkerId = "";
  let selectedWorkerName = "";
  let proxyBusy = false;

  const setText = (id, text) => { const node = document.getElementById(id); if (node) node.textContent = text; };
  const setProxyBusy = busy => {
    proxyBusy = busy;
    for (const id of ["applyLinuxProxy","linuxSaveApplyProxy","testLinuxProxy","closeLinuxProxy","openLinuxProxyCatalog"]) {
      const node = document.getElementById(id);
      if (node) node.disabled = busy;
    }
  };

  const renderWorkerChoices = () => {
    const select = document.getElementById("linuxSavedProxySelect");
    if (!select) return;
    const previous = select.value;
    if (!catalog.length) {
      select.innerHTML = '<option value="">暂无已保存代理</option>';
      select.disabled = true;
      document.getElementById("applyLinuxProxy").disabled = true;
      setText("linuxSavedProxyHint", "代理列表为空，请在下方新增并保存；保存后会立即出现在代理管理页面。");
      return;
    }
    select.disabled = proxyBusy;
    select.innerHTML = catalog.map(item => `<option value="${esc(item.proxy_id)}">${esc(item.name || "未命名代理")} · ${esc(String(item.scheme || "proxy").toUpperCase())}</option>`).join("");
    if (catalog.some(item => item.proxy_id === previous)) select.value = previous;
    document.getElementById("applyLinuxProxy").disabled = proxyBusy;
    setText("linuxSavedProxyHint", `已保存 ${catalog.length} 个代理，选择后可直接校验并应用到当前 Worker。`);
  };

  const renderCatalog = () => {
    const list = document.getElementById("linuxProxyCatalogList");
    if (!catalog.length) {
      list.innerHTML = '<div style="padding:22px;text-align:center;border:1px dashed #334155;border-radius:10px;color:#94a3b8">暂无代理，请先新增。</div>';
      return;
    }
    list.innerHTML = catalog.map(item => `<div data-catalog-row="${esc(item.proxy_id)}" style="padding:14px;margin-bottom:10px;border:1px solid #334155;border-radius:10px;background:#020617"><div style="display:grid;grid-template-columns:minmax(160px,240px) 1fr;gap:10px"><input data-catalog-name="${esc(item.proxy_id)}" value="${esc(item.name)}" autocomplete="off" style="box-sizing:border-box;width:100%;padding:9px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb"><textarea data-catalog-link="${esc(item.proxy_id)}" autocomplete="off" spellcheck="false" style="box-sizing:border-box;width:100%;min-height:72px;padding:9px;border:1px solid #334155;border-radius:8px;background:#0f172a;color:#e5e7eb;resize:vertical">${esc(item.share_link)}</textarea></div><div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:9px"><div style="font-size:12px;color:#64748b">${esc(String(item.scheme || "proxy").toUpperCase())} · 更新 ${esc(item.updated_at || "-")}</div><div style="display:flex;gap:8px"><button class="action" data-save-catalog="${esc(item.proxy_id)}" type="button">保存修改</button><button class="action danger" data-delete-catalog="${esc(item.proxy_id)}" type="button">删除</button></div></div></div>`).join("");
  };

  const loadCatalog = async () => {
    const payload = await request("/api/admin/linux-worker-proxies");
    catalog = Array.isArray(payload.data) ? payload.data : [];
    renderCatalog();
    renderWorkerChoices();
    return catalog;
  };

  const openCatalog = async () => {
    setText("linuxCatalogResult", "正在加载…");
    if (!catalogDialog.open) catalogDialog.showModal();
    try { await loadCatalog(); setText("linuxCatalogResult", ""); }
    catch (error) { setText("linuxCatalogResult", error.message); }
  };

  const closeWorkerProxy = () => {
    if (proxyBusy) return;
    selectedWorkerId = "";
    selectedWorkerName = "";
    document.getElementById("linuxProxyShareLink").value = "";
    document.getElementById("linuxProxyNewName").value = "";
    setText("linuxProxyResult", "");
    if (proxyDialog.open) proxyDialog.close();
  };

  const openWorkerProxy = async (workerId, workerName) => {
    selectedWorkerId = workerId;
    selectedWorkerName = workerName || workerId;
    setText("linuxProxyWorkerName", selectedWorkerName);
    setText("linuxProxyResult", "正在加载已保存代理…");
    document.getElementById("linuxProxyShareLink").value = "";
    document.getElementById("linuxProxyNewName").value = "";
    if (!proxyDialog.open) proxyDialog.showModal();
    try {
      await loadCatalog();
      setText("linuxProxyResult", "");
      const select = document.getElementById("linuxSavedProxySelect");
      if (catalog.length) select.focus(); else document.getElementById("linuxProxyShareLink").focus();
    } catch (error) {
      setText("linuxProxyResult", error.message);
    }
  };

  const applyLinkToWorker = async shareLink => {
    if (!selectedWorkerId || !shareLink || proxyBusy) return;
    setProxyBusy(true);
    setText("linuxProxyResult", "正在校验 Xray 配置、应用并测试 ChatGPT 连通性…");
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy`, {method:"POST",body:JSON.stringify({share_link:shareLink})});
      const proxy = result.proxy || {};
      setText("linuxProxyResult", `应用成功：${proxy.protocol || "proxy"}${proxy.server ? ` · ${proxy.server}:${proxy.port || ""}` : ""} · HTTP ${result.test?.http_status || "reachable"}；现在可以登录。`);
      if (refreshButton) refreshButton.click();
    } catch (error) {
      setText("linuxProxyResult", error.message);
    } finally {
      setProxyBusy(false);
      renderWorkerChoices();
    }
  };

  addProxyButton.addEventListener("click", openCatalog);
  document.getElementById("closeLinuxProxyCatalog").onclick = () => { if (catalogDialog.open) catalogDialog.close(); };
  catalogDialog.addEventListener("cancel", event => { event.preventDefault(); catalogDialog.close(); });
  document.getElementById("createLinuxCatalogProxy").onclick = async () => {
    const nameNode = document.getElementById("linuxCatalogNewName");
    const linkNode = document.getElementById("linuxCatalogNewLink");
    const shareLink = linkNode.value.trim();
    if (!shareLink) { setText("linuxCatalogResult", "请先填写代理链接。"); return; }
    setText("linuxCatalogResult", "正在保存…");
    try {
      await request("/api/admin/linux-worker-proxies", {method:"POST",body:JSON.stringify({name:nameNode.value.trim(),share_link:shareLink})});
      nameNode.value = "";
      linkNode.value = "";
      await loadCatalog();
      setText("linuxCatalogResult", "代理已保存。");
    } catch (error) { setText("linuxCatalogResult", error.message); }
  };

  document.getElementById("linuxProxyCatalogList").onclick = async event => {
    const saveId = event.target.dataset.saveCatalog;
    if (saveId) {
      const name = document.querySelector(`[data-catalog-name="${CSS.escape(saveId)}"]`)?.value || "";
      const shareLink = document.querySelector(`[data-catalog-link="${CSS.escape(saveId)}"]`)?.value.trim() || "";
      setText("linuxCatalogResult", "正在保存修改…");
      try {
        await request(`/api/admin/linux-worker-proxies/${encodeURIComponent(saveId)}`, {method:"PATCH",body:JSON.stringify({name,share_link:shareLink})});
        await loadCatalog();
        setText("linuxCatalogResult", "修改已保存。");
      } catch (error) { setText("linuxCatalogResult", error.message); }
      return;
    }
    const deleteId = event.target.dataset.deleteCatalog;
    if (deleteId && confirm("确定删除这个已保存代理？已经应用到 Worker 的本地 Xray 配置不会被自动删除。")) {
      setText("linuxCatalogResult", "正在删除…");
      try {
        await request(`/api/admin/linux-worker-proxies/${encodeURIComponent(deleteId)}`, {method:"DELETE"});
        await loadCatalog();
        setText("linuxCatalogResult", "代理已删除。");
      } catch (error) { setText("linuxCatalogResult", error.message); }
    }
  };

  document.getElementById("closeLinuxProxy").onclick = closeWorkerProxy;
  document.getElementById("openLinuxProxyCatalog").onclick = async () => {
    if (proxyDialog.open) proxyDialog.close();
    await openCatalog();
  };
  document.getElementById("applyLinuxProxy").onclick = async () => {
    const id = document.getElementById("linuxSavedProxySelect").value;
    const item = catalog.find(entry => entry.proxy_id === id);
    if (!item) { setText("linuxProxyResult", "请先选择一个已保存代理。"); return; }
    await applyLinkToWorker(item.share_link);
  };
  document.getElementById("linuxSaveApplyProxy").onclick = async () => {
    const name = document.getElementById("linuxProxyNewName").value.trim();
    const linkNode = document.getElementById("linuxProxyShareLink");
    const shareLink = linkNode.value.trim();
    if (!shareLink) { setText("linuxProxyResult", "请先粘贴代理链接。"); return; }
    setProxyBusy(true);
    setText("linuxProxyResult", "正在保存代理…");
    try {
      const saved = await request("/api/admin/linux-worker-proxies", {method:"POST",body:JSON.stringify({name,share_link:shareLink})});
      await loadCatalog();
      document.getElementById("linuxSavedProxySelect").value = saved.proxy?.proxy_id || "";
      document.getElementById("linuxProxyNewName").value = "";
      linkNode.value = "";
      setProxyBusy(false);
      await applyLinkToWorker(saved.proxy?.share_link || shareLink);
    } catch (error) {
      setText("linuxProxyResult", error.message);
      setProxyBusy(false);
      renderWorkerChoices();
    }
  };
  document.getElementById("testLinuxProxy").onclick = async () => {
    if (!selectedWorkerId || proxyBusy) return;
    setProxyBusy(true);
    setText("linuxProxyResult", "正在通过当前 SOCKS 代理测试 ChatGPT…");
    try {
      const result = await request(`/api/admin/linux-workers/${encodeURIComponent(selectedWorkerId)}/proxy/test`, {method:"POST",body:"{}"});
      setText("linuxProxyResult", result.ok ? `当前代理可达 · HTTP ${result.http_status || "reachable"}` : `当前代理不可达：${result.error || "test failed"}`);
    } catch (error) { setText("linuxProxyResult", error.message); }
    finally { setProxyBusy(false); renderWorkerChoices(); }
  };

  workerRows.addEventListener("click", event => {
    const target = event.target.closest?.("[data-proxy]");
    if (!target) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openWorkerProxy(target.dataset.proxy, target.dataset.workerName || target.dataset.proxy);
  }, true);
})();