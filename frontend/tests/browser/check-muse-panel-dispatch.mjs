import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer as createViteServer } from 'vite';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const frontend = fileURLToPath(new URL('../../', import.meta.url));
const virtualOrigin = 'http://127.0.0.1:8001';
const project = { id: 'muse-project', name: 'Muse 测试项目', path: '/fixture', managed: true };
const model = { id: 'fixture-model', label: 'Fixture Model' };
const rooms = ['room-a', 'room-b'].map((id) => ({
  id, project_id: project.id, title: id,
  team_snapshot: { members: [{ id: 'reviewer', name: '审阅员', kind: 'model', model: model.id }] },
}));
const standalonePath = '/__muse-panel-standalone';
const standaloneEntry = '/@id/muse-panel-standalone';
const cacheDir = await mkdtemp(join(tmpdir(), 'modeldesk-muse-panel-'));
const vite = await createViteServer({
  root: frontend,
  cacheDir,
  envFile: false,
  server: { middlewareMode: true, hmr: false, ws: false },
  plugins: [{
    name: 'muse-panel-browser-fixture',
    resolveId(id) {
      if (id === 'muse-panel-standalone') return '\0muse-panel-standalone';
    },
    load(id) {
      if (id !== '\0muse-panel-standalone') return;
      return `import { createElement as e, useState } from 'react';
import { createRoot } from 'react-dom/client';
import MuseDesktopPanel from '/src/components/MuseDesktopPanel.jsx';
import '/src/index.css';
function StandalonePanel() {
  const [projectId, setProject] = useState(() => sessionStorage.getItem('fixture.muse.project') || null);
  const switchProject = (event) => {
    sessionStorage.setItem('fixture.muse.project', event.target.value);
    setProject(event.target.value || null);
  };
  return e('main', {}, e('label', {}, '项目', e('select', { 'aria-label': '独立 Muse 项目', value: projectId || '', onChange: switchProject },
    e('option', { value: '' }, '未选项目'), e('option', { value: ${JSON.stringify(project.id)} }, 'Muse 测试项目'))),
    e(MuseDesktopPanel, { projectId }));
}
createRoot(document.querySelector('#root')).render(e(StandalonePanel));`;
    },
  }],
});
const unexpected = [];
const fixture = createServer((request, response) => {
  const { pathname } = new URL(request.url, 'http://127.0.0.1');
  if (request.method !== 'GET' || pathname.startsWith('/api/')) {
    unexpected.push(`Fixture server received ${request.method} ${pathname}`);
    return response.writeHead(405).end();
  }
  if (pathname === standalonePath) {
    return response.writeHead(200, { 'Content-Type': 'text/html' }).end(
      `<!doctype html><html lang="zh-CN"><body><div id="root"></div><script type="module" src="${standaloneEntry}"></script></body></html>`,
    );
  }
  vite.middlewares(request, response);
});
fixture.listen(0, '127.0.0.1');
await once(fixture, 'listening');
const fixtureOrigin = `http://127.0.0.1:${fixture.address().port}`;
const records = new Map();
const requests = [];
const reads = {
  '/api/workbench/status': { gateway: { online: true }, providers: [], models: [model], tools: {}, platform: { system: 'Darwin', is_macos: true } },
  '/api/workbench/projects': [project],
  '/api/groups': rooms,
  '/api/group-tasks/engines': { engines: [] },
  '/api/muse/status': { installed: true, permission: true, ok: true, composer_ready: true },
};
let dropNextResponse = false;

async function intercept(route) {
  const request = route.request();
  const url = new URL(request.url());
  // No browser request is allowed to reach a running ModelDesk or the real Muse.
  if (url.origin !== virtualOrigin) return route.abort();
  if (url.pathname === '/api/muse/tasks' && request.method() === 'POST') {
    const body = request.postDataJSON();
    requests.push(body);
    const existing = records.get(body.client_request_id);
    if (existing) {
      assert.deepEqual(body, existing.body);
      existing.visible = true;
      return route.fulfill({ json: existing.task });
    }
    const dropResponse = dropNextResponse;
    dropNextResponse = false;
    const task = {
      ...body, id: `task-${records.size + 1}`, status: 'uncertain', sent: null, uncertain: true,
      monitoring: false, remote_state: 'unknown', response: '', created_at: new Date().toISOString(),
    };
    records.set(body.client_request_id, { body, task, visible: !dropResponse });
    return dropResponse ? route.abort('failed') : route.fulfill({ json: task });
  }
  if (request.method() !== 'GET') {
    unexpected.push(`${request.method()} ${url.pathname}`);
    return route.abort();
  }
  if (url.pathname === '/api/muse/tasks') {
    const projectId = url.searchParams.get('project_id');
    const roomId = url.searchParams.get('room_id');
    const list = [...records.values()].filter(({ visible, task }) =>
      visible && task.project_id === projectId && (!roomId || task.room_id === roomId));
    return route.fulfill({ json: list.map(({ task }) => task) });
  }
  if (url.pathname.startsWith('/api/')) {
    const room = rooms.find(({ id }) => url.pathname === `/api/groups/${id}`);
    return route.fulfill({ json: room || (url.pathname.endsWith('/messages') ? { jobs: [] } : reads[url.pathname] ?? []) });
  }
  return route.fulfill({ response: await route.fetch({ url: `${fixtureOrigin}${url.pathname}${url.search}` }) });
}

