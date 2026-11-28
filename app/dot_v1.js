"use strict";
(() => {
  const byId = id => document.getElementById(id);
  const base = "/api/user/chat/dot";
  let owner = "", busy = false, pending = null;
  const status = text => { byId("dotStatus").textContent = text; };
  async function api(path, method = "GET", payload) {
    const headers = { "Accept": "application/json" };
    if (method !== "GET") headers["X-Chat2API-User-ID"] = owner;
    if (payload !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(path, { method, headers, credentials: "same-origin",
      cache: "no-store", ...(payload === undefined ? {} : {body: JSON.stringify(payload)}) });
    if (!res.ok) throw new Error("请求失败：" + res.status);
    return res.json();
  }
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  function button(label, action) {
    const n = node("button", label); n.type = "button"; n.className = "attach-button";
    n.addEventListener("click", () => execute(action)); return n;
  }
  async function refresh(loadProfile = false) {
    const data = await api(base);
    if (owner && owner !== data.user_id) { location.reload(); throw new Error("账户已变化"); }
    owner = data.user_id;
    if (loadProfile) {
      byId("dotName").value = data.profile.name;
      byId("dotInstructions").value = data.profile.instructions;
      const models = await api("/api/user/chat/models");
      const select = byId("dotModel");
      select.replaceChildren();
      for (const model of models.data || []) {
        if (!model.id) continue;
        const option = node("option", model.label || model.id);
        option.value = model.id; select.append(option);
      }
      byId("dotProfile").querySelector("button").disabled = false;
      byId("dotGoalForm").querySelector("button").disabled = !select.options.length;
    }
    const list = byId("dotTasks"); list.replaceChildren();
    for (const task of data.tasks) {
      const card = document.createElement("article"); card.className = "utility-item";
      card.append(node("h3", task.goal), node("p", task.model + " · " + ({
        queued:"待执行", running:"执行中", completed:"已完成", failed:"失败", interrupted:"已中断"
      }[task.status] || task.status)));
      if (task.request_id) card.append(node("p", "请求：" + task.request_id));
      if (task.result) card.append(node("pre", task.result));
      if (task.error) card.append(node("p", task.error));
      if (task.status === "queued") card.append(button("执行", async () => {status("正在调用模型，请保持页面打开…"); await api(base + "/tasks/" + task.id + "/run", "POST");}));
      if (task.status !== "running") card.append(button("删除", async () => {if (confirm("删除该任务及结果？")) await api(base + "/tasks/" + task.id, "DELETE");}));
      list.append(card);
    }
    if (!data.tasks.length) list.append(node("p", "暂无任务"));
  }
  async function execute(action, loadProfile = false) {
    if (busy) return;
    busy = true;
    try { await action(); await refresh(loadProfile); status("已更新"); }
    catch (err) { status(err.message); }
    finally { busy = false; }
  }
  byId("dotProfile").addEventListener("submit", e => {
    e.preventDefault(); execute(() => api(base + "/profile", "PUT", {name: byId("dotName").value, instructions: byId("dotInstructions").value}));
  });
  byId("dotGoalForm").addEventListener("submit", e => {
    e.preventDefault(); execute(async () => {
      const goal = byId("dotGoal").value, model = byId("dotModel").value;
      if (!pending || pending.goal !== goal || pending.model !== model) pending = {goal, model, idempotency_key: crypto.randomUUID()};
      await api(base + "/tasks", "POST", pending); pending = null; byId("dotGoal").value = "";
    });
  });
  byId("dotRefresh").addEventListener("click", () => execute(async () => {}));
  execute(async () => {}, true);
})();
