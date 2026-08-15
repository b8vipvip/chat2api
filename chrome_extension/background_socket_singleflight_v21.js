(() => {
  const KEY = "__CHAT2API_SOCKET_SINGLEFLIGHT_V21__";
  if (globalThis[KEY]) return;
  if (typeof connectSocket !== "function") return;

  const baseConnectSocket = connectSocket;
  const state = { inFlight: null, starts: 0, joined: 0 };
  globalThis[KEY] = state;

  connectSocket = async function connectSocketSingleflightV21(...args) {
    if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
    if (state.inFlight) {
      state.joined += 1;
      return state.inFlight;
    }

    state.starts += 1;
    state.inFlight = Promise.resolve()
      .then(() => baseConnectSocket(...args))
      .finally(() => { state.inFlight = null; });
    return state.inFlight;
  };
})();
