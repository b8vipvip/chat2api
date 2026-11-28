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
        queued:"待提交", scheduled:"等待调度", paused:"已暂停", running:"执行中", completed:"已完成", failed:"失败", interrupted:"已中断", cancelled:"已取消"
      }[task.status] || task.status)));
      if (task.stop_requested && task.status === "running") card.append(node("p", "已请求停止，等待 Worker 终态"));
      if (task.status === "scheduled" || task.status === "paused") card.append(node("p", "计划执行：" + new Date(task.run_at * 1000).toLocaleString()));
      if (task.request_id) card.append(node("p", "请求：" + task.request_id));
      if (task.result) card.append(node("pre", task.result));
      if (task.error) card.append(node("p", task.error));
      const path = base + "/tasks/" + task.id;
      if (task.status === "queued") {
        card.append(button("后台执行", () => api(path + "/run", "POST")));
        card.append(button("定时执行", async () => {
          const value = prompt("多少分钟后执行？（0–43200）", "60");
          if (value === null) return;
          const minutes = Number(value);
          if (!value.trim() || !Number.isInteger(minutes) || minutes < 0 || minutes > 43200) throw new Error("请输入有效分钟数");
          await api(path + "/run", "POST", {delay_seconds: minutes * 60});
        }));
      }
      if (task.status === "scheduled") card.append(button("暂停", () => api(path + "/control/pause", "POST")));
      if (task.status === "paused") card.append(button("恢复", () => api(path + "/control/resume", "POST")));
      if (["queued","scheduled","paused","running"].includes(task.status) && !task.stop_requested)
        card.append(button("停止", () => api(path + "/control/stop", "POST")));
      if (!["running","scheduled","paused"].includes(task.status)) card.append(button("删除", async () => {if (confirm("删除该任务及结果？")) await api(base + "/tasks/" + task.id, "DELETE");}));
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
  setInterval(() => { if (!document.hidden && owner && !busy) execute(async () => {}); }, 4000);
})();
