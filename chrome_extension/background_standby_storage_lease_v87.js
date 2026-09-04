(() => {
  const KEY = "__CHAT2API_STANDBY_STORAGE_LEASE_V87__";
  if (globalThis[KEY]) return;

  const WARM_KEY = "chat2apiConversationWarmPoolV2";
  const RESERVE_KEY = "chat2apiReservePoolV29";
  const state = { version: 87, warm_touches: 0, reserve_touches: 0, last_at_ms: 0 };
  globalThis[KEY] = state;

  const local = chrome?.storage?.local;
  if (!local || typeof local.get !== "function") return;
  const baseGet = local.get.bind(local);

  function requestsKey(keys, target) {
    if (keys === null || keys === undefined) return true;
    if (typeof keys === "string") return keys === target;
    if (Array.isArray(keys)) return keys.includes(target);
    return typeof keys === "object" && Object.prototype.hasOwnProperty.call(keys, target);
  }

  function touchWarm(value, now) {
    if (!value || typeof value !== "object") return value;
    const clone = { ...value };
    if (Array.isArray(value.slots)) {
      clone.slots = value.slots.map(slot => {
        if (!Number.isInteger(slot?.tab_id) || !Number.isInteger(slot?.window_id)) return slot;
        state.warm_touches += 1;
        return { ...slot, ready_at_ms: now, standby_lease_recovered_v87: true };
      });
    } else if (Number.isInteger(value.tab_id) && Number.isInteger(value.window_id)) {
      state.warm_touches += 1;
      clone.ready_at_ms = now;
      clone.standby_lease_recovered_v87 = true;
    }
    return clone;
  }

  function touchReserve(value, now) {
    if (!value || typeof value !== "object") return value;
    const clone = { ...value };
    if (Array.isArray(value.slots)) {
      clone.slots = value.slots.map(slot => {
        if (slot?.ready !== true || !Number.isInteger(slot?.tab_id) || !Number.isInteger(slot?.window_id)) return slot;
        state.reserve_touches += 1;
        return { ...slot, ready_at_ms: now, standby_lease_recovered_v87: true };
      });
    }
    return clone;
  }

  local.get = async function getWithStandbyLeaseRecovery(keys) {
    const result = await baseGet(keys);
    if (!result || typeof result !== "object") return result;
    const now = Date.now();
    let changed = false;
    const output = { ...result };

    // The old v39 freshness timestamp is not a reason to destroy a standby
    // browser window after an MV3 service-worker restart. Both warm/reserve
    // loaders still validate that the recorded ChatGPT tab exists, and v87's
    // request preflight validates/repairs the runtime before reuse. Recover the
    // lease timestamp here so a healthy physical window is adopted rather than
    // immediately closed and recreated solely because the worker slept >30 min.
    if (requestsKey(keys, WARM_KEY) && output[WARM_KEY]) {
      output[WARM_KEY] = touchWarm(output[WARM_KEY], now);
      changed = true;
    }
    if (requestsKey(keys, RESERVE_KEY) && output[RESERVE_KEY]) {
      output[RESERVE_KEY] = touchReserve(output[RESERVE_KEY], now);
      changed = true;
    }
    if (changed) state.last_at_ms = now;
    return output;
  };
})();
