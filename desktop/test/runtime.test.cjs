const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { workspaceRoot, serviceEnvironment, isWorkbenchURL, isExportURL, isExternalURL, isStatusURL } = require('../runtime.cjs');

test('only the local workbench origin is allowed in the app window', () => {
  assert.equal(isWorkbenchURL('http://localhost:5173/agents?mode=camel'), true);
  assert.equal(isWorkbenchURL('http://127.0.0.1:5173/#council'), true);
  for (const url of ['https://localhost:5173/', 'http://localhost:5174/', 'http://localhost.evil.test:5173/', 'http://user:secret@localhost:5173/', 'file:///tmp/modeldesk.html', 'javascript:alert(1)']) {
    assert.equal(isWorkbenchURL(url), false, url);
  }
});

test('external navigation accepts only credential-free http(s)', () => {
  assert.equal(isExternalURL('https://github.com/karpathy/llm-council'), true);
  for (const url of ['http://localhost:5173/', 'https://user:secret@example.com/', 'file:///Users/private/auth.json', 'javascript:alert(1)', 'mailto:user@example.com', 'modeldesk-action://retry']) {
    assert.equal(isExternalURL(url), false, url);
  }
});

test('only exact local UUID exports with one supported format download inside the app', () => {
  const route = '/api/workbench/jobs/df9d3857-3d01-4c24-9e64-b9343e217f9a/export';
  for (const host of ['localhost', '127.0.0.1']) {
    for (const format of ['md', 'json']) {
      const url = `http://${host}:8001${route}?format=${format}`;
      assert.equal(isExportURL(url), true, url);
      assert.equal(isWorkbenchURL(url), false, url);
      assert.equal(isExternalURL(url), false, url);
    }
  }
  for (const url of [
    `https://localhost:8001${route}?format=md`,
    `http://localhost.evil.test:8001${route}?format=md`,
    `http://remote.example:8001${route}?format=md`,
    `http://localhost:8002${route}?format=md`,
    `http://user:secret@localhost:8001${route}?format=md`,
    `http://localhost:8001${route}`, `http://localhost:8001${route}?format=html`,
    `http://localhost:8001${route}?format=md&format=json`,
    `http://localhost:8001${route}?format=md&path=credentials.json`,
    `http://localhost:8001${route}?format=md#secret`,
    `http://localhost:8001${route}/../export?format=md`,
    'http://localhost:8001/api/workbench/jobs/not-a-uuid/export?format=json',
    'http://localhost:8001/api/workbench/status?format=md',
    'file:///api/workbench/jobs/df9d3857-3d01-4c24-9e64-b9343e217f9a/export?format=md',
    'javascript:alert(1)',
  ]) assert.equal(isExportURL(url), false, url);
});

test('internal page access cannot be redirected to another local file', () => {
  const status = '/Users/example/ModelDesk.app/Contents/Resources/app/status.html';
  assert.equal(isStatusURL(pathToFileURL(status).href + '?state=error', status), true);
  assert.equal(isStatusURL('file:///Users/example/credentials.json', status), false);
  assert.equal(isStatusURL('file://remote/Users/example/ModelDesk.app/Contents/Resources/app/status.html', status), false);
});

test('launch resolves absolute workspace and deterministic GUI executable paths', () => {
  assert.equal(workspaceRoot({ MODEL_DESK_WORKSPACE: '/Users/example/llm-tools' }), '/Users/example/llm-tools');
  assert.throws(() => workspaceRoot({ MODEL_DESK_WORKSPACE: '../relative' }), /absolute/);
  const environment = serviceEnvironment({ PATH: '/usr/bin', MODEL_DESK_WORKSPACE: '/Users/example/llm-tools' });
  assert.equal(environment.PATH.startsWith('/usr/local/bin:/opt/homebrew/bin:'), true);
  assert.equal(environment.MODEL_DESK_WORKSPACE, '/Users/example/llm-tools');
});

test('the internal error page script is covered by its exact CSP hash', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'status.html'), 'utf8');
  const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
  const hash = crypto.createHash('sha256').update(script).digest('base64');
  assert.equal(page.includes(`script-src 'sha256-${hash}'`), true);
  assert.equal(page.includes('href="modeldesk-action://retry"'), true);
});
