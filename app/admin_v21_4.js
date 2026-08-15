(() => {
  const VERSION = "0.21.4";
  const MINI_MODEL = "gpt-5.5-mini";

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function setHtml(node, value) {
    if (node && node.innerHTML !== value) node.innerHTML = value;
  }

  function patchMiniCard() {
    const cards = [...document.querySelectorAll("#view-models .modelCard")];
    const card = cards.find(item => item.querySelector(".modelCardId")?.textContent.trim() === MINI_MODEL);
    if (!card) return;

    setText(card.querySelector(".modelPlazaEyebrow"), "文本 / 多模态 · Free 账户默认");
    setText(
      card.querySelector(".modelCardSummary"),
      "Free 账户默认逻辑模型，支持文本、视觉和文件理解；没有可用 Free 扩展时自动回退 GPT-5.5 + 极速。",
    );
    setHtml(
      card.querySelector(".modelBadges"),
      ["文本", "视觉", "文件", "Free", "自动路由"].map(text => `<span class="modelBadge">${text}</span>`).join(""),
    );
    setHtml(
      card.querySelector(".modelUseCases"),
      ["Free 账户", "视觉理解", "文件理解", "快速回复"].map(text => `<span class="modelUseCase">${text}</span>`).join(""),
    );

    const rows = card.querySelectorAll(".modelSpecRow");
    for (const row of rows) {
      const label = row.querySelector("span")?.textContent?.trim();
      if (label === "输入") setText(row.querySelector("strong"), "文本 / 图片 / 文件");
    }
  }

  function patchDocs() {
    const rows = [...document.querySelectorAll("#view-docs table tbody tr")];
    const row = rows.find(item => item.querySelector("code")?.textContent.trim() === MINI_MODEL);
    if (!row) return;
    const cells = row.querySelectorAll("td");
    setText(cells[1], "文本 / 视觉 / 文件理解（优先 Free 账户默认模型）");
  }

  function patchAll() {
    const brandSmall = document.querySelector(".brand small");
    setText(brandSmall, `Server Console · v${VERSION}`);
    patchMiniCard();
    patchDocs();
  }

  const baseShow = typeof show === "function" ? show : null;
  if (baseShow) {
    show = async viewName => {
      await baseShow(viewName);
      patchAll();
    };
  }

  patchAll();
  setTimeout(patchAll, 100);
  setTimeout(patchAll, 500);
})();
