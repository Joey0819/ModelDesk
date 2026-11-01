const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { buildMuse } = require('../scripts/build-muse.cjs');

test('unchanged bridge source/toolchain reuses only exact verified binary; changes rebuild atomically', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-cache-test-'));
  try {
    fs.mkdirSync(path.join(root, 'native'));
    const source = path.join(root, 'native', 'MuseBridge.swift'), compiler = path.join(root, 'swiftc'), binary = path.join(root, 'bin', 'muse-bridge');
    fs.writeFileSync(source, 'source-1'); fs.writeFileSync(compiler, 'compiler-1');
    let builds = 0, signatureValid = true, compilationFails = false;
    const run = (command, args) => {
      if (command === '/usr/bin/xcrun' && args.includes('-o')) {
        builds++;
        if (compilationFails) throw new Error('compile failed');
        fs.writeFileSync(args.at(-1), `signed binary ${builds}`);
      } else if (command === '/usr/bin/xcrun') {
        return ({ '--find': compiler, 'swiftc': 'Swift 6', '--show-sdk-path': '/sdk/MacOSX.sdk', '--show-sdk-version': '26.0' })[args[0]];
      } else if (args.includes('--verify')) {
        assert.ok(args.includes('--strict'));
        assert.ok(args.includes('=identifier "local.modeldesk.muse-bridge"'));
        if (!signatureValid && args.at(-1) === binary) throw new Error('signature failed');
      }
      return '';
    };
    const build = () => buildMuse({ root, run, platform: 'darwin', arch: 'arm64' });
    assert.equal(build().reused, false); assert.equal(builds, 1);
    assert.equal(build().reused, true); assert.equal(builds, 1);
    fs.writeFileSync(binary, 'tampered bytes');
    assert.equal(build().reused, false); assert.equal(builds, 2);
    signatureValid = false;
    assert.equal(build().reused, false); assert.equal(builds, 3);
    signatureValid = true;
    fs.writeFileSync(compiler, 'compiler-2');
    assert.equal(build().reused, false); assert.equal(builds, 4);
    fs.writeFileSync(source, 'source-2');
    const before = fs.readFileSync(binary);
    compilationFails = true;
    assert.throws(build, /compile failed/);
    assert.deepEqual(fs.readFileSync(binary), before);
    assert.deepEqual(fs.readdirSync(path.join(root, 'bin')).sort(), ['muse-bridge', 'muse-bridge.build.json']);
    assert.throws(() => buildMuse({ root, run, platform: 'linux' }), /macOS/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
