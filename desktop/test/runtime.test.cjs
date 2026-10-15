const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { workspaceRoot, serviceEnvironment, isWorkbenchURL, isOriginalFrameURL, isExportURL, exportFormat, isExternalURL, isStatusURL, isTrustedWorkbenchIPC } = require('../runtime.cjs');

test('media originals and portable bundles use canonical scoped export routes', () => {
  const id = 'df9d3857-3d01-4c24-9e64-b9343e217f9a';
  const base = `http://localhost:8001/api/media/jobs/${id}`;
  const content = `${base}/assets/${id}/content`;
  const bundle = `${base}/bundle`;
  for (const url of [content, `${content}?project_id=${id}&download=1`, `${content}?download=1`, bundle, `${bundle}?project_id=${id}`]) {
    assert.equal(isExportURL(url), true, url);
    assert.equal(exportFormat(url), url.includes('/bundle') ? 'zip' : 'attachment');
  }
  for (const url of [`${bundle}?download=1`, `${bundle}?project_id=foreign`, `${content}?download=true`, `${content}?project_id=${id}&project_id=${id}`, `${content}?token=private`, `${content}#hash`, content.replace('localhost', 'example.com'), content.replace('/assets/', '/%2e%2e/assets/')]) {
    assert.equal(isExportURL(url), false, url);
    assert.equal(exportFormat(url), null, url);
  }
});

test('only the local workbench origin is allowed in the app window', () => {
  assert.equal(isWorkbenchURL('http://localhost:5173/agents?mode=camel'), true);
  assert.equal(isWorkbenchURL('http://127.0.0.1:5173/#council'), true);
  for (const url of ['https://localhost:5173/', 'http://localhost:5174/', 'http://localhost.evil.test:5173/', 'http://user:secret@localhost:5173/', 'file:///tmp/modeldesk.html', 'javascript:alert(1)']) {
    assert.equal(isWorkbenchURL(url), false, url);
  }
});

