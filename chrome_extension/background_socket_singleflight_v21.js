(() => {
  const KEY = "__CHAT2API_SOCKET_SINGLEFLIGHT_V21__";
  if (globalThis[KEY]) return;
  if (typeof connectSocket !== "function") return;

  const baseConnectSocket = connectSocket;
  const baseUpdateState = typeof updateState === "function" ? updateState : null;
  const baseScheduleReconnect = typeof scheduleReconnect === "function" ? scheduleReconnect : null;
  const state = { inFlight: null, starts: 0, joined: 0, staleStateDrops: 0, staleReconnectDrops: 0 };
  globalThis[KEY] = state;

  // background.js can replace a closing Worker socket before the old socket's
  // onclose callback is delivered. That stale callback used to overwrite the
  // new live connection with `disconnected`, clear its keepalive interval and
  // schedule a pointless reconnect. The server would still see the new socket
  // online while the popup stayed permanently red/disconnected.
  //
  // Keep the existing transport implementation, but make global status and
  // reconnect side effects authoritative to the socket currently referenced by
  // `socket`. Old callbacks call these functions by name, so this guard also
  // fixes an already-created socket from background.js' eager startup connect.
  if (baseUpdateState) {
    updateState = async function updateSocketStateOwnedV21(nextState, socketError = "") {
      const staleTerminalState = ["disconnected", "error"].includes(String(nextState || ""));
      if (staleTerminalState && typeof socketReady === "function" && socketReady()) {
        state.staleStateDrops += 1;
        // A stale onclose clears the global keepalive timer before calling
        // updateState. Re-arm it for the live replacement socket here.
        try {
          clearInterval(keepAliveTimer);
          keepAliveTimer = setInterval(() => trySendSocket({ type: "heartbeat", ts: Date.now() }), 20000);
        } catch (_) {}
        return baseUpdateState("connected", "");
      }
      return baseUpdateState(nextState, socketError);
    };
  }

  if (baseScheduleReconnect) {
    scheduleReconnect = function scheduleSocketReconnectOwnedV21(...args) {
      if (typeof socketReady === "function" && socketReady()) {
        state.staleReconnectDrops += 1;
        return;
      }
      return baseScheduleReconnect(...args);
    };
  }

  connectSocket = async function connectSocketSingleflightV21(...args) {
    if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) {
      if (socket.readyState === WebSocket.OPEN && baseUpdateState) await baseUpdateState("connected", "");
      return;
    }
    if (state.inFlight) {
      state.joined += 1;
      return state.inFlight;
    }

    state.starts += 1;
    state.inFlight = Promise.resolve()
      .then(() => baseConnectSocket(...args))
      .then(async result => {
        if (typeof socketReady === "function" && socketReady() && baseUpdateState) await baseUpdateState("connected", "");
        return result;
      })
      .finally(() => { state.inFlight = null; });
    return state.inFlight;
  };
})();