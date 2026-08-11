(() => {
  const OLD_KEY = "chat2api:model-state:v1";
  const NEW_KEY = "chat2api:model-state:v2";
  try {
    if (sessionStorage.getItem(NEW_KEY)) return;
    const old = JSON.parse(sessionStorage.getItem(OLD_KEY) || "null");
    if (!old || old.dirty || !["gpt-5.6-sol", "gpt-5.5"].includes(String(old.family || ""))) return;
    const reasoning = ["instant", "medium", "high"].includes(String(old.reasoning || "")) ? String(old.reasoning) : "";
    sessionStorage.setItem(NEW_KEY, JSON.stringify({
      family: String(old.family),
      reasoning,
      dirty_family: false,
      dirty_reasoning: false,
      source: "migrated-v6-trusted-cache",
      updated_at: Date.now(),
    }));
  } catch (_) {}
})();
