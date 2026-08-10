(() => {
  const priorOneTest = oneTest;
  const priorQuality = quality;
  const priorFillModels = fillModels;

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.7";

  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const labelMap = {
    text: "文本",
    vision: "视觉理解",
    file: "文件理解",
    image_generation: "图片生成",
    voice_generation: "语音生成",
    voice_conversation: "语音对话",
    dictation: "听写 / 语音转文字",
  };

  function simplifyRequestPage() {
    const section = document.querySelector("#view-requests");
    if (!section) return;
    const split = section.querySelector(".split");
    if (split) split.classList.remove("split");
    section.querySelector(".panel.detail")?.remove();
  }

  simplifyRequestPage();

  loadRequests = async function loadRequestsV7() {
    if (!key()) return;
    try {
      const p = new URLSearchParams({ limit: "100" });
      if ($("rqSearch").value.trim()) p.set("q", $("rqSearch").value.trim());
      if ($("rqStatus").value) p.set("status", $("rqStatus").value);
      if ($("rqModel").value.trim()) p.set("model", $("rqModel").value.trim());
      const d = await api("/api/admin/requests?" + p);
      $("rqBody").innerHTML = (d.data || []).map(r => `
        <tr>
          <td>${fmtTime(r.recorded_at)}</td>
          <td>${esc(r.request_type || "text")}</td>
          <td>${pill(r.status)}</td>
          <td>${esc(r.api_key_name || "-")}</td>
          <td><code>${esc(r.requested_model)}</code></td>
          <td>${r.attachments_count ?? 0}</td>
          <td>${fmtMs(r.timings?.first_token_ms)}</td>
          <td>${fmtMs(r.timings?.total_ms)}</td>
          <td>${r.usage?.total_tokens ?? 0}</td>
        </tr>`).join("") || '<tr><td colspan="9" class="muted">暂无请求记录</td></tr>';
    } catch (error) {
      status(error.message, "bad");
    }
  };
  if ($("rqGo")) $("rqGo").onclick = loadRequests;

  const typeSelect = $("testType");
  if (typeSelect && ![...typeSelect.options].some(option => option.value === "dictation")) {
    const option = document.createElement("option");
    option.value = "dictation";
    option.textContent = "听写 / 语音转文字";
    const all = [...typeSelect.options].find(item => item.value === "all");
    typeSelect.insertBefore(option, all || null);
  }

  const attachmentInput = $("testFiles");
  if (attachmentInput) {
    const label = attachmentInput.closest("div")?.querySelector("label");
    if (label) label.textContent = "测试附件（可多选：图片 / 视频 / PDF / 文档 / 音频；留空自动生成默认样本）";
  }

  fillModels = function fillModelsV7() {
    priorFillModels();
    const select = $("testModel");
    if (!select) return;
    [...select.options].forEach(option => {
      if (/^gpt-(?:live|dictation)(?:-|$)/i.test(option.value)) option.remove();
    });
  };

  function randomCode(prefix) {
    return `${prefix}-${Date.now().toString(36).slice(-5).toUpperCase()}-${Math.random().toString(36).slice(2, 5).toUpperCase()}`;
  }

  function canvasBlob(canvas, type = "image/png", qualityValue) {
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Canvas 没有生成测试图片")), type, qualityValue));
  }

  async function makeDefaultImage() {
    const code = randomCode("VISION");
    const canvas = document.createElement("canvas");
    canvas.width = 720;
    canvas.height = 420;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("浏览器 Canvas 不可用");
    const h1 = Math.floor(Math.random() * 360);
    const h2 = (h1 + 120 + Math.floor(Math.random() * 80)) % 360;
    const gradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
    gradient.addColorStop(0, `hsl(${h1} 72% 58%)`);
    gradient.addColorStop(1, `hsl(${h2} 72% 45%)`);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(255,255,255,.92)";
    ctx.fillRect(45, 45, 630, 330);
    ctx.fillStyle = "#111";
    ctx.font = "700 42px sans-serif";
    ctx.fillText("chat2api VISION TEST", 85, 130);
    ctx.font = "700 34px monospace";
    ctx.fillText(code, 85, 195);
    ctx.fillStyle = `hsl(${(h1 + 220) % 360} 78% 48%)`;
    ctx.beginPath();
    ctx.arc(160, 285, 55, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `hsl(${(h2 + 70) % 360} 78% 48%)`;
    ctx.fillRect(285, 235, 120, 100);
    ctx.fillStyle = "#111";
    ctx.font = "24px sans-serif";
    ctx.fillText("圆形 + 方形", 455, 292);
    const blob = await canvasBlob(canvas, "image/png");
    return { file: new File([blob], `${code}.png`, { type: "image/png", lastModified: Date.now() }), code };
  }

  function preferredVideoMime() {
    const values = ["video/webm;codecs=vp8", "video/webm"];
    return values.find(value => globalThis.MediaRecorder?.isTypeSupported?.(value)) || "";
  }

  async function makeDefaultVideo() {
    if (!globalThis.MediaRecorder) throw new Error("当前管理浏览器不支持 MediaRecorder，无法生成默认视频");
    const code = randomCode("VIDEO");
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 360;
    const ctx = canvas.getContext("2d");
    if (!ctx || !canvas.captureStream) throw new Error("当前浏览器不支持 Canvas 视频样本生成");
    const stream = canvas.captureStream(12);
    const mime = preferredVideoMime();
    const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    const chunks = [];
    recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
    const stopped = new Promise((resolve, reject) => {
      recorder.onstop = resolve;
      recorder.onerror = event => reject(event.error || new Error("测试视频录制失败"));
    });
    recorder.start(200);
    const frames = 30;
    for (let i = 0; i < frames; i += 1) {
      ctx.fillStyle = `hsl(${(i * 11) % 360} 58% 38%)`;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "white";
      ctx.font = "700 36px sans-serif";
      ctx.fillText("chat2api VIDEO TEST", 58, 82);
      ctx.font = "700 28px monospace";
      ctx.fillText(code, 58, 125);
      const x = 70 + (i / (frames - 1)) * 500;
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(x, 230, 38, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#111";
      ctx.font = "22px monospace";
      ctx.fillText(`frame ${String(i + 1).padStart(2, "0")}`, 240, 320);
      await pause(85);
    }
    recorder.stop();
    await stopped;
    stream.getTracks().forEach(track => track.stop());
    const blob = new Blob(chunks, { type: recorder.mimeType || mime || "video/webm" });
    if (blob.size < 500) throw new Error("默认测试视频数据为空");
    return { file: new File([blob], `${code}.webm`, { type: blob.type || "video/webm", lastModified: Date.now() }), code };
  }

  function makePdfBlob(text) {
    const escapePdf = value => String(value).replace(/([\\()])/g, "\\$1");
    const stream = `BT /F1 16 Tf 72 720 Td (${escapePdf(text)}) Tj ET`;
    const objects = [
      "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
      "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
      "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
      `4 0 obj << /Length ${stream.length} >> stream\n${stream}\nendstream endobj\n`,
      "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ];
    let body = "%PDF-1.4\n";
    const offsets = [0];
    for (const object of objects) {
      offsets.push(body.length);
      body += object;
    }
    const xref = body.length;
    body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    for (let i = 1; i <= objects.length; i += 1) body += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
    body += `trailer << /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return new Blob([body], { type: "application/pdf" });
  }

  function makeDefaultDocuments() {
    const code = randomCode("DOC");
    const now = new Date().toISOString();
    return {
      code,
      files: [
        new File([
          `chat2api default TXT sample\nTest code: TXT-${code}\nCreated: ${now}\nCore topic: file understanding can read plain text.\n`,
        ], `chat2api-${code}.txt`, { type: "text/plain" }),
        new File([
          `name,value,test_code\nalpha,17,CSV-${code}\nbeta,29,CSV-${code}\n`,
        ], `chat2api-${code}.csv`, { type: "text/csv" }),
        new File([
          JSON.stringify({ product: "chat2api", purpose: "file understanding test", test_code: `JSON-${code}`, values: [7, 4, 2] }, null, 2),
        ], `chat2api-${code}.json`, { type: "application/json" }),
        new File([
          makePdfBlob(`chat2api PDF sample. Test code PDF-${code}. File understanding test.`),
        ], `chat2api-${code}.pdf`, { type: "application/pdf" }),
      ],
    };
  }

  async function defaultAudioFile() {
    const response = await fetch("/assets/chat2api-test-dictation.mp3", { cache: "no-store" });
    if (!response.ok) throw new Error(`默认测试语音下载失败：HTTP ${response.status}`);
    const blob = await response.blob();
    return new File([blob], "chat2api-dictation-test.mp3", { type: "audio/mpeg", lastModified: Date.now() });
  }

  async function visualSubtest(file, model, testToken) {
    const started = performance.now();
    try {
      const uploaded = await upload(file, "vision", testToken);
      const isVideo = String(file.type || "").startsWith("video/");
      const result = await streamChat(
        model,
        isVideo
          ? "请分析附件视频。如果可以读取视频，请描述画面中的测试文字、移动物体和主要变化；如果当前 ChatGPT 不支持该视频附件，请明确说明不支持。"
          : "请识别附件图片，并描述其中的测试代码、主要文字、圆形和方形。",
        [{ file_id: uploaded.id }],
        testToken,
      );
      return { kind: isVideo ? "video" : "image", name: file.name, status: "passed", ...result };
    } catch (error) {
      return {
        kind: String(file.type || "").startsWith("video/") ? "video" : "image",
        name: file.name,
        status: "failed",
        error: String(error?.message || error),
        total_ms: performance.now() - started,
      };
    }
  }

  async function visionTest(model, files, testToken) {
    const started = performance.now();
    const supplied = files.filter(file => /^(image|video)\//.test(String(file.type || "")));
    const generated = supplied.length === 0;
    const assets = [];
    const generationIssues = [];
    const codes = [];
    if (generated) {
      const image = await makeDefaultImage();
      assets.push(image.file);
      codes.push(image.code);
      try {
        const video = await makeDefaultVideo();
        assets.push(video.file);
        codes.push(video.code);
      } catch (error) {
        generationIssues.push(String(error?.message || error));
      }
    } else {
      const firstImage = supplied.find(file => String(file.type || "").startsWith("image/"));
      const firstVideo = supplied.find(file => String(file.type || "").startsWith("video/"));
      if (firstImage) assets.push(firstImage);
      if (firstVideo) assets.push(firstVideo);
      if (!assets.length) assets.push(supplied[0]);
    }

    const subtests = [];
    for (const file of assets) subtests.push(await visualSubtest(file, model, testToken));
    const passed = subtests.filter(item => item.status === "passed");
    const failed = subtests.filter(item => item.status === "failed");
    const result = {
      kind: "vision",
      label: "视觉理解",
      status: passed.length ? ((failed.length || generationIssues.length) ? "warning" : "passed") : "failed",
      message: `${passed.length}/${subtests.length} 个视觉样本调用成功${generated ? " · 使用自动生成图片/视频" : ""}`,
      total_ms: performance.now() - started,
      first_token_ms: passed.map(item => item.first_token_ms).filter(value => value != null).sort((a, b) => a - b)[0] ?? null,
      response_chars: passed.reduce((sum, item) => sum + Number(item.response_chars || 0), 0),
      meta: passed[0]?.meta || null,
      default_assets_generated: generated,
      default_asset_codes: codes,
      subtests,
    };
    const issues = [
      ...generationIssues.map(text => `默认视频生成失败：${text}`),
      ...failed.map(item => `${item.kind === "video" ? "视频" : "图片"}子测试失败：${item.error}`),
    ];
    result.quality = { grade: result.status === "failed" ? "failed" : issues.length ? "warning" : "passed", issues };
    return result;
  }

  async function fileTest(model, files, testToken) {
    const started = performance.now();
    let selected = files.filter(file => !/^(image|video|audio)\//.test(String(file.type || ""))).slice(0, 4);
    let generated = false;
    let bundleCode = null;
    if (!selected.length) {
      const defaults = makeDefaultDocuments();
      selected = defaults.files;
      bundleCode = defaults.code;
      generated = true;
    }
    try {
      const attachments = [];
      for (const file of selected) {
        const uploaded = await upload(file, "file-understanding", testToken);
        attachments.push({ file_id: uploaded.id });
      }
      const response = await streamChat(
        model,
        "请逐个阅读所有附件，列出每个文件名/文件类型，并概括核心内容；如果里面有 Test code 或 test_code，请同时列出来。",
        attachments,
        testToken,
      );
      const diagnostics = response.meta?.chat2api?.diagnostics || {};
      const issues = [];
      if (Number(diagnostics.attachments_count || 0) !== attachments.length) {
        issues.push(`计划上传 ${attachments.length} 个文件，但浏览器诊断确认 ${diagnostics.attachments_count || 0} 个`);
      }
      return {
        kind: "file",
        label: "文件理解",
        status: issues.length ? "warning" : "passed",
        message: `调用完成 · ${selected.length} 个${generated ? "默认" : "用户"}文件`,
        ...response,
        total_ms: performance.now() - started,
        default_assets_generated: generated,
        default_bundle_code: bundleCode,
        tested_files: selected.map(file => ({ name: file.name, type: file.type, bytes: file.size })),
        quality: { grade: issues.length ? "warning" : "passed", issues },
      };
    } catch (error) {
      const result = {
        kind: "file",
        label: "文件理解",
        status: "failed",
        error: String(error?.message || error),
        message: String(error?.message || error),
        total_ms: performance.now() - started,
        default_assets_generated: generated,
        tested_files: selected.map(file => ({ name: file.name, type: file.type, bytes: file.size })),
      };
      result.quality = { grade: "failed", issues: [`调用失败：${result.error}`] };
      return result;
    }
  }

  function voiceResult(kind, label, response, started, inputAudioName = null, defaultAudio = false) {
    const audio = response?.audio || {};
    const transcript = String(response?.transcript || "");
    const b64 = String(audio.b64_json || "");
    if (!b64 || b64.length < 100) throw new Error("GPT-Live 没有返回可用音频数据");
    const bytes = Math.round(b64.length * 0.75);
    const diagnostics = response?.chat2api?.diagnostics || {};
    const issues = [];
    if (!diagnostics.remote_track_seen) issues.push("未确认 GPT-Live WebRTC 远端音轨");
    if (!transcript) issues.push("已捕获 Voice 音频，但当前 Voice 页面未同步提供文字转写");
    return {
      kind,
      label,
      status: issues.length ? "warning" : "passed",
      message: `调用完成 · ${audio.mime_type || "audio"} · ${bytes} bytes`,
      first_token_ms: response?.chat2api?.timings?.first_token_ms ?? null,
      total_ms: performance.now() - started,
      response_chars: transcript.length,
      audio_bytes: bytes,
      transcript,
      input_audio_name: inputAudioName,
      default_audio_generated: defaultAudio,
      meta: { usage: response?.usage, chat2api: response?.chat2api },
      quality: { grade: issues.length ? "warning" : "passed", issues },
    };
  }

  async function voiceGenerationTest(testToken) {
    const started = performance.now();
    try {
      const response = await api("/v1/audio/speech", {
        method: "POST",
        key: testToken,
        body: {
          model: "gpt-live",
          input: "请只用一句简短中文回复：chat2api GPT Live 语音生成测试成功",
          response_format: "b64_json",
          timeout: 90,
        },
      });
      return voiceResult("voice_generation", "语音生成", response, started);
    } catch (error) {
      return {
        kind: "voice_generation",
        label: "语音生成",
        status: "failed",
        error: String(error?.message || error),
        message: String(error?.message || error),
        total_ms: performance.now() - started,
        quality: { grade: "failed", issues: [`调用失败：${String(error?.message || error)}`] },
      };
    }
  }

  async function voiceConversationTest(files, testToken) {
    const started = performance.now();
    try {
      let audioFile = files.find(file => String(file.type || "").startsWith("audio/"));
      let usingDefault = false;
      if (!audioFile) {
        audioFile = await defaultAudioFile();
        usingDefault = true;
      }
      const uploaded = await upload(audioFile, "voice-input", testToken);
      const response = await api("/v1/audio/conversations", {
        method: "POST",
        key: testToken,
        body: {
          model: "gpt-live",
          audio_file_id: uploaded.id,
          instruction: "请用一句简短中文回应你听到的内容。",
          response_format: "b64_json",
          timeout: 90,
        },
      });
      return voiceResult("voice_conversation", "语音对话", response, started, audioFile.name, usingDefault);
    } catch (error) {
      return {
        kind: "voice_conversation",
        label: "语音对话",
        status: "failed",
        error: String(error?.message || error),
        message: String(error?.message || error),
        total_ms: performance.now() - started,
        quality: { grade: "failed", issues: [`调用失败：${String(error?.message || error)}`] },
      };
    }
  }

  async function dictationTest(files, testToken) {
    const started = performance.now();
    try {
      let audioFile = files.find(file => String(file.type || "").startsWith("audio/"));
      let usingDefault = false;
      if (!audioFile) {
        audioFile = await defaultAudioFile();
        usingDefault = true;
      }
      const uploaded = await upload(audioFile, "voice-input", testToken);
      const response = await api("/v1/audio/transcriptions", {
        method: "POST",
        key: testToken,
        body: {
          model: "gpt-dictation",
          audio_file_id: uploaded.id,
          timeout: 90,
        },
      });
      const text = String(response?.text || "").trim();
      if (!text) throw new Error("听写接口没有返回文字");
      const issues = [];
      if (usingDefault) {
        const normalized = text.toLowerCase();
        const hits = ["chat", "api", "seven", "four", "two", "742"].filter(token => normalized.includes(token)).length;
        if (hits < 2) issues.push(`默认语音已转写，但与预期测试短语的匹配度偏低：${text}`);
      }
      return {
        kind: "dictation",
        label: "听写 / 语音转文字",
        status: issues.length ? "warning" : "passed",
        message: `转写完成 · ${text.length} 字符`,
        total_ms: performance.now() - started,
        response_chars: text.length,
        transcript: text,
        input_audio_name: audioFile.name,
        default_audio_generated: usingDefault,
        meta: { usage: response?.usage, chat2api: response?.chat2api },
        quality: { grade: issues.length ? "warning" : "passed", issues },
      };
    } catch (error) {
      return {
        kind: "dictation",
        label: "听写 / 语音转文字",
        status: "failed",
        error: String(error?.message || error),
        message: String(error?.message || error),
        total_ms: performance.now() - started,
        quality: { grade: "failed", issues: [`调用失败：${String(error?.message || error)}`] },
      };
    }
  }

  quality = function qualityV7(result) {
    if (result?.quality?.grade && ["vision", "file", "voice_generation", "voice_conversation", "dictation"].includes(result.kind)) return result.quality;
    const value = priorQuality(result);
    if (["voice_generation", "voice_conversation"].includes(result?.kind) && result?.audio_bytes) {
      value.issues = (value.issues || []).filter(issue => !/没有捕获到文本输出/.test(issue));
      if (!result.transcript) value.issues.push("已捕获语音，但页面未同步提供文字转写");
      value.grade = value.issues.length ? "warning" : "passed";
    }
    return value;
  };

  oneTest = async function oneTestV7(kind, model, files, testToken) {
    if (kind === "vision") return visionTest(model, files, testToken);
    if (kind === "file") return fileTest(model, files, testToken);
    if (kind === "voice_generation") return voiceGenerationTest(testToken);
    if (kind === "voice_conversation") return voiceConversationTest(files, testToken);
    if (kind === "dictation") return dictationTest(files, testToken);
    const result = await priorOneTest(kind, model, files, testToken);
    result.quality = quality(result);
    if (result.status === "passed" && result.quality.grade === "warning") result.status = "warning";
    return result;
  };

  $("startTest").onclick = async () => {
    if (!key()) return status("请先连接管理员主密钥", "bad");
    let credential;
    try {
      credential = await resolveTestCredential();
    } catch (error) {
      return status("测试 Key 读取失败：" + error.message, "bad");
    }
    const type = $("testType").value;
    const model = $("testModel").value;
    const files = [...$("testFiles").files];
    const kinds = type === "all"
      ? ["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation", "dictation"]
      : [type];
    const rows = [];
    $("testState").textContent = "运行中";
    redraw(rows);
    status(`正在使用：${credential.label}`, "ok");
    const startedAt = new Date().toISOString();
    const start = performance.now();

    for (const kind of kinds) {
      rows.push({ kind, label: labelMap[kind] || kind, status: "running", message: "执行中" });
      redraw(rows);
      rows[rows.length - 1] = await oneTest(kind, model, files, credential.token);
      redraw(rows);
    }

    const failed = rows.some(item => item.status === "failed");
    const warn = rows.some(item => item.status === "warning");
    const passed = rows.some(item => item.status === "passed");
    const reportStatus = failed ? "failed" : warn ? "warning" : passed ? "passed" : "skipped";
    const runId = "testrun_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    const report = {
      run_id: runId,
      test_type: type,
      status: reportStatus,
      model,
      started_at: startedAt,
      finished_at: new Date().toISOString(),
      duration_ms: performance.now() - start,
      summary: `${rows.filter(item => item.status === "passed").length} passed, ${rows.filter(item => item.status === "warning").length} warning, ${rows.filter(item => item.status === "failed").length} failed, ${rows.filter(item => item.status === "skipped").length} skipped`,
      results: rows,
      quality: {
        api_key_label: credential.label,
        api_key_source: credential.source,
        api_key_id: credential.key_id,
        bugs: rows.filter(item => item.status === "failed").map(item => item.message),
        latency_warnings: rows.flatMap(item => item.quality?.issues || []).filter(issue => /延迟|耗时|超时/.test(issue)),
      },
    };
    try {
      await api("/api/admin/tests", { method: "POST", body: report });
      $("testState").textContent = reportStatus;
      await loadTests();
    } catch (error) {
      status("保存测试报告失败：" + error.message, "bad");
    }
  };

  function addV7Docs() {
    const doc = document.querySelector("#view-docs .panel");
    if (!doc || doc.querySelector("[data-chat2api-v7-docs]")) return;
    const block = document.createElement("div");
    block.dataset.chat2apiV7Docs = "1";
    block.innerHTML = `
      <h2>模型 ID 命名</h2>
      <ul>
        <li><code>gpt-live</code>：推荐的 ChatGPT Voice / Live 路由。实际 Live 档位由 ChatGPT 账号和当前产品配置决定。</li>
        <li><code>gpt-live-mini</code>：保留兼容别名，不承诺强制切到 mini。</li>
        <li><code>gpt-dictation</code>：ChatGPT “听写”按钮路由，只做音频转文字，不生成 ChatGPT 回答。</li>
      </ul>
      <h2>听写 / 音频转文字</h2>
      <p>先上传 <code>audio/*</code> 文件，再调用：</p>
      <div class="codebox">POST ${location.origin}/v1/audio/transcriptions\nAuthorization: Bearer YOUR_API_KEY\n\n{\n  "model":"gpt-dictation",\n  "audio_file_id":"file_xxx"\n}</div>
      <h2>测试场默认样本</h2>
      <p>未上传附件时，视觉测试会自动生成 PNG 和短 WebM 视频；文件理解会自动生成 TXT、CSV、JSON、PDF 四种文件；语音对话和听写会使用内置 MP3 测试短语。视频文件理解属于实验子测试，若 ChatGPT 当前网页不接受视频文件会记录 warning，而不会掩盖图片理解结果。</p>`;
    doc.appendChild(block);
  }

  const beforeShow = show;
  show = function showV7(view) {
    beforeShow(view);
    if (view === "requests") simplifyRequestPage();
    if (view === "docs") setTimeout(addV7Docs, 0);
  };

  const subtitle = document.querySelector("#view-playground .panel > .muted");
  if (subtitle) subtitle.textContent = "执行标准化用例并生成质量报告。未提供附件时会自动生成图片、短视频、TXT/CSV/JSON/PDF 和测试语音；Voice 与听写均由扩展自动寻找并点击 ChatGPT 对应按钮。";

  const footer = document.querySelector(".content > .footer");
  if (footer) footer.textContent = "Token 为 chat2api 本地估算，不是 ChatGPT 官方 usage。视频理解为实验性网页能力；Voice/听写测试会记录扩展按钮定位、麦克风注入和 WebRTC 阶段诊断。";

  if (location.hash === "#docs") addV7Docs();
})();
