const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { MAX_FILE_BYTES, safeSelectedPath, inputMime, readSelectedFile, collectSelectedFiles } = require('../input-files.cjs');
const { isExportURL, exportFormat, isExternalURL } = require('../runtime.cjs');

async function fixture(run) {
  const directory = await fs.mkdtemp(path.join(await fs.realpath(os.tmpdir()), 'modeldesk-input-'));
  try { await run(directory); } finally { await fs.rm(directory, { recursive: true, force: true }); }
}

test('native imports validate names, supported extensions and private directories', () => {
  assert.equal(safeSelectedPath('/Users/example/材料/研究记录.md'), true);
  assert.equal(safeSelectedPath('/Users/example/figure.PNG'), true);
  for (const value of ['relative.txt', '/Users/example/.env', '/Users/example/credentials.json', '/Users/example/.private/auth/copy.txt', '/Users/example/node_modules/pkg/file.js', '/Users/example/key.pem', '/Users/example/line\nname.txt']) assert.equal(safeSelectedPath(value), false, value);
  assert.equal(inputMime('fake.png', Buffer.from('not an image')), null);
  assert.equal(inputMime('binary.txt', Buffer.from([255,0,1])), null);
  assert.equal(inputMime('note.md', Buffer.from('中文材料')), 'text/plain');
});

test('native selection returns only explicitly chosen bytes without source paths', async () => fixture(async directory => {
  const filename = path.join(directory, 'note.md');
  await fs.writeFile(filename, '实际资料\nsecond line');
  const result = await collectSelectedFiles([filename]);
  assert.equal(result.files.length, 1);
  assert.equal(result.files[0].name, 'note.md');
  assert.equal(Buffer.from(result.files[0].data_base64, 'base64').toString(), '实际资料\nsecond line');
  assert.deepEqual(result.omitted, []);
  assert.equal(JSON.stringify(result).includes(directory), false);
}));

test('symlinks and oversize files are omitted with bounded reads', async () => fixture(async directory => {
  const real = path.join(directory, 'real.txt'), link = path.join(directory, 'link.txt'), huge = path.join(directory, 'huge.txt');
  await fs.writeFile(real, 'actual text'); await fs.symlink(real, link); await fs.writeFile(huge, '');
  const handle=await fs.open(huge,'r+'); await handle.truncate(MAX_FILE_BYTES+1); await handle.close();
  await assert.rejects(readSelectedFile(link), /符号链接/);
  await assert.rejects(readSelectedFile(huge), /20MB/);
  const results=await collectSelectedFiles([real,link,huge]);
  assert.equal(results.files.length,1); assert.equal(results.omitted.length,2);
  assert.equal(JSON.stringify(results.omitted).includes(directory),false);
}));

test('original attachments use exact loopback project/asset UUID download routes', () => {
  const route='http://localhost:8001/api/workbench/projects/df9d3857-3d01-4c24-9e64-b9343e217f9a/assets/41bb2bb9-2c81-4082-8a62-e7aa0f65041b/content';
  assert.equal(isExportURL(route),true); assert.equal(exportFormat(route),'attachment'); assert.equal(isExternalURL(route),false);
  const history=route.replace('/projects/', '/jobs/').replace('/assets/', '/attachments/');
  assert.equal(isExportURL(history),true); assert.equal(exportFormat(history),'attachment');
  assert.equal(isExportURL(history+'?path=.env'),false);
  for (const url of [route+'?path=.env',route+'#secret',route.replace('localhost','evil.test'),route.replace('http:','https:'),route.replace('/content','/content/../content'),route.replace('/assets/41bb2bb9-2c81-4082-8a62-e7aa0f65041b','/assets/no-uuid')]) assert.equal(isExportURL(url),false,url);
});
