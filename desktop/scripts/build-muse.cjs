const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const IDENTIFIER = 'local.modeldesk.muse-bridge';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');

function validCache(manifest, inputHash, binary, verifySignature) {
  if (manifest?.schema_version !== 1 || manifest.input_sha256 !== inputHash || manifest.binary_sha256 !== hash(binary)) return false;
  try { verifySignature(); return true; } catch { return false; }
}

function buildMuse({ root = path.resolve(__dirname, '..'), run = execFileSync, platform = process.platform, arch = process.arch } = {}) {
  if (platform !== 'darwin') throw new Error('Muse desktop bridge requires macOS.');
  const source = path.join(root, 'native', 'MuseBridge.swift');
  const bin = path.join(root, 'bin');
  const output = path.join(bin, 'muse-bridge');
  const receipt = `${output}.build.json`;
  fs.mkdirSync(bin, { recursive: true });
  const inspect = args => String(run('/usr/bin/xcrun', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })).trim();
  const compiler = inspect(['--find', 'swiftc']);
  const inputs = { source_sha256: hash(fs.readFileSync(source)), compiler, compiler_sha256: hash(fs.readFileSync(compiler)),
    compiler_version: inspect(['swiftc', '--version']), sdk: inspect(['--show-sdk-path']), sdk_version: inspect(['--show-sdk-version']),
    architecture: arch, source_path: source, optimization: '-O', signing_identifier: IDENTIFIER };
  const fingerprint = hash(JSON.stringify(inputs));
  const verify = file => run('/usr/bin/codesign', ['--verify', '--strict', '-R', `=identifier "${IDENTIFIER}"`, file], { stdio: ['ignore', 'pipe', 'pipe'] });
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(receipt, 'utf8')); } catch { /* A missing or damaged receipt requires a fresh build. */ }
  if (fs.existsSync(output) && validCache(manifest, fingerprint, fs.readFileSync(output), () => verify(output))) {
    console.log('Reused verified Muse bridge: source, toolchain and signed binary unchanged.');
    return { reused: true, ...manifest };
  }
  const temporary = fs.mkdtempSync(path.join(bin, '.muse-build-'));
  try {
    const staged = path.join(temporary, 'muse-bridge');
    run('/usr/bin/xcrun', ['swiftc', '-O', source, '-o', staged], { stdio: 'inherit' });
    run('/usr/bin/codesign', ['--force', '--sign', '-', '--identifier', IDENTIFIER, staged], { stdio: 'inherit' });
    verify(staged);
    const next = { schema_version: 1, input_sha256: fingerprint, binary_sha256: hash(fs.readFileSync(staged)), inputs };
    const stagedReceipt = path.join(temporary, 'manifest.json');
    fs.writeFileSync(stagedReceipt, JSON.stringify(next, null, 2) + '\n');
    fs.renameSync(staged, output);
    fs.renameSync(stagedReceipt, receipt);
    console.log('Built and verified Muse bridge; saved source and toolchain receipt.');
    return { reused: false, ...next };
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

if (require.main === module) buildMuse();
module.exports = { buildMuse, validCache };
