const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
if (process.platform !== 'darwin') throw new Error('Muse desktop bridge requires macOS.');
const root = path.resolve(__dirname, '..');
fs.mkdirSync(path.join(root, 'bin'), { recursive: true });
execFileSync('/usr/bin/xcrun', ['swiftc', '-O', path.join(root, 'native', 'MuseBridge.swift'), '-o', path.join(root, 'bin', 'muse-bridge')], { stdio: 'inherit' });
execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', '--identifier', 'local.modeldesk.muse-bridge', path.join(root, 'bin', 'muse-bridge')], { stdio: 'inherit' });
