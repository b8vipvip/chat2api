from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_enable_ui_observer_cannot_self_trigger_on_button_text_mutation(tmp_path):
    source = (ROOT / "app" / "admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")

    assert 'observer.observe(document.documentElement, { childList: true, subtree: true })' not in source
    assert 'rowsObserver.observe(workerRows, { childList: true, subtree: false })' in source
    assert 'if (button.textContent !== nextText) button.textContent = nextText;' in source
    assert 'if (button.title !== nextTitle) button.title = nextTitle;' in source
    assert 'if (button.dataset.workerEnabled !== nextEnabled) button.dataset.workerEnabled = nextEnabled;' in source

    target = tmp_path / "admin_linux_worker_enable_v46.js"
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_enable_ui_reuses_worker_console_owner_instead_of_hot_polling():
    source = (ROOT / "app" / "admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")

    for token in (
        '__CHAT2API_LINUX_WORKER_ROWS__',
        '__CHAT2API_LINUX_WORKER_REFRESH__',
        'globalThis.addEventListener("chat2api:linux-worker-rows"',
        'if (!linuxSection?.classList.contains("active")) return;',
        '}, 5000);',
    ):
        assert token in source

    assert 'setInterval(refresh, 1000);' not in source


def test_enable_ui_paint_is_idempotent_under_repeated_observer_callbacks(tmp_path):
    source_path = ROOT / "app" / "admin_linux_worker_enable_v46.js"
    harness = tmp_path / "enable_ui_harness.mjs"
    harness.write_text(
        f'''import fs from "node:fs";
const source = fs.readFileSync({str(source_path)!r}, "utf8");
let textWrites = 0;
const button = {{
  dataset: {{revoke:"wrk_test"}},
  _text: "禁用 Worker",
  get textContent() {{ return this._text; }},
  set textContent(value) {{ textWrites += 1; this._text = value; }},
  title: "",
  disabled: false,
  classList: {{toggle() {{}}}},
}};
const workerRows = {{}};
const section = {{classList:{{contains(){{return false;}}}}}};
let observer = null;
globalThis.MutationObserver = class {{
  constructor(callback) {{ this.callback = callback; observer = this; }}
  observe(target, options) {{ this.target = target; this.options = options; }}
}};
globalThis.document = {{
  addEventListener() {{}},
  querySelectorAll(selector) {{ return selector === "button[data-revoke]" ? [button] : []; }},
  querySelector() {{ return null; }},
  getElementById(id) {{
    if (id === "linuxWorkerRows") return workerRows;
    if (id === "view-linux-workers") return section;
    return null;
  }},
}};
globalThis.addEventListener = () => {{}};
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.fetch = async () => ({{ok:true, json:async()=>({{data:[]}})}});
(0, eval)(source);
if (!observer) throw new Error("rows observer was not installed");
if (observer.target !== workerRows) throw new Error("observer target escaped Worker tbody");
if (observer.options?.subtree !== false) throw new Error("observer subtree must be false");
observer.callback();
const afterFirst = textWrites;
observer.callback();
if (textWrites !== afterFirst) throw new Error(`paint rewrote identical text: ${{afterFirst}} -> ${{textWrites}}`);
''',
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(harness)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
