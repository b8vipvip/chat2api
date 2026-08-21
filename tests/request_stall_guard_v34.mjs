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
assert.equal(guard.constants.generation_stop_grace_ms, 30000);

{
  const track = guard.newTrack('req_generation_stop');
  assert.equal(guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 0), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 10), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: true, new_assistant_text: '', error_text: '', status_active: false, send_ready: false }, 20), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 30), null);
  // The production regression fired at roughly five seconds after Stop disappeared.
  // That must remain safely inside the new UI-transition grace period.
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 5031), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: true, send_ready: true }, 20000), null);
  // Active status resets the idle clock, so a fresh full grace is required after it clears.
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 20010), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 50009), null);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 50011);
  assert.equal(failure?.code, 'generation-stopped-without-response');
}

{
  const track = guard.newTrack('req_send_not_ready');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 0);
  guard.evaluate(track, { composer_has_text: false, generating: true, new_assistant_text: '', error_text: '', status_active: false, send_ready: false }, 10);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: false }, 20);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: false }, 60000), null);
  assert.equal(guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 60010), null);
}

{
  const track = guard.newTrack('req_inline_error');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 100);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 110);
  const failure = guard.evaluate(track, {
    composer_has_text: false,
    generating: false,
    new_assistant_text: '',
    error_text: "You've reached the Free plan limit. Try again later.",
    status_active: false,
    send_ready: true,
  }, 120);
  assert.equal(failure?.code, 'chatgpt-ui-error');
}

{
  const track = guard.newTrack('req_no_start');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 1000);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 1010);
  const before = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 60000);
  assert.equal(before, null);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 61011);
  assert.equal(failure?.code, 'generation-did-not-start');
}

{
  const track = guard.newTrack('req_success');
  guard.evaluate(track, { composer_has_text: true, generating: false, new_assistant_text: '', error_text: '', status_active: false, send_ready: true }, 2000);
  guard.evaluate(track, { composer_has_text: false, generating: true, new_assistant_text: '', error_text: '', status_active: false, send_ready: false }, 2010);
  guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '正常回复', error_text: '', status_active: false, send_ready: true }, 3000);
  const failure = guard.evaluate(track, { composer_has_text: false, generating: false, new_assistant_text: '正常回复', error_text: '', status_active: false, send_ready: true }, 10000);
  assert.equal(failure, null);
}

console.log('request_stall_guard_v34 VM contract passed');