const draft = (page) => page.getByRole('textbox', { name: 'Muse 任务要求' });
const submit = (page) => page.getByRole('button', { name: '派发 Muse', exact: true });
const retry = (page) => page.getByRole('button', { name: '重试上次派发', exact: true });
const openGroupPanel = async (page) => {
  await page.getByRole('button', { name: '更多群聊操作' }).click();
  await page.getByRole('button', { name: /Muse 面板$/ }).click();
  await draft(page).waitFor();
};
const closeGroupPanel = (page) => page.getByRole('dialog', { name: 'Muse 任务与连接' }).getByRole('button', { name: '关闭', exact: true }).click();
const switchRoom = (page, id) => page.getByRole('combobox', { name: '打开项目群聊' }).selectOption(id);
const openStandalonePanel = async (page) => {
  await page.locator('.muse-desktop-panel > summary').click();
  await draft(page).waitFor();
};
const switchStandaloneProject = (page, id) => page.getByRole('combobox', { name: '独立 Muse 项目' }).selectOption(id);

async function submitWithoutResponse(page, prompt, projectId, roomId) {
  const count = requests.length;
  await draft(page).fill(prompt);
  dropNextResponse = true;
  await submit(page).click();
  await retry(page).waitFor();
  assert.equal(requests.length, count + 1);
  const original = requests.at(-1);
  assert.match(original.client_request_id, /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
  assert.deepEqual([original.project_id, original.room_id], [projectId, roomId]);
  assert.equal(await draft(page).inputValue(), prompt);
  assert.equal(await submit(page).isDisabled(), true);
  return original;
}

let browser;
try {
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, serviceWorkers: 'block' });
  await context.route('**/*', intercept);
  await context.routeWebSocket('**/*', () => {});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => unexpected.push(error.message));
  await page.addInitScript(({ projectId, roomId }) => {
    if (localStorage.getItem('modeldesk.activeProject')) return;
    localStorage.setItem('modeldesk.activeProject', projectId);
    localStorage.setItem('modeldesk.view', 'groups');
    localStorage.setItem(`modeldesk.groups.v1:${projectId}`, JSON.stringify({ roomId }));
  }, { projectId: project.id, roomId: rooms[0].id });

  await page.goto(virtualOrigin);
  await openGroupPanel(page);
  const roomRequest = await submitWithoutResponse(page, '群聊 Muse 原始任务', project.id, rooms[0].id);
  await draft(page).fill('群聊更新后的草稿');
  await closeGroupPanel(page);
  await switchRoom(page, rooms[1].id);
  await openGroupPanel(page);
  assert.equal(await draft(page).inputValue(), '');
  assert.equal(await retry(page).count(), 0);
  await closeGroupPanel(page);
  await switchRoom(page, rooms[0].id);
  await openGroupPanel(page);
  await retry(page).waitFor();
  await page.reload();
  await openGroupPanel(page);
  assert.equal(await draft(page).inputValue(), '群聊更新后的草稿');
  await retry(page).click();
  await page.getByText('上次任务记录已确认，当前草稿已保留。', { exact: true }).waitFor();
  await page.getByText('送达或回复未确认', { exact: true }).last().waitFor();
  assert.equal(await draft(page).inputValue(), '群聊更新后的草稿');
  assert.deepEqual(requests, [roomRequest, roomRequest]);
  assert.equal(records.size, 1);

  await page.goto(`${virtualOrigin}${standalonePath}`);
  await openStandalonePanel(page);
  const standaloneRequest = await submitWithoutResponse(page, '独立 Muse 原始任务', null, null);
  await draft(page).fill('独立面板更新后的草稿');
  await switchStandaloneProject(page, project.id);
  await openStandalonePanel(page);
  assert.equal(await draft(page).inputValue(), '');
  assert.equal(await retry(page).count(), 0);
  assert.equal(await page.getByRole('combobox', { name: '查看 Muse 任务' }).count(), 0);
  await draft(page).fill('另一个项目的 Muse 草稿');
  await switchStandaloneProject(page, '');
  await openStandalonePanel(page);
  await retry(page).waitFor();
  assert.equal(await draft(page).inputValue(), '独立面板更新后的草稿');
  records.get(standaloneRequest.client_request_id).visible = true;
  await page.reload();
  await openStandalonePanel(page);
  await page.getByText('上次任务记录已确认，当前草稿已保留。', { exact: true }).waitFor();
  assert.equal(await retry(page).count(), 0);
  assert.equal(await draft(page).inputValue(), '独立面板更新后的草稿');
  assert.deepEqual(requests, [roomRequest, roomRequest, standaloneRequest]);
  assert.equal(records.size, 2);
  await switchStandaloneProject(page, project.id);
  await openStandalonePanel(page);
  assert.equal(await draft(page).inputValue(), '另一个项目的 Muse 草稿');
  assert.equal(await retry(page).count(), 0);
  assert.deepEqual(unexpected, []);
  console.log('Muse panel passed room retry, unassigned history recovery, reload, project isolation, and draft preservation. Two fixture creations across three POST attempts.');
} finally {
  await browser?.close();
  await vite.close();
  await new Promise((resolve) => fixture.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
