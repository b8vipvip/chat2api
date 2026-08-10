(() => {
  const oldQuality = quality;
  const oldOneTest = oneTest;
  const oldFillModels = fillModels;

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.6";

  fillModels = function fillModelsV6() {
    oldFillModels();
    const select = $("testModel");
    if (!select) return;
    [...select.options].forEach(option => {
      if (/^gpt-live(?:-|$)/i.test(option.value)) option.remove();
    });
  };

  function b64ToFile(name, mime, base64) {
    const raw = atob(String(base64 || ""));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return new File([bytes], name, { type: mime || "audio/webm", lastModified: Date.now() });
  }

  function audioResult(kind, label, response, started) {
    const audio = response?.audio || {};
    const transcript = String(response?.transcript || "");
    const b64 = String(audio.b64_json || "");
    if (!b64 || b64.length < 100) throw new Error("GPT-Live 没有返回可用音频数据");
    return {
      kind,
      label,
      status: "passed",
      message: `调用完成 · ${audio.mime_type || "audio"} · ${Math.round(b64.length * 0.75)} bytes`,
      first_token_ms: response?.chat2api?.timings?.first_token_ms ?? null,
      total_ms: performance.now() - started,
      response_chars: transcript.length,
      audio_bytes: Math.round(b64.length * 0.75),
      transcript,
      meta: { usage: response?.usage, chat2api: response?.chat2api },
    };
  }

  quality = function qualityV6(result) {
    const base = oldQuality(result);
    if (!result || result.status === "failed" || result.status === "skipped") return base;
    const diagnostics = result.meta?.chat2api?.diagnostics || {};
    const issues = [...(base.issues || [])];
    if ((result.kind === "vision" || result.kind === "file") && diagnostics.ui_error_detected) {
      const retries = Number(diagnostics.ui_retry_count || 0);
      issues.push(retries ? `ChatGPT 页面发生错误并自动重试 ${retries} 次` : "ChatGPT 页面检测到错误状态");
    }
    if ((result.kind === "vision" || result.kind === "file") && Number(diagnostics.attachments_count || 0) < 1) {
      issues.push("浏览器诊断未确认附件已注入 ChatGPT");
    }
    if ((result.kind === "voice_generation" || result.kind === "voice_conversation") && !result.audio_bytes) {
      issues.push("没有捕获到 GPT-Live 音频");
    }
    if ((result.kind === "voice_generation" || result.kind === "voice_conversation") && !diagnostics.remote_track_seen) {
      issues.push("未确认 GPT-Live WebRTC 远端音轨");
    }
    return { grade: issues.length ? "warning" : "passed", issues };
  };

  oneTest = async function oneTestV6(kind, model, files, testToken) {
    if (kind !== "voice_generation" && kind !== "voice_conversation") {
      const result = await oldOneTest(kind, model, files, testToken);
      result.quality = quality(result);
      if (result.status === "passed" && result.quality.grade === "warning") result.status = "warning";
      return result;
    }

    const label = kind === "voice_generation" ? "语音生成" : "语音对话";
    const started = performance.now();
    try {
      if (kind === "voice_generation") {
        const response = await api("/v1/audio/speech", {
          method: "POST",
          key: testToken,
          body: {
            model: "gpt-live",
            input: "请只用一句简短中文回复：chat2api GPT Live 语音生成测试成功",
            response_format: "b64_json",
            timeout: 180,
          },
        });
        const result = audioResult(kind, label, response, started);
        result.quality = quality(result);
        if (result.quality.grade === "warning") result.status = "warning";
        return result;
      }

      let audioFile = files.find(file => String(file.type || "").startsWith("audio/"));
      let seed = null;
      if (!audioFile) {
        seed = await api("/v1/audio/speech", {
          method: "POST",
          key: testToken,
          body: {
            model: "gpt-live",
            input: "请清楚地说：这是 chat2api 语音输入回环测试",
            response_format: "b64_json",
            timeout: 180,
          },
        });
        audioFile = b64ToFile(
          "chat2api-voice-loopback.webm",
          seed.audio?.mime_type || "audio/webm",
          seed.audio?.b64_json || "",
        );
      }
      const uploaded = await upload(audioFile, "voice-input", testToken);
      const response = await api("/v1/audio/conversations", {
        method: "POST",
        key: testToken,
        body: {
          model: "gpt-live",
          audio_file_id: uploaded.id,
          response_format: "b64_json",
          timeout: 180,
        },
      });
      const result = audioResult(kind, label, response, started);
      result.loopback_seed_generated = Boolean(seed);
      result.input_audio_name = audioFile.name;
      result.quality = quality(result);
      if (result.quality.grade === "warning") result.status = "warning";
      return result;
    } catch (error) {
      const result = {
        kind,
        label,
        status: "failed",
        error: String(error?.message || error),
        message: String(error?.message || error),
        total_ms: performance.now() - started,
      };
      result.quality = quality(result);
      return result;
    }
  };

  function addVoiceDocs() {
    const doc = document.querySelector("#view-docs .panel");
    if (!doc || doc.querySelector("[data-chat2api-v6-voice-docs]")) return;
    const block = document.createElement("div");
    block.dataset.chat2apiV6VoiceDocs = "1";
    block.innerHTML = `
      <h2>GPT-Live 语音生成</h2>
      <div class="codebox">POST ${location.origin}/v1/audio/speech\nAuthorization: Bearer YOUR_API_KEY\n\n{\n  "model":"gpt-live",\n  "input":"请用中文介绍一下你自己",\n  "response_format":"b64_json"\n}</div>
      <h2>GPT-Live 单轮语音对话</h2>
      <p>先通过 <code>POST /v1/files</code> 上传 <code>audio/*</code> 文件，再把 <code>file_id</code> 传给语音对话接口。扩展会把音频注入 ChatGPT Voice 的麦克风 MediaStream，并录制远端 GPT-Live 回答。</p>
      <div class="codebox">POST ${location.origin}/v1/audio/conversations\nAuthorization: Bearer YOUR_API_KEY\n\n{\n  "model":"gpt-live",\n  "audio_file_id":"file_xxx",\n  "response_format":"b64_json"\n}</div>
      <p class="muted">当前 v0.6 实现单轮语音生成和单轮语音对话。真正持续双向、可打断的全双工 WebSocket Live 会话留在后续版本。</p>`;
    doc.appendChild(block);
  }

  const oldShow = show;
  show = function showV6(view) {
    oldShow(view);
    if (view === "docs") setTimeout(addVoiceDocs, 0);
  };

  const subtitle = document.querySelector("#view-playground .panel > .muted");
  if (subtitle) subtitle.textContent = "执行标准化用例并生成质量报告。视觉测试会识别 ChatGPT 页面错误和自动重试；语音测试使用 GPT-Live 的真实 WebRTC 音轨。";

  if (location.hash === "#docs") addVoiceDocs();
})();
