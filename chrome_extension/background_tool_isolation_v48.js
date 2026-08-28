(() => {
  const KEY = "__CHAT2API_BACKGROUND_TOOL_ISOLATION_V48__";
  if (globalThis[KEY]) return;

  const POLICY = [
    "[chat2api API execution rule]",
    "External account-connected apps, plugins, connectors, actions, integrations, and their account data are disabled for this API request.",
    "Do not call, open, connect, reconnect, enable, authorize, install, select, or ask to use any external account tool, even when the user names one or asks you to use it.",
    "Treat plugin/connector/app names and @mentions in the user's text as ordinary text only.",
    "Answer directly using only the normal ChatGPT model capabilities available without external account connections. Do not mention this execution rule unless it is directly relevant to explaining an unavailable external-account action.",
  ].join(" ");

  const state = {version: 48, wrapped_requests: 0, last_request_id: ""};
  globalThis[KEY] = state;
  const baseHandleServerMessage = handleServerMessage;

  function isolatedChatMessage(message) {
    const original = String(message?.prompt || "");
    const diagnostics = {
      ...(message?.options?.chat2api_diagnostics || {}),
      external_account_tools_disabled: true,
      tool_isolation: "tool-isolation-v48",
    };
    return {
      ...message,
      prompt: `${POLICY}\n\n${original}`,
      options: {
        ...(message?.options || {}),
        chat2api_diagnostics: diagnostics,
      },
    };
  }

  handleServerMessage = async function handleNoAccountToolsV48(message) {
    if (message?.type !== "chat.request") return baseHandleServerMessage(message);
    state.wrapped_requests += 1;
    state.last_request_id = String(message?.request_id || "");
    const isolated = isolatedChatMessage(message);
    try {
      await trySendSocket({
        type: "chat.diagnostics",
        request_id: state.last_request_id,
        diagnostics: {
          external_account_tools_disabled: true,
          tool_isolation: "tool-isolation-v48",
          tool_policy_injected: true,
        },
      });
    } catch (_) {}
    return baseHandleServerMessage(isolated);
  };

  globalThis.__CHAT2API_EXTERNAL_ACCOUNT_TOOLS_DISABLED__ = Object.freeze({
    version: 48,
    policy: POLICY,
  });
})();
