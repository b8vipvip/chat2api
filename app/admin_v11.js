(() => {
  const priorOneTest = oneTest;
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.11";

  function randomCode(prefix) {
    return `${prefix}-${Date.now().toString(36).slice(-5).toUpperCase()}-${Math.random().toString(36).slice(2, 5).toUpperCase()}`;
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

  function makeDefaultDocumentsV11() {
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

  function fileKind(file) {
    const name = String(file?.name || "");
    const ext = name.includes(".") ? name.split(".").pop().toUpperCase() : "FILE";
    return ext || "FILE";
  }

  async function documentSubtest(file, model, testToken) {
    const started = performance.now();
    try {
      const uploaded = await upload(file, "file-understanding", testToken);
      const response = await streamChat(
        model,
        "请阅读这个附件，列出文件名和文件类型，并概括核心内容；如果里面有 Test code 或 test_code，请同时列出来。",
        [{ file_id: uploaded.id }],
        testToken,
      );
      const diagnostics = response.meta?.chat2api?.diagnostics || {};
      const issues = [];
      if (Number(diagnostics.attachments_count || 0) !== 1) {
        issues.push(`浏览器诊断没有确认单文件附件：attachments_count=${diagnostics.attachments_count ?? "unknown"}`);
      }
      return {
        kind: fileKind(file).toLowerCase(),
        name: file.name,
        type: file.type,
        bytes: file.size,
        status: issues.length ? "warning" : "passed",
        ...response,
        total_ms: performance.now() - started,
        quality: { grade: issues.length ? "warning" : "passed", issues },
      };
    } catch (error) {
      return {
        kind: fileKind(file).toLowerCase(),
        name: file.name,
        type: file.type,
        bytes: file.size,
        status: "failed",
        error: String(error?.message || error),
        total_ms: performance.now() - started,
        quality: { grade: "failed", issues: [`调用失败：${String(error?.message || error)}`] },
      };
    }
  }

  async function fileTestV11(model, files, testToken) {
    const started = performance.now();
    let selected = files.filter(file => !/^(image|video|audio)\//.test(String(file.type || ""))).slice(0, 4);
    let generated = false;
    let bundleCode = null;
    if (!selected.length) {
      const defaults = makeDefaultDocumentsV11();
      selected = defaults.files;
      bundleCode = defaults.code;
      generated = true;
    }

    const subtests = [];
    for (const file of selected) subtests.push(await documentSubtest(file, model, testToken));
    const successful = subtests.filter(item => item.status !== "failed");
    const passed = subtests.filter(item => item.status === "passed");
    const warnings = subtests.filter(item => item.status === "warning");
    const failed = subtests.filter(item => item.status === "failed");
    const statusValue = successful.length === 0 ? "failed" : (failed.length || warnings.length ? "warning" : "passed");
    const statusText = subtests.map(item => `${fileKind(item)}：${item.status === "passed" ? "通过" : item.status === "warning" ? "警告" : "失败"}`).join(" · ");
    const issues = [
      ...warnings.flatMap(item => (item.quality?.issues || []).map(issue => `${item.name}：${issue}`)),
      ...failed.map(item => `${item.name}：${item.error || "调用失败"}`),
    ];

    return {
      kind: "file",
      label: "文件理解",
      status: statusValue,
      message: `${statusText} · ${passed.length}/${subtests.length} 完全通过${generated ? " · 使用自动生成样本" : ""}`,
      total_ms: performance.now() - started,
      first_token_ms: successful.map(item => item.first_token_ms).filter(value => value != null).sort((a, b) => a - b)[0] ?? null,
      response_chars: successful.reduce((sum, item) => sum + Number(item.response_chars || 0), 0),
      meta: successful[0]?.meta || null,
      default_assets_generated: generated,
      default_bundle_code: bundleCode,
      tested_files: selected.map(file => ({ name: file.name, type: file.type, bytes: file.size })),
      subtests,
      quality: { grade: statusValue, issues },
    };
  }

  oneTest = async function oneTestV11(kind, model, files, testToken) {
    if (kind === "file") return fileTestV11(model, files, testToken);
    return priorOneTest(kind, model, files, testToken);
  };

  const subtitle = document.querySelector("#view-playground .panel > .muted");
  if (subtitle) {
    subtitle.textContent = "执行标准化用例并生成质量报告。文件理解会逐个文件独立测试，避免 ChatGPT 网页在多文件批量处理中长期锁住发送按钮；未提供附件时自动生成 TXT/CSV/JSON/PDF。";
  }
})();
