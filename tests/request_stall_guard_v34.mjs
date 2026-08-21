import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../chrome_extension/content_request_stall_guard_v34.js', import.meta.url), 'utf8');

const context = {
  console,
  Set,
  Date,
  Promise,
  document: { querySelectorAll: () => [] },
  getComputedStyle: () => ({ display: '', visibility: '' }),
  setInterval: () => 1,
  chrome: { runtime: { sendMessage: async () => ({ ok: true }) } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'content_request_stall_guard_v34.js' });

const guard = context.__CHAT2API_REQUEST_STALL_GUARD_V34__;
assert.ok(guard, 'stall guard should install');
assert.equal(guard.matchesError("You've reached the Free plan limit"), true);
assert.equal(guard.matchesError('There was an error generating a response. Please try again.'), true);
assert.equal(guard.matchesError('普通的正常回复内容'), false);

{
  const track = guard.newTrack('req_generation_stop');
  assert.equal(guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '' }, 0), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 10), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: true, new_assistant_text: '', error_text: '' }, 20), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 30), null);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 5031);
  assert.equal(failure?.code, 'generation-stopped-without-response');
}

{
  const track = guard.newTrack('req_inline_error');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '' }, 100);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 110);
  const failure = guard.evaluate(track, {
    composer_has_text: false,
    generating: false,
    new_assistant_text: '',
    error_text: "You've reached the Free plan limit. Try again later.",
  }, 120);
  assert.equal(failure?.code, 'chatgpt-ui-error');
}

{
  const track = guard.newTrack('req_no_start');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '' }, 1000);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 1010);
  const before = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 60000);
  assert.equal(before, null);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '' }, 61011);
  assert.equal(failure?.code, 'generation-did-not-start');
}

{
  const track = guard.newTrack('req_success');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '' }, 2000);
  guard.evaluate(track, { composer_has_text: false, generating: true, new_assistant_text: '', error_text: '' }, 2010);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '正常回复', error_text: '' }, 3000);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '正常回复', error_text: '' }, 10000);
  assert.equal(failure, null);
}

console.log('request_stall_guard_v34 VM contract passed');
