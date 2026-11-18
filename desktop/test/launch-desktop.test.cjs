const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

test('source launcher delegates workspace selection and adds X11 only on Linux', t => {
  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), 'modeldesk-launch-'));
  t.after(() => fs.rmSync(checkout, { recursive: true, force: true }));
  const desktop = path.join(checkout, 'desktop');
  const electronBin = path.join(desktop, 'node_modules', '.bin');
  const scripts = path.join(checkout, 'scripts');
  fs.mkdirSync(electronBin, { recursive: true });
  fs.mkdirSync(scripts);

  const launcher = path.join(scripts, 'launch-desktop.sh');
  fs.copyFileSync(path.resolve(__dirname, '../../scripts/launch-desktop.sh'), launcher);
  fs.writeFileSync(path.join(scripts, 'uname'), '#!/bin/sh\nprintf "%s\\n" "$MODELDESK_TEST_PLATFORM"\n', { mode: 0o755 });
  fs.writeFileSync(path.join(electronBin, 'electron'), '#!/usr/bin/env node\nconsole.log(JSON.stringify({ workspace: process.env.MODEL_DESK_WORKSPACE ?? null, args: process.argv.slice(2) }));\n', { mode: 0o755 });

  const { MODEL_DESK_WORKSPACE: _inherited, ...env } = process.env;
  env.PATH = [scripts, path.dirname(process.execPath), env.PATH].join(path.delimiter);
  const forwarded = ['--enable-logging', 'argument with spaces'];
  const launch = (platform, override) => JSON.parse(execFileSync('bash', [launcher, ...forwarded], {
    env: { ...env, MODELDESK_TEST_PLATFORM: platform, ...override },
    encoding: 'utf8',
  }));
  assert.deepEqual(launch('Darwin'), { workspace: null, args: [desktop, ...forwarded] });
  assert.deepEqual(launch('Darwin', { MODEL_DESK_WORKSPACE: '/w/explicit' }), {
    workspace: '/w/explicit', args: [desktop, ...forwarded],
  });
  assert.deepEqual(launch('Linux'), { workspace: null, args: [desktop, '--ozone-platform=x11', ...forwarded] });
});
