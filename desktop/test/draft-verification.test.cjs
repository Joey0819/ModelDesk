const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

test('native draft check requires actual exact readback and unchanged window/thread without normalization', { skip: process.platform !== 'darwin' }, () => {
  const source = fs.readFileSync(path.join(__dirname, '../native/MuseBridge.swift'), 'utf8');
  const contract = source.split('// BEGIN DRAFT VERIFICATION CONTRACT\n')[1]?.split('// END DRAFT VERIFICATION CONTRACT')[0];
  assert.ok(contract);
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'modeldesk-draft-contract-'));
  try {
    const file = path.join(folder, 'main.swift');
    fs.writeFileSync(file, contract + `
let payload = "[MODELDESK_REQUEST:expected]\\nChinese 内容\\n  code indentation"
func check(_ copied: String, thread: Bool = true, app: Bool = true, window: Bool = true) -> String {
  draftVerificationError(expected: payload, copied: copied, sentinel: "fresh sentinel", newThread: thread, appFocused: app, windowFocused: window)
}
assert(check(payload).isEmpty)
assert(check("fresh sentinel") == "draft_copy_unconfirmed")
assert(check("") == "draft_copy_unconfirmed")
assert(check(payload + "\\n") == "draft_content_mismatch")
assert(check("[MODELDESK_REQUEST:expected]\\npartial") == "draft_content_mismatch")
assert(check("unrelated text") == "draft_content_mismatch")
assert(check(payload, thread: false) == "draft_thread_changed")
assert(check(payload, app: false) == "draft_focus_lost")
assert(check(payload, window: false) == "draft_focus_lost")
print("draft verification contract passed")
`);
    assert.match(execFileSync('/usr/bin/xcrun', ['swift', file], { encoding: 'utf8' }), /contract passed/);
  } finally { fs.rmSync(folder, { recursive: true, force: true }); }
});
