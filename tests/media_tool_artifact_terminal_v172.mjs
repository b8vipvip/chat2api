import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const content = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const rich = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const listeners = [];
const context = {
  chrome: { runtime: { onMessage: {
    addListener: fn => listeners.push(fn),
    removeListener: () => {},
  }}},
  setTimeout, clearTimeout,
};
runInNewContext(content, context);
assert.equal(listeners.length, 1);
const owner = context.__CHAT2API_REQUEST_CONTENT_V6__;
assert.ok(owner?.contract, "only request-v6 owns request terminal decisions");

const creation = owner.contract.isImageCreationRequest;
const recipe = owner.contract.isLocalImageBuildCommand;
const request = "External account connections are disabled.\n\n[User]\n帮我生成100X100的萝卜头像";
const command = `bash -lc python - <<'PY'
from PIL import Image
im=Image.open('/mnt/data/a_simple_clean_cute_cartoon_illustration_in_a_10.png').convert('RGB')
im.resize((100,100),Image.Resampling.LANCZOS).save('/mnt/data/萝卜头像_100x100.png')
print(Image.open('/mnt/data/萝卜头像_100x100.png').size)
PY`;
assert.equal(creation(request), true);
assert.equal(recipe(command), true, "TX03 reply is a PIL recipe, not image data");
assert.equal(recipe("The picture is ready: ![image](https://example.com/ok.png)"), false);
assert.equal(recipe("Here is an example:\n```bash\n" + command + "\n```"), false);
assert.equal(recipe("from PIL import Image\nprint('text only')"), false);
assert.equal(creation("Explain the PIL script without creating an image"), false);
assert.equal(creation("[User] 请分析这个图片生成函数的性能"), false);

assert.match(content, /const imageBuildArtifact = imageCreationRequested/);
assert.match(content, /const intrinsicHandoff = Boolean\(imageHandoff\) \|\| imageBuildArtifact/);
assert.match(content, /const captureMedia = imageHandoff \|\| imageBuildArtifact/);
assert.match(content, /networkTerminalAt && networkTerminalText && !captureMedia/);
assert.match(content, /isLocalImageBuildCommand\(networkTerminalText\)/);
assert.match(content, /&& !imageBuildArtifact[\s\S]*?!isImageToolArguments\(networkText\)\) \{/);
assert.match(content, /ChatGPT returned an image-building command, but no verified transferable image was captured/);
assert.match(content, /hasTransferableImage\(captured\) && delivered > 0/);
assert.match(content, /response_terminal_source: "verified-request-media-dom-v176"/);
assert.match(content, /media_last_baseline_excluded/);
assert.match(rich, /excluded_by_baseline/);
assert.match(rich, /excluded_by_media_gate/);
assert.match(rich, /baseline_key_count/);
assert.equal((content.match(/type: "chat.completed"/g) || []).length >= 2, true);
console.log("TX03 PIL recipe guarded by request-v6; only verified newly transferred image may complete");
