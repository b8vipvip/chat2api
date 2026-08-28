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
        f'''import fs from "node:fs";\n'
const source = fs.readFileSync({str(source_path)!r}, "utf8");\n'
let textWrites = 0;\n'
const button = {{\n'
  dataset: {{revoke:"wrk_test"}},\n'
  _text: "禁用 Worker",\n'
  get textContent() {{ return this._text; }},\n'
  set textContent(value) {{ textWrites += 1; this._text = value; }},\n'
  title: "",\n'
  disabled: false,\n'
  classList: {{toggle() {{}}}},\n'
}};\n'
const workerRows = {{}};\n'
const section = {{classList:{{contains(){{return false;}}}}}};\n'
let observer = null;\n'
globalThis.MutationObserver = class {{\n'
  constructor(callback) {{ this.callback = callback; observer = this; }}\n'
  observe(target, options) {{ this.target = target; this.options = options; }}\n'
}};\n'
globalThis.document = {{\n'
  addEventListener() {{}},\n'
  querySelectorAll(selector) {{ return selector === "button[data-revoke]" ? [button] : []; }},\n'
  querySelector() {{ return null; }},\n'
  getElementById(id) {{\n'
    if (id === "linuxWorkerRows") return workerRows;\n'
    if (id === "view-linux-workers") return section;\n'
    return null;\n'
  }},\n'
}};\n'
globalThis.addEventListener = () => {{}};\n'
globalThis.setInterval = () => 0;\n'
globalThis.setTimeout = () => 0;\n'
globalThis.fetch = async () => ({{ok:true, json:async()=>({{data:[]}})}});\n'
(0, eval)(source);\n'
if (!observer) throw new Error("rows observer was not installed");\n'
if (observer.target !== workerRows) throw new Error("observer target escaped Worker tbody");\n'
if (observer.options?.subtree !== false) throw new Error("observer subtree must be false");\n'
observer.callback();\n'
const afterFirst = textWrites;\n'
observer.callback();\n'
if (textWrites !== afterFirst) throw new Error(`paint rewrote identical text: ${{afterFirst}} -> ${{textWrites}}`);\n'
''',
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(harness)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
