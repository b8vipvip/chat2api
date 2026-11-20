/* W-04: resume a Web Chat turn by its original request_id.
 * This controller polls an owner-checked server snapshot. It NEVER resends
 * POST /v1/chat/completions and NEVER treats HTTP EOF as request completion.
 */
(() => {
  "use strict";

  const REQUEST_ID = /^req_[A-Za-z0-9_-]{8,120}$/;
  const MESSAGE_ID = /^[A-Za-z0-9_-]{8,100}$/;
  const TERMINAL = new Set(["completed", "error", "cancelled", "stalled"]);

  function createRequestId(cryptoApi) {
    const api = cryptoApi || (typeof window !== "undefined" ? window.crypto : null);
    if (api && typeof api.randomUUID === "function") return "req_" + api.randomUUID();
    const random = Math.random().toString(36).slice(2, 14);
    return "req_" + Date.now().toString(36) + "_" + random;
  }

  function create(options) {
    const config = options || {};
    const fetcher = config.fetcher || (typeof window !== "undefined" && window.fetch.bind(window));
    const AbortControllerImpl = config.AbortController || (typeof AbortController !== "undefined" ? AbortController : null);
    const notify = typeof config.onUpdate === "function" ? config.onUpdate : () => {};
    const notifyEvent = typeof config.onEvent === "function" ? config.onEvent : () => {};
    const baseDelay = Math.max(250, Math.min(10000, Number(config.pollIntervalMs) || 1000));
    const maxDuration = Math.max(1000, Math.min(30 * 60 * 1000, Number(config.maxDurationMs) || 15 * 60 * 1000));
    const maxReplayDuration = Math.max(1000, Math.min(30 * 60 * 1000, Number(config.maxReplayDurationMs) || maxDuration));
    let activeController = null;
    let activeReplayController = null;

    function emit(record, update) {
      try { notify(Object.freeze({
        request_id: record.request_id,
        assistant_message_id: record.assistant_message_id,
        ...update,
      })); } catch { /* UI observer failure must not change request truth. */ }
    }

    function pause(ms, signal) {
      return new Promise((resolve, reject) => {
        if (signal.aborted) {
          const error = new Error("Recovery polling aborted");
          error.name = "AbortError";
          reject(error);
          return;
        }
        const timer = setTimeout(done, ms);
        function done() {
          signal.removeEventListener("abort", aborted);
          resolve();
        }
        function aborted() {
          clearTimeout(timer);
          signal.removeEventListener("abort", aborted);
          const error = new Error("Recovery polling aborted");
          error.name = "AbortError";
          reject(error);
        }
        signal.addEventListener("abort", aborted, {once:true});
      });
    }

    async function resume(record) {
      if (!fetcher || !AbortControllerImpl) throw new Error("Recovery transport is unavailable");
      if (activeController) throw new Error("A recovery poll is already active");
      const requestId = String(record && record.request_id || "");
      const assistantId = String(record && record.assistant_message_id || "");
      if (!REQUEST_ID.test(requestId) || !MESSAGE_ID.test(assistantId)) {
        throw new TypeError("Invalid pending chat recovery record");
      }

      const controller = new AbortControllerImpl();
      activeController = controller;
      const startedAt = Date.now();
      let delay = baseDelay;
      emit({request_id:requestId, assistant_message_id:assistantId}, {
        status:"recovering", active:true, terminal:false,
      });
      const safeRecord = {request_id:requestId, assistant_message_id:assistantId};

      try {
        while (Date.now() - startedAt < maxDuration) {
          let response;
          try {
            response = await fetcher(
              "/api/user/chat/requests/" + encodeURIComponent(requestId) + "/recovery",
              {
                method:"GET",
                credentials:"same-origin",
                headers:{Accept:"application/json"},
                signal:controller.signal,
              }
            );
          } catch (error) {
            if (error && error.name === "AbortError") throw error;
            emit(safeRecord, {status:"reconnecting", active:true, terminal:false});
            await pause(delay, controller.signal);
            delay = Math.min(15000, delay * 2);
            continue;
          }

          if (response.status === 401 || response.status === 403) {
            const result = {status:"auth_required", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }
          if (response.status === 404) {
            const result = {status:"not_found", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }
          if (response.status === 429 || response.status >= 500) {
            emit(safeRecord, {status:"reconnecting", active:true, terminal:false});
            await pause(delay, controller.signal);
            delay = Math.min(15000, delay * 2);
            continue;
          }
          if (!response.ok) {
            const result = {status:"recovery_failed", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }

          let payload;
          try { payload = await response.json(); }
          catch {
            const result = {status:"invalid_response", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }
          if (!payload || payload.request_id !== requestId) {
            const result = {status:"request_mismatch", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }

          const status = String(payload.status || "unknown");
          if (TERMINAL.has(status)) {
            if (payload.terminal !== true) {
              const result = {status:"invalid_response", active:false, terminal:false};
              emit(safeRecord, result);
              return result;
            }
            if (status === "completed") {
              const hasText = payload.response_text_available === true &&
                typeof payload.response_text === "string";
              const result = {
                status:hasText ? "completed" : "completed_without_text",
                terminal:true,
                active:false,
                response_text:hasText ? payload.response_text : "",
              };
              emit(safeRecord, result);
              return result;
            }
            const result = {
              status, terminal:true, active:false,
              ...(status === "stalled" && payload.partial_text_available === true
                && typeof payload.partial_text === "string"
                ? {partial_text:payload.partial_text} : {}),
            };
            emit(safeRecord, result);
            return result;
          }
          if (payload.terminal === true) {
            const result = {status:"invalid_response", active:false, terminal:false};
            emit(safeRecord, result);
            return result;
          }
          if (payload.recoverable !== true) {
            const result = {status:"recovery_unavailable", terminal:false, active:false};
            emit(safeRecord, result);
            return result;
          }

          emit(safeRecord, {
            status:status === "queued" ? "queued" : "running",
            phase:String(payload.phase || ""),
            terminal:false,
            active:true,
          });
          const requestedDelay = Number(payload.retry_after_ms);
          delay = Number.isFinite(requestedDelay)
            ? Math.max(250, Math.min(10000, requestedDelay))
            : baseDelay;
          await pause(delay, controller.signal);
        }

        const result = {status:"recovery_timeout", terminal:false, active:false};
        emit(safeRecord, result);
        return result;
      } catch (error) {
        if (error && error.name === "AbortError") {
          const result = {status:"polling_stopped", terminal:false, active:false};
          emit(safeRecord, result);
          return result;
        }
        throw error;
      } finally {
        if (activeController === controller) activeController = null;
      }
    }


    async function replay(record, lastEventId = 0) {
      if (!fetcher || !AbortControllerImpl) throw new Error("Replay transport is unavailable");
      if (activeReplayController) throw new Error("An event replay is already active");
      const requestId = String(record && record.request_id || "");
      const assistantId = String(record && record.assistant_message_id || "");
      if (!REQUEST_ID.test(requestId) || !MESSAGE_ID.test(assistantId)) {
        throw new TypeError("Invalid pending chat replay record");
      }
      const cursor = Number.isSafeInteger(Number(lastEventId)) && Number(lastEventId) >= 0
        ? Number(lastEventId) : 0;
      const controller = new AbortControllerImpl();
      activeReplayController = controller;
      const safeRecord = {request_id:requestId, assistant_message_id:assistantId};
      let reader = null, terminal = null, buffer = "";
      const startedAt = Date.now();
      const timer = setTimeout(() => controller.abort(), maxReplayDuration);

      function dispatchFrame(frame) {
        const lines = String(frame || "").split(/\r?\n/);
        let eventName = "message", eventId = 0;
        const dataLines = [];
        for (const line of lines) {
          if (line.startsWith("id:")) {
            const value = Number(line.slice(3).trim());
            if (Number.isSafeInteger(value) && value >= 0) eventId = value;
          } else if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trimStart());
          }
        }
        if (!dataLines.length) return;
        let payload;
        try { payload = JSON.parse(dataLines.join("\n")); }
        catch { return; }
        try {
          notifyEvent(Object.freeze({
            ...safeRecord, id:eventId, event:eventName,
            data:payload && typeof payload === "object" ? payload : {},
          }));
        } catch { /* UI observer failure cannot change replay state. */ }

        if (eventName === "delivery.completed") {
          terminal = {status:"completed",terminal:true,active:false};
        } else if (eventName === "delivery.error" || eventName === "delivery.cancelled"
            || eventName === "delivery.stalled" || eventName === "recovery.stalled"
            || eventName === "recovery.terminal") {
          const status = String(payload.status || (
            eventName === "delivery.cancelled" ? "cancelled" :
            eventName === "delivery.error" ? "error" :
            eventName === "recovery.stalled" ? "stalled" : "unknown"
          ));
          terminal = {
            status,terminal:true,active:false,
            ...(typeof payload.partial_text === "string" ? {partial_text:payload.partial_text} : {}),
            ...(typeof payload.response_text === "string" ? {response_text:payload.response_text} : {}),
          };
        } else if (eventName === "replay.reset" && payload.terminal === true) {
          const status = String(payload.status || "unknown");
          terminal = {
            status,terminal:true,active:false,
            ...(typeof payload.partial_text === "string" ? {partial_text:payload.partial_text} : {}),
            ...(typeof payload.response_text === "string" ? {response_text:payload.response_text} : {}),
          };
        }
      }

      try {
        emit(safeRecord, {status:"replaying",active:true,terminal:false,last_event_id:cursor});
        let response;
        try {
          response = await fetcher(
            "/api/user/chat/requests/" + encodeURIComponent(requestId)
              + "/events?after=" + encodeURIComponent(String(cursor)),
            {
              method:"GET",
              credentials:"same-origin",
              headers:{Accept:"text/event-stream","Last-Event-ID":String(cursor)},
              signal:controller.signal,
            }
          );
        } catch (error) {
          if (error && error.name === "AbortError") {
            return {status:"replay_timeout",terminal:false,active:false};
          }
          return {status:"replay_unavailable",terminal:false,active:false};
        }
        if (response.status === 401 || response.status === 403) {
          return {status:"auth_required",terminal:false,active:false};
        }
        if (response.status === 404) {
          return {status:"not_found",terminal:false,active:false};
        }
        if (response.status === 429 || response.status >= 500 || !response.ok) {
          return {status:"replay_unavailable",terminal:false,active:false};
        }
        if (!response.body || typeof response.body.getReader !== "function") {
          return {status:"replay_unavailable",terminal:false,active:false};
        }
        reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        while (!terminal && Date.now() - startedAt < maxReplayDuration) {
          let part;
          try { part = await reader.read(); }
          catch (error) {
            if (error && error.name === "AbortError") {
              return {status:"replay_timeout",terminal:false,active:false};
            }
            return {status:"replay_interrupted",terminal:false,active:false};
          }
          if (part.done) break;
          buffer += decoder.decode(part.value,{stream:true});
          let boundary;
          while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
            const frame = buffer.slice(0,boundary.index);
            buffer = buffer.slice(boundary.index + boundary[0].length);
            dispatchFrame(frame);
            if (terminal) break;
          }
        }
        if (terminal) {
          emit(safeRecord, terminal);
          return terminal;
        }
        return {
          status:Date.now() - startedAt >= maxReplayDuration ? "replay_timeout" : "replay_interrupted",
          terminal:false,active:false,
        };
      } finally {
        clearTimeout(timer);
        if (reader) {
          try { await reader.cancel(); } catch {}
          try { reader.releaseLock(); } catch {}
        }
        if (activeReplayController === controller) activeReplayController = null;
      }
    }

    async function cancel(record) {
      if (!fetcher) throw new Error("Recovery transport is unavailable");
      const requestId = String(record && record.request_id || "");
      const assistantId = String(record && record.assistant_message_id || "");
      if (!REQUEST_ID.test(requestId) || !MESSAGE_ID.test(assistantId)) {
        throw new TypeError("Invalid pending chat recovery record");
      }
      const safeRecord = {request_id:requestId, assistant_message_id:assistantId};
      let response;
      try {
        response = await fetcher(
          "/api/user/chat/requests/" + encodeURIComponent(requestId) + "/cancel",
          {
            method:"POST",
            credentials:"same-origin",
            headers:{Accept:"application/json"},
          }
        );
      } catch {
        const result = {status:"cancel_unavailable", active:true, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (response.status === 401 || response.status === 403) {
        const result = {status:"auth_required", active:false, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (response.status === 404) {
        const result = {status:"not_found", active:false, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (response.status === 409 || response.status === 429 || response.status >= 500) {
        const result = {status:"cancel_unavailable", active:true, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (!response.ok) {
        const result = {status:"cancel_failed", active:true, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      let payload;
      try { payload = await response.json(); }
      catch {
        const result = {status:"invalid_response", active:true, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (!payload || payload.request_id !== requestId) {
        const result = {status:"request_mismatch", active:false, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      if (payload.terminal === true) {
        const result = {
          status:TERMINAL.has(String(payload.status || "")) ? String(payload.status) : "invalid_response",
          active:false,
          terminal:TERMINAL.has(String(payload.status || "")),
          cancel_requested:false,
          queued:payload.queued === true,
        };
        emit(safeRecord, result);
        return result;
      }
      if (payload.status === "finalizing" && payload.already_terminal === true) {
        const result = {
          status:"finalizing",
          active:true,
          terminal:false,
          cancel_requested:false,
        };
        emit(safeRecord, result);
        return result;
      }
      if (payload.cancel_requested !== true) {
        const result = {status:"cancel_unavailable", active:true, terminal:false, cancel_requested:false};
        emit(safeRecord, result);
        return result;
      }
      const result = {
        status:"cancellation_requested",
        active:true,
        terminal:false,
        cancel_requested:true,
        already_requested:payload.already_requested === true,
        queued:payload.queued === true,
      };
      emit(safeRecord, result);
      return result;
    }

    function stop() {
      if (activeController) activeController.abort();
      if (activeReplayController) activeReplayController.abort();
    }

    return Object.freeze({resume, replay, cancel, stop});
  }

  Object.defineProperty(window, "Chat2APIStreamRecoveryV1", {
    value:Object.freeze({create, createRequestId, isRequestId:value => REQUEST_ID.test(String(value || ""))}),
    writable:false,
    configurable:false,
  });
})();
