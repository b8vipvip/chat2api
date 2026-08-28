(() => {
  if (globalThis.__CHAT2API_LINUX_WORKER_POLL_GUARD_V22_27__) return;
  globalThis.__CHAT2API_LINUX_WORKER_POLL_GUARD_V22_27__ = true;

  const nativeFetch = globalThis.fetch.bind(globalThis);
  const TARGET = "/api/admin/linux-worker-installations";
  const CACHE_MS = 750;
  const TIMEOUT_MS = 8000;
  let inflight = null;
  let cached = null;
  let cachedAt = 0;

  const snapshot = async response => ({
    body: await response.arrayBuffer(),
    status: response.status,
    statusText: response.statusText,
    headers: [...response.headers.entries()],
  });

  const responseFrom = value => new Response(value.body.slice(0), {
    status: value.status,
    statusText: value.statusText,
    headers: value.headers,
  });

  const targetRequest = (input, init = {}) => {
    try {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      const method = String(init.method || (typeof input !== "string" && input.method) || "GET").toUpperCase();
      return method === "GET" && url.origin === location.origin && url.pathname === TARGET;
    } catch (_) {
      return false;
    }
  };

  const fetchWorkerRows = async (input, init = {}) => {
    const now = Date.now();
    if (cached && now - cachedAt < CACHE_MS) return responseFrom(cached);
    if (!inflight) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort("linux-worker-console-timeout"), TIMEOUT_MS);
      inflight = nativeFetch(input, {...init, signal: controller.signal})
        .then(snapshot)
        .then(value => {
          cached = value;
          cachedAt = Date.now();
          return value;
        })
        .finally(() => {
          clearTimeout(timer);
          inflight = null;
        });
    }
    return responseFrom(await inflight);
  };

  globalThis.fetch = (input, init = {}) => {
    if (!targetRequest(input, init)) return nativeFetch(input, init);
    return fetchWorkerRows(input, init);
  };
})();