test('upstream Streamlit origin is allowed only as a separate frame', () => {
  for (const value of ['http://127.0.0.1:8501/', 'http://localhost:8501/?login=1']) {
    assert.equal(isOriginalFrameURL(value), true);
    assert.equal(isWorkbenchURL(value), false);
  }
  for (const value of ['https://localhost:8501/', 'http://localhost:8502/', 'http://evil.test:8501/', 'http://user:key@localhost:8501/', 'file:///tmp/app.py', 'chrome-extension://lbljnnfbdkefegcmacocckdiljgifdfp/app.html']) {
    assert.equal(isOriginalFrameURL(value), false, value);
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

test('platform JSON backups download through a fixed local endpoint', () => {
  const route = 'http://127.0.0.1:8001/api/platform/data/export';
  for (const value of [route, `${route}?include_jobs=true`, `${route}?include_jobs=false`]) {
    assert.equal(isExportURL(value), true, value);
    assert.equal(exportFormat(value), 'json');
    assert.equal(isExternalURL(value), false);
  }
  for (const value of [`${route}?include_jobs=1`, `${route}?include_jobs=true&include_jobs=false`, `${route}?include_jobs=true&token=secret`, `${route}?format=json`, `${route}/../export`, `${route}#private`, route.replace('127.0.0.1', 'example.com')]) {
    assert.equal(isExportURL(value), false, value);
    assert.equal(exportFormat(value), null);
  }
});

test('collaboration run exports stay in the native save flow with a strict UUID route', () => {
  const route = 'http://localhost:8001/api/harness/runs/df9d3857-3d01-4c24-9e64-b9343e217f9a/export';
  for (const format of ['md', 'json']) {
    const url = `${route}?format=${format}`;
    assert.equal(isExportURL(url), true);
    assert.equal(exportFormat(url), format);
    assert.equal(isExternalURL(url), false);
  }
  for (const url of [route, `${route}?format=html`, `${route}?format=md&format=json`,
    `${route}?format=md&artifact_id=task:review`, `${route}?format=md&path=private`,
    `${route}/../export?format=md`, `${route}?format=json#fragment`,
    route.replace('localhost', 'remote.example') + '?format=md',
    route.replace('df9d3857-3d01-4c24-9e64-b9343e217f9a', 'anything') + '?format=md']) {
    assert.equal(isExportURL(url), false, url);
    assert.equal(exportFormat(url), null, url);
  }
});

test('selected real artifacts use the same fixed local export route without file paths or extra query fields', () => {
  const route = 'http://localhost:8001/api/workbench/jobs/df9d3857-3d01-4c24-9e64-b9343e217f9a/export';
  for (const id of ['task:review', 'chat:openai/gpt', 'council:synthesis', 'review:findings', 'failure:child', 'format:0']) {
    const value = `${route}?format=md&artifact_id=${encodeURIComponent(id)}`;
    assert.equal(isExportURL(value), true, value);
    assert.equal(exportFormat(value), 'md');
  }
  for (const suffix of ['format=json&artifact_id=task:review', 'format=md&artifact_id=unknown:x', 'format=md&artifact_id=task:review&path=auth.json', 'format=md&artifact_id=task:review&artifact_id=task:draft', 'format=md&artifact_id=task:%0Aheader', 'format=md&artifact_id=']) assert.equal(isExportURL(`${route}?${suffix}`), false, suffix);
});

test('internal page access cannot be redirected to another local file', () => {
  const status = '/Users/example/ModelDesk.app/Contents/Resources/app/status.html';
  assert.equal(isStatusURL(pathToFileURL(status).href + '?state=error', status), true);
  assert.equal(isStatusURL('file:///Users/example/credentials.json', status), false);
  assert.equal(isStatusURL('file://remote/Users/example/ModelDesk.app/Contents/Resources/app/status.html', status), false);
});

test('Muse task exports accept only scoped local Markdown or JSON', () => {
  const base = 'http://localhost:8001/api/muse/tasks/df9d3857-3d01-4c24-9e64-b9343e217f9a/export';
  assert.equal(isExportURL(`${base}?format=md`), true);
  assert.equal(isExportURL(`${base}?project_id=df9d3857-3d01-4c24-9e64-b9343e217f9a&format=json`), true);
  assert.equal(exportFormat(`${base}?format=json`), 'json');
  for (const suffix of ['format=html', 'format=md&path=/etc/passwd', 'format=md&project_id=other', 'format=md&format=json', 'format=md&project_id=', 'format=md#external']) assert.equal(isExportURL(`${base}?${suffix}`), false);
  assert.equal(isExportURL(base.replace('localhost', 'example.com') + '?format=md'), false);
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

test('native project picker IPC accepts only this window and its trusted main frame', () => {
  const contents = {
    mainFrame: { processId: 21, routingId: 7 },
    isDestroyed: () => false,
    getURL: () => 'http://localhost:5173/?tab=projects',
  };
  const event = { sender: contents, senderFrame: { processId: 21, routingId: 7, url: 'http://localhost:5173/?tab=projects' } };
  assert.equal(isTrustedWorkbenchIPC(event, contents), true);
  assert.equal(isTrustedWorkbenchIPC({ ...event, sender: {} }, contents), false);
  assert.equal(isTrustedWorkbenchIPC({ ...event, senderFrame: { ...event.senderFrame, routingId: 8 } }, contents), false);
  assert.equal(isTrustedWorkbenchIPC({ ...event, senderFrame: { ...event.senderFrame, url: 'https://remote.example/' } }, contents), false);
  assert.equal(isTrustedWorkbenchIPC({ ...event, senderFrame: null }, contents), false);
  contents.getURL = () => 'file:///Users/example/status.html';
  assert.equal(isTrustedWorkbenchIPC(event, contents), false);
  contents.getURL = () => 'http://localhost:5173/';
  contents.isDestroyed = () => true;
  assert.equal(isTrustedWorkbenchIPC(event, contents), false);
});

test('agent task exports require exact project and room scope without secrets or other routes', () => {
  const id = 'df9d3857-3d01-4c24-9e64-b9343e217f9a';
  const base = `http://localhost:8001/api/group-tasks/${id}/export`;
  const scoped = `${base}?project_id=${id}&room_id=${id}&format=md`;
  assert.equal(isExportURL(scoped), true);
  assert.equal(exportFormat(scoped), 'md');
  assert.equal(isExportURL(scoped.replace('format=md', 'format=json')), true);
  for (const url of [`${base}?format=md`, `${base}?project_id=${id}&format=md`, `${scoped}&token=private`, `${scoped}&room_id=${id}`, scoped.replace('format=md', 'format=patch'), scoped.replace('localhost', 'example.com'), `${scoped}#fragment`]) assert.equal(isExportURL(url), false, url);
});
