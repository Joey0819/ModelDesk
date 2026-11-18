import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const awaitMatch = async (read, expected) => {
  for (let index = 0; index < 80; index++) {
    if (await read() === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(await read(), expected);
};
const virtualOrigin = 'http://127.0.0.1:8001';
const project = { id: 'fixture-project', name: '派发测试项目', path: '/fixture', managed: true };
const model = { id: 'fixture-llm', label: 'Fixture LLM' };
const members = [
  { id: 'codex', name: 'Codex', kind: 'codex' }, { id: 'claude', name: 'Claude', kind: 'claude' },
  { id: 'muse', name: 'Muse', kind: 'muse' }, { id: 'reviewer', name: '独立审阅', kind: 'model', model: model.id },
];
const engines = [
  { id: 'codex', available: true, authenticated: true, capabilities: ['code', 'review'] },
  { id: 'claude', available: true, authenticated: true, capabilities: ['code', 'review'] },
  { id: 'member_model', available: true, authenticated: true, capabilities: ['review'] },
];
const rooms = ['room-a', 'room-b'].map((id, index) => ({ id, project_id: project.id, title: `派发测试群 ${index + 1}`, team_snapshot: { members } }));
const source = {
  id: 'saved-patch', project_id: project.id, room_id: rooms[0].id, kind: 'code', engine: 'codex',
  member_id: 'codex', member: members[0], status: 'complete', prompt: '原代码任务', output: '已确认参考',
  diff_sha256: 'a'.repeat(64), changed_files: [{ path: 'fixture.txt', status: 'M' }], created_at: '2026-10-01T08:00:00Z',
};
const fixture = await startNavigationFixture();
const screenshots = process.env.SCREENSHOT_DIR;
if (screenshots) await mkdir(screenshots, { recursive: true });
const shot = (page, name) => screenshots && page.screenshot({ path: join(screenshots, `${name}.png`) });
const reads = {
  '/api/workbench/status': { gateway: { online: true }, providers: [], models: [model], tools: {}, platform: { system: 'Darwin', is_macos: true } },
  '/api/workbench/projects': [project], '/api/groups': rooms,
  '/api/group-tasks/engines': { engines }, '/api/muse/status': { installed: true, permission: true, ok: true, composer_ready: true },
  '/api/skills': { installed: [], available: [] },
};
const runs = new Map();
const posts = [];
const errors = [];
let hideNextReceipt = false;
let rejectNextDispatch = false;
const uncertainty = '旧派发记录还无法核对。请先恢复记录，再重新检查。';
let browser;

async function transport(route) {
  const request = route.request();
  const url = new URL(request.url());
  // The browser never reaches any running ModelDesk instance, even for the local-only Muse UI.
  if (url.origin !== virtualOrigin) return route.abort();
  if (!url.pathname.startsWith('/api/')) {
    if (request.method() !== 'GET') return route.abort();
    return route.fulfill({ response: await route.fetch({ url: `${fixture.origin}${url.pathname}${url.search}` }) });
  }
  const muse = url.pathname === '/api/muse/tasks';
  const agent = url.pathname === '/api/group-tasks';
  if (request.method() === 'POST' && (muse || agent)) {
    const body = request.postDataJSON();
    const key = `${url.pathname}:${body.project_id}:${body.room_id}:${body.client_request_id}`;
    const previous = runs.get(key);
    posts.push({ path: url.pathname, body });
    if (rejectNextDispatch) {
      rejectNextDispatch = false;
      return route.fulfill({ status: 409, json: { detail: '这项改动已经不能审阅。' } });
    }
    if (previous) {
      assert.deepEqual(body, previous.body);
      if (previous.unverified) return route.fulfill({ status: 409, json: { detail: { code: 'dispatch_receipt_unverified', message: uncertainty } } });
      previous.hidden = false;
      return route.fulfill({ json: previous.task });
    }
    const hidden = hideNextReceipt;
    hideNextReceipt = false;
    const member = members.find((entry) => entry.id === body.member_id);
    const task = { ...body, id: `fixture-created-${runs.size + 1}`, member, status: 'complete', output: '已完成', response: '已观察回复', created_at: new Date().toISOString() };
    runs.set(key, { task, body, muse, hidden });
    if (hidden) return route.abort('failed');
    return route.fulfill({ json: task });
  }
  if (request.method() !== 'GET') {
    errors.push(`Unexpected ${request.method()} ${url.pathname}`);
    return route.abort();
  }
  const scope = url.searchParams.get('room_id');
  const room = rooms.find((item) => url.pathname === `/api/groups/${item.id}`);
  const jobs = rooms.some((item) => url.pathname === `/api/groups/${item.id}/messages`);
  const taskList = () => [...(agent && scope === source.room_id ? [source] : []), ...[...runs.values()].filter((entry) => entry.muse === muse && !entry.hidden && entry.task.room_id === scope).map((entry) => entry.task)];
  return route.fulfill({ json: room || (jobs ? { jobs: [] } : muse || agent ? taskList() : reads[url.pathname] ?? []) });
}

const checkReceipt = (page) => page.getByRole('button', { name: '重新检查上次派发', exact: true });
const retry = (page) => page.getByRole('button', { name: '重试上次派发', exact: true });
const composer = (page) => page.getByRole('textbox', { name: '群聊消息' });
const formDraft = (page, kind) => page.getByRole('textbox', { name: kind === 'review' ? 'Agent 审阅要求' : 'Agent 执行任务' });
const switchRoom = (page, id) => page.getByRole('combobox', { name: '打开项目群聊' }).selectOption(id);
const click = (page, label) => page.getByRole('button', { name: label, exact: true }).click();
const noRetry = (page) => retry(page).waitFor({ state: 'hidden' });
const savedAttempt = (page, channel) => page.evaluate(({ groupId, roomId, channelId }) => JSON.parse(localStorage.getItem(`modeldesk.groups.v1:${groupId}:${roomId}:dispatch:${channelId}`) || 'null'), { groupId: project.id, roomId: rooms[0].id, channelId: channel });
async function mention(page, name, message) {
  await composer(page).fill(`@${name}`);
  await page.getByRole('option', { name: new RegExp(`^@${name}`) }).click();
  await composer(page).fill(message);
}
async function lostResponse(page, submit, channel) {
  const count = posts.length;
  hideNextReceipt = true;
  await click(page, submit);
  await retry(page).waitFor();
  assert.equal(posts.length, count + 1);
  const record = posts.at(-1);
  assert.match(record.body.client_request_id, /^[0-9a-f-]{36}$/);
  assert.equal((await savedAttempt(page, channel)).id, record.body.client_request_id);
  return record;
}
async function replay(page, first) {
  const count = posts.length;
  await retry(page).click();
  await noRetry(page);
  assert.equal(posts.length, count + 1);
  assert.deepEqual(posts.at(-1), first);
}

try {
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1370, height: 900 }, serviceWorkers: 'block' });
  await context.route('**/*', transport);
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => { errors.push(error.message); console.error(error); });
  await page.addInitScript(({ projectId, roomId }) => {
    if (localStorage.getItem('modeldesk.activeProject')) return;
    localStorage.setItem('modeldesk.activeProject', projectId);
    localStorage.setItem('modeldesk.view', 'groups');
    localStorage.setItem(`modeldesk.groups.v1:${projectId}`, JSON.stringify({ roomId }));
  }, { projectId: project.id, roomId: rooms[0].id });
  await page.goto(virtualOrigin);
  await mention(page, 'Codex', '首个 Codex 任务');
  const first = await lostResponse(page, '派发 Codex', 'composer');
  assert.equal(first.body.engine, 'codex');
  await mention(page, 'Claude', '编辑后的 Claude 草稿');
  await switchRoom(page, rooms[1].id);
  await composer(page).waitFor();
  assert.equal(await composer(page).inputValue(), '');
  assert.equal(await retry(page).count(), 0);
  await switchRoom(page, rooms[0].id);
  await retry(page).waitFor();
  await page.reload();
  await retry(page).waitFor();
  assert.equal(await composer(page).inputValue(), '编辑后的 Claude 草稿');
  assert.equal(await page.getByRole('button', { name: '派发 Claude Code', exact: true }).isDisabled(), true);
  await shot(page, 'group-dispatch-pending');
  await replay(page, first);
  await page.getByText('上次派发已确认，当前草稿已保留。', { exact: true }).waitFor();
  assert.equal(await composer(page).inputValue(), '编辑后的 Claude 草稿');
  await shot(page, 'group-dispatch-preserved-draft');
  await click(page, '派发 Claude Code');
  await awaitMatch(() => composer(page).inputValue(), '');
  assert.equal(posts.at(-1).body.engine, 'claude');
  assert.notEqual(posts.at(-1).body.client_request_id, first.body.client_request_id);

  await mention(page, 'Muse', '首个 Muse 任务');
  const muse = await lostResponse(page, '派发 Muse', 'composer');
  assert.equal(muse.path, '/api/muse/tasks');
  const museRun = [...runs.values()].find((entry) => entry.body.client_request_id === muse.body.client_request_id);
  const countBeforeRecovery = posts.length;
  museRun.hidden = false;
  await page.reload();
  await composer(page).waitFor();
  await awaitMatch(() => composer(page).inputValue(), '');
  await noRetry(page);
  await awaitMatch(() => savedAttempt(page, 'composer'), null);
  assert.equal(posts.length, countBeforeRecovery);

  await click(page, '代码 Agent');
  await formDraft(page, 'code').fill('代码表单任务');
  const code = await lostResponse(page, '分配任务', 'form');
  assert.equal(code.body.kind, 'code');
  await formDraft(page, 'code').fill('未派发的代码草稿');
  await switchRoom(page, rooms[1].id);
  await composer(page).waitFor();
  assert.equal(await retry(page).count(), 0);
  await switchRoom(page, rooms[0].id);
  await retry(page).waitFor();
  await page.reload();
  await retry(page).waitFor();
  assert.equal(await formDraft(page, 'code').inputValue(), '未派发的代码草稿');
  await replay(page, code);
  await page.getByText('上次派发已确认，当前草稿已保留。', { exact: true }).waitFor();
  assert.equal(await formDraft(page, 'code').inputValue(), '未派发的代码草稿');
  await awaitMatch(() => page.getByRole('button', { name: '分配任务', exact: true }).isEnabled(), true);

  await click(page, '请另一位成员审阅');
  await formDraft(page, 'review').fill('审阅表单任务');
  const review = await lostResponse(page, '开始独立审阅', 'form');
  assert.equal(review.body.kind, 'review');
  assert.equal(review.body.source_task_id, source.id);
  await formDraft(page, 'review').fill('未派发的审阅草稿');
  await page.reload();
  await retry(page).waitFor();
  await replay(page, review);
  await page.getByText('上次派发已确认，当前草稿已保留。', { exact: true }).waitFor();
  assert.equal(await formDraft(page, 'review').inputValue(), '未派发的审阅草稿');
  await awaitMatch(() => page.getByRole('button', { name: '开始独立审阅', exact: true }).isEnabled(), true);
  await shot(page, 'group-dispatch-review-preserved');

  await formDraft(page, 'review').fill('等待核对的审阅');
  const uncertainReview = await lostResponse(page, '开始独立审阅', 'form');
  const uncertainRun = [...runs.values()].find((entry) => entry.body.client_request_id === uncertainReview.body.client_request_id);
  uncertainRun.unverified = true;
  await retry(page).click();
  await checkReceipt(page).waitFor();
  await page.getByRole('alert').filter({ hasText: uncertainty }).waitFor();
  assert.deepEqual(posts.at(-1), uncertainReview);
  await formDraft(page, 'review').fill('回执不明时编辑的审阅草稿');
  uncertainRun.hidden = false;
  await page.reload();
  await checkReceipt(page).waitFor();
  await page.getByRole('alert').filter({ hasText: uncertainty }).waitFor();
  assert.equal((await savedAttempt(page, 'form')).id, uncertainReview.body.client_request_id);
  assert.equal(await page.getByRole('button', { name: '开始独立审阅', exact: true }).isDisabled(), true);
  await shot(page, 'group-dispatch-unverified-receipt');
  await switchRoom(page, rooms[1].id);
  await mention(page, 'Claude', '在另一个群聊执行的新任务');
  await click(page, '派发 Claude Code');
  await awaitMatch(() => composer(page).inputValue(), '');
  assert.equal(posts.at(-1).body.room_id, rooms[1].id);
  assert.notEqual(posts.at(-1).body.client_request_id, uncertainReview.body.client_request_id);
  await switchRoom(page, rooms[0].id);
  await checkReceipt(page).waitFor();
  assert.equal(await formDraft(page, 'review').inputValue(), '回执不明时编辑的审阅草稿');
  await checkReceipt(page).click();
  await checkReceipt(page).waitFor();
  assert.deepEqual(posts.at(-1), uncertainReview);
  assert.equal((await savedAttempt(page, 'form')).id, uncertainReview.body.client_request_id);
  uncertainRun.unverified = false;
  await checkReceipt(page).click();
  await page.getByText('上次派发已确认，当前草稿已保留。', { exact: true }).waitFor();
  await awaitMatch(() => savedAttempt(page, 'form'), null);
  assert.deepEqual(posts.at(-1), uncertainReview);
  assert.equal(await formDraft(page, 'review').inputValue(), '回执不明时编辑的审阅草稿');

  rejectNextDispatch = true;
  await click(page, '开始独立审阅');
  await page.getByRole('alert').filter({ hasText: '这项改动已经不能审阅。' }).waitFor();
  assert.equal(await checkReceipt(page).count(), 0);
  assert.equal(await retry(page).count(), 0);
  assert.equal(await savedAttempt(page, 'form'), null);
  assert.equal(await formDraft(page, 'review').inputValue(), '回执不明时编辑的审阅草稿');
  assert.deepEqual(fixture.writes, []);
  assert.deepEqual(errors, []);
  assert.equal(runs.size, 7);
  assert.equal(posts.length, 14);
  console.log('Group dispatch passed Codex, Claude, Muse, code and review with lost responses, draft edits, reload and room isolation. Unverified and business 409 responses handled separately. Seven unique tasks across fourteen POST attempts.');
} finally {
  await browser?.close();
  await fixture.close();
}
