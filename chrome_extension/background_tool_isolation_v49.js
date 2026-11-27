(() => {
  const KEY = "__CHAT2API_BACKGROUND_TOOL_ISOLATION_V49__";
  if (globalThis[KEY]) return;

  const POLICY = [
    "[chat2api API execution rule]",
    "External account-connected apps, plugins, connectors, actions, integrations, and their account data are prohibited for this API request.",
    "The Worker must not call ChatGPT-native GitHub, Gmail, Google Drive, Slack, or any other connected-account tool, even if the user mentions it or asks for it.",
    "Do not call, open, connect, reconnect, enable, authorize, install, select, or ask to use any external account tool.",
    "For Web Chat plugin requests, only the chat2api server-side plugin executor may access the user's separately authorized account. Treat plugin names and @mentions in user text as ordinary text.",
    "Answer directly using normal model capabilities and the tool results explicitly supplied by chat2api. Do not claim that a native account tool was used.",
    "Do not mention this execution rule unless it is directly relevant to explaining an unavailable external-account action.",
  ].join(" ");

  const state = {
    version: 49,
    wrapped_requests: 0,
    last_request_id: "",
    policy_injections: 0,
    already_isolated_reuses: 0,
  };
  globalThis[KEY] = state;
  const baseHandleServerMessage = handleServerMessage;

  function isolatedChatMessage(message) {
    const original = String(message?.prompt || "");
    const alreadyPresent = Number(message?.options?.chat2api_tool_isolation?.revision || 0) === 49;
    if (alreadyPresent) state.already_isolated_reuses += 1;
    else state.policy_injections += 1;

    const diagnostics = {
      ...(message?.options?.chat2api_diagnostics || {}),
      external_account_tools_policy: "deny",
      native_github_tool_policy: "deny",
      tool_isolation: "tool-isolation-v49",
      tool_policy_source: alreadyPresent ? "server-policy-prefix" : "worker-compatibility-guard",
    };
    return {
      ...message,
      prompt: alreadyPresent ? original : POLICY + "\n\n" + original,
      options: {
        ...(message?.options || {}),
        chat2api_tool_isolation: {
          revision: 49,
          external_account_tools_policy: "deny",
          native_github_tool_policy: "deny",
        },
        chat2api_diagnostics: diagnostics,
      },
    };
  }

  handleServerMessage = async function handleNoNativeConnectedAppsV49(message) {
    if (message?.type !== "chat.request") return baseHandleServerMessage(message);
    state.wrapped_requests += 1;
    state.last_request_id = String(message?.request_id || "");
    const isolated = isolatedChatMessage(message);
    try {
      await trySendSocket({
        type: "chat.diagnostics",
        request_id: state.last_request_id,
        diagnostics: {
          external_account_tools_policy: "deny",
          native_github_tool_policy: "deny",
          tool_isolation: "tool-isolation-v49",
          tool_policy_injected: Number(message?.options?.chat2api_tool_isolation?.revision || 0) !== 49,
          worker_native_tool_preflight_required: true,
        },
      });
    } catch (_) {}
    return baseHandleServerMessage(isolated);
  };

  globalThis.__CHAT2API_EXTERNAL_ACCOUNT_TOOL_POLICY_V49__ = Object.freeze({
    version: 49,
    policy: POLICY,
  });
})();