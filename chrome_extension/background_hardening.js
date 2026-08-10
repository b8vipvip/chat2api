(() => {
  let observedSocket = null;
  async function recoverRejectedToken(event) {
    if (event.code !== 4401) return;
    clearTimeout(reconnectTimer);
    socket = null;
    await chrome.storage.local.set({
      clientId: "",
      clientToken: "",
      socketState: "unpaired",
      socketError: "Extension token was rejected. Pair the extension with the server again.",
    });
  }
  function attach() {
    if (!socket || socket === observedSocket) return;
    observedSocket = socket;
    socket.addEventListener("close", event => recoverRejectedToken(event).catch(console.warn), { once: true });
  }
  attach();
  setInterval(attach, 250);
})();
