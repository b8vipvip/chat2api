(() => {
  const KEY = "__CHAT2API_EXTENSION_COLUMN_MENU_VIEWPORT_V2111__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const BUTTON_ID = "extensionColumnSettingsButton";
  const MENU_ID = "extensionColumnSettingsMenu";
  const MARGIN = 12;
  const GAP = 8;
  const PREFERRED_WIDTH = 340;
  const PREFERRED_MAX_HEIGHT = 620;
  let frame = 0;

  function viewportSize() {
    return {
      width: Math.max(document.documentElement?.clientWidth || 0, window.innerWidth || 0),
      height: Math.max(document.documentElement?.clientHeight || 0, window.innerHeight || 0),
    };
  }

  function isMenuVisible(menu) {
    if (!menu) return false;
    if (menu.style.display === "none") return false;
    return getComputedStyle(menu).display !== "none";
  }

  function adjustMenu() {
    frame = 0;
    const button = document.getElementById(BUTTON_ID);
    const menu = document.getElementById(MENU_ID);
    if (!button || !menu || !isMenuVisible(menu)) return;

    const viewport = viewportSize();
    if (viewport.width <= 0 || viewport.height <= 0) return;

    const rect = button.getBoundingClientRect();
    const width = Math.max(120, Math.min(PREFERRED_WIDTH, viewport.width - MARGIN * 2));
    const left = Math.max(MARGIN, Math.min(rect.left, viewport.width - width - MARGIN));

    // Measure the real menu content before deciding whether it should open
    // above or below the settings button. This prevents the bottom of the menu
    // from falling outside the viewport on shorter admin-console windows.
    menu.style.width = `${width}px`;
    menu.style.maxWidth = `${Math.max(120, viewport.width - MARGIN * 2)}px`;
    menu.style.maxHeight = "none";
    menu.style.height = "auto";
    menu.style.overflowY = "visible";
    menu.style.overflowX = "hidden";

    const naturalHeight = Math.min(
      PREFERRED_MAX_HEIGHT,
      Math.max(1, Math.ceil(menu.scrollHeight || menu.getBoundingClientRect().height || 1)),
    );
    const belowTop = rect.bottom + GAP;
    const availableBelow = Math.max(0, viewport.height - MARGIN - belowTop);
    const availableAbove = Math.max(0, rect.top - GAP - MARGIN);
    const usefulHeight = Math.min(naturalHeight, 360);
    const openAbove = availableBelow < usefulHeight && availableAbove > availableBelow;
    const available = openAbove ? availableAbove : availableBelow;
    const maxHeight = Math.max(1, Math.min(naturalHeight, available || viewport.height - MARGIN * 2));

    let top = openAbove
      ? rect.top - GAP - maxHeight
      : belowTop;
    top = Math.max(MARGIN, Math.min(top, viewport.height - MARGIN - maxHeight));

    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.maxHeight = `${maxHeight}px`;
    menu.style.overflowY = naturalHeight > maxHeight ? "auto" : "visible";
    menu.style.overflowX = "hidden";
    menu.style.overscrollBehavior = "contain";
    menu.style.scrollbarGutter = "stable";
    menu.style.transformOrigin = openAbove ? "bottom left" : "top left";
    menu.dataset.chat2apiViewportPlacement = openAbove ? "above" : "below";
  }

  function scheduleAdjust() {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      // Run one frame after the legacy menu handler so this patch owns the
      // final viewport-safe position even when renderMenu() repositions it.
      frame = requestAnimationFrame(adjustMenu);
    });
  }

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(`#${BUTTON_ID}`) || target?.closest(`#${MENU_ID}`)) {
      scheduleAdjust();
    }
  }, true);

  document.addEventListener("change", event => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(`#${MENU_ID}`)) scheduleAdjust();
  }, true);

  window.addEventListener("resize", scheduleAdjust);
  window.addEventListener("scroll", scheduleAdjust, true);

  if (typeof MutationObserver === "function") {
    const observer = new MutationObserver(records => {
      if (records.some(record =>
        [...record.addedNodes].some(node =>
          node instanceof Element
          && (node.id === MENU_ID || node.querySelector?.(`#${MENU_ID}`)),
        ),
      )) {
        scheduleAdjust();
      }
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
  }
})();
