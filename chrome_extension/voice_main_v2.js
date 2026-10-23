(() => {
  const KEY = "__CHAT2API_VOICE_MAIN_STOP_V2__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  function post(requestId, type, data = {}) {
    window.postMessage({ source: "chat2api-main", type, request_id: requestId, ...data }, "*");
  }

  async function stopSyntheticMic(requestId) {
    const state = globalThis.__CHAT2API_VOICE_MAIN_V1__;
    if (!state) {
      post(requestId, "voice.mic.stopped", { stopped: false, reason: "voice-main-state-missing" });
      return;
    }
    let stoppedTracks = 0;
    // Detach exactly the mic owned by this session *before* any await.
    // A second Voice request can start while close() is pending; a late old
    // callback must not null or close the new request's microphone.
    const destination = state.micDestination;
    const oscillator = state.micOscillator;
    const context = state.micContext;
    state.micDestination = null;
    state.micOscillator = null;
    state.micContext = null;
    try {
      for (const track of destination?.stream?.getTracks?.() || []) {
        try { track.stop(); stoppedTracks += 1; } catch (_) {}
      }
      try { oscillator?.stop?.(); } catch (_) {}
      try { await context?.close?.(); } catch (_) {}
      post(requestId, "voice.mic.stopped", { stopped: true, stopped_tracks: stoppedTracks });
    } catch (error) {
      post(requestId, "voice.mic.stop.error", { error: String(error?.message || error), stopped_tracks: stoppedTracks });
    }
  }

  window.addEventListener("message", event => {
    if (event.source !== window || event.data?.source !== "chat2api-isolated") return;
    if (event.data?.type !== "voice.mic.stop") return;
    stopSyntheticMic(event.data.request_id || null);
  });
})();
