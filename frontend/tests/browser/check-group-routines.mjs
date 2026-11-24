import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const virtualOrigin = 'http://127.0.0.1:8001';
const project = { id: 'routine-project', name: '例程测试项目', path: '/fixture', managed: true };
const model = { id: 'fixture-llm', label: 'Fixture LLM' };
const rooms = ['room-a', 'room-b'].map((id, index) => ({
  id, project_id: project.id, title: `例程测试群 ${index + 1}`,
  team_snapshot: { members: [{ id: 'reviewer', name: '独立审阅', kind: 'model', model: model.id }] },
}));
const createdAt = '2026-11-19T09:00:00Z';
const job = {
  id: 'verified-job', mode: 'group', project_id: project.id, group_room_id: rooms[0].id, group_delivery_mode: 'auto',
  status: 'complete', created_at: createdAt, prompt: '运行每周简报',
  result: { delivery: { status: 'complete', title: '每周简报', content: '本周分析已完成。' } },
};
const reads = {
  '/api/workbench/status': { gateway: { online: true }, providers: [], models: [model], tools: {} },
  '/api/workbench/projects': [project], '/api/groups': rooms, '/api/group-tasks/engines': { engines: [] },
  '/api/workbench/jobs/verified-job': job,
};
const routines = new Map();
const runs = new Map();
const createPosts = [];
const runPosts = [];
const enabledPosts = [];
const errors = [];
let browser;

async function transport(route) {
  const request = route.request();
  const url = new URL(request.url());
  const method = request.method();
  if (url.origin !== virtualOrigin) return route.abort();
  if (!url.pathname.startsWith('/api/')) {
    if (method !== 'GET') return route.abort();
    return route.fulfill({ response: await route.fetch({ url: `${fixture.origin}${url.pathname}${url.search}` }) });
  }

  const [, , namespace, roomId, resource, id, action] = url.pathname.split('/');
  const room = namespace === 'groups' && rooms.find((item) => item.id === roomId);
  const reply = (json, status = 200) => route.fulfill({ status, json });
  if (!room || resource !== 'routines') {
    if (method === 'GET') return reply(room ? resource === 'messages' ? { jobs: [] } : room : reads[url.pathname] ?? []);
  } else {
    assert.equal(url.searchParams.get('project_id'), project.id);
    if (method === 'GET' && !id) {
      return reply({ routines: [...routines.values()].filter((item) => item.visible && item.room_id === roomId).map((item) => ({
        ...item, recent_runs: [...runs.values()].filter((run) => run.visible && run.routine_id === item.id),
      })) });
    }
    if (method === 'POST' && !id) {
      const body = request.postDataJSON();
      createPosts.push({ roomId, body });
      const previous = routines.get(body.client_request_id);
      if (previous) {
        previous.visible = true;
        return reply(previous, 201);
      }
      const visible = createPosts.length > 1;
      const created = { ...body, id: `routine-${routines.size + 1}`, room_id: roomId, project_id: project.id, revision: 1, enabled: false, visible, recent_runs: [] };
      routines.set(body.client_request_id, created);
      // Persist the mutation while simulating a dropped reply and unavailable list receipt.
      return visible ? reply(created, 201) : route.abort('failed');
    }
    const routine = [...routines.values()].find((item) => item.id === id && item.room_id === roomId);
    if (method === 'POST' && routine && action === 'run') {
      const body = request.postDataJSON();
      runPosts.push({ id, body });
      const previous = runs.get(body.client_request_id);
      if (previous) {
        previous.visible = true;
        return reply(previous, 202);
      }
      const run = { ...body, id: `run-${runs.size + 1}`, routine_id: id, state: 'queued', created_at: createdAt, trigger_kind: 'manual' };
      runs.set(body.client_request_id, run);
      return route.abort('failed');
    }
    if (method === 'POST' && routine && action === 'enabled') {
      const body = request.postDataJSON();
      assert.equal(body.expected_revision, routine.revision);
      enabledPosts.push(body.enabled);
      Object.assign(routine, { enabled: body.enabled, revision: routine.revision + 1, next_run_at: body.enabled ? '2026-11-23T14:30:00Z' : null });
      return reply(routine);
    }
  }

  errors.push(`Unexpected ${method} ${url.pathname}`);
  return route.abort();
}

try {
  const screenshots = process.env.SCREENSHOT_DIR;
  if (screenshots) await mkdir(screenshots, { recursive: true });
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1370, height: 885 }, timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', transport);
  await page.addInitScript(({ projectId, roomId }) => {
    if (localStorage.getItem('modeldesk.activeProject')) return;
    localStorage.setItem('modeldesk.activeProject', projectId);
    localStorage.setItem('modeldesk.view', 'groups');
    localStorage.setItem(`modeldesk.groups.v1:${projectId}`, JSON.stringify({ roomId }));
  }, { projectId: project.id, roomId: rooms[0].id });
  await page.goto(virtualOrigin);

  const dialog = page.getByRole('dialog', { name: '群聊自动化', exact: true });
  const button = (name, scope = dialog) => scope.getByRole('button', { name, exact: true });
  const open = () => button('群聊自动化', page).click();
  const shot = (name) => screenshots && page.screenshot({ path: join(screenshots, `${name}.png`) });
  const composer = page.getByRole('textbox', { name: '群聊消息' });
  const goal = dialog.getByLabel('预期成果');
  const prompt = '生成每周简报，列出进度、风险和负责人。';

  await composer.fill(prompt);
  await open();
  await button('新建例程').click();
  assert.equal(await goal.inputValue(), '');
  await button('使用群聊草稿').click();
  assert.equal(await goal.inputValue(), prompt);
  await dialog.getByLabel('例程名称').fill('每周简报');
  await dialog.getByLabel('执行步骤').fill('汇总项目变化并列出来源。');
  await dialog.getByLabel('验收标准').fill('核对日期与来源。');
  await dialog.getByLabel('触发方式').selectOption('weekly');
  assert.equal(await dialog.getByLabel('时区').inputValue(), 'Asia/Shanghai');
  await dialog.getByLabel('时区').fill('America/New_York');
  await dialog.getByLabel('每周哪天').selectOption('0');
  await dialog.getByLabel('运行时间').fill('09:30');
  for (const format of ['Word', 'PDF', 'HTML']) await dialog.getByRole('checkbox', { name: format, exact: true }).check();
  await shot('group-routine-editor-desktop');

  await button('保存例程').click();
  await button('重试保存').waitFor();
  await page.reload();
  await open();
  assert.equal(await goal.inputValue(), prompt);
  await button('重试保存').click();
  await button('运行一次').waitFor();
  assert.equal(routines.size, 1);
  assert.equal(createPosts.length, 2);
  assert.deepEqual(createPosts[0], createPosts[1]);
  const { body } = createPosts[0];
  assert.match(body.client_request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(body.trigger, { kind: 'weekly', time: '09:30', timezone: 'America/New_York', weekday: 0 });
  assert.deepEqual(body.document_formats, ['docx', 'pdf', 'html']);
  assert.equal(body.allow_native, false);
  assert.equal(body.enabled, undefined);
  assert.deepEqual(enabledPosts, []);

  await button('运行一次').click();
  await button('重试运行请求').waitFor();
  await page.reload();
  await open();
  await button('重试运行请求').click();
  await dialog.getByText('已接收，等待执行', { exact: true }).first().waitFor();
  assert.equal(runs.size, 1);
  assert.equal(runPosts.length, 2);
  assert.deepEqual(runPosts[0], runPosts[1]);
  assert.equal(await button('查看运行记录').count(), 0);
  Object.assign([...runs.values()][0], { job_id: job.id, state: 'complete' });
  await button('查看运行记录').waitFor();

  await button('启用').click();
  await dialog.getByText('下次运行', { exact: false }).waitFor();
  await shot('group-routine-enabled-desktop');
  await button('暂停').click();
  await button('启用').waitFor();
  assert.deepEqual(enabledPosts, [true, false]);
  await button('查看运行记录').click();
  await page.locator('[data-group-job="verified-job"]:focus').waitFor();
  const completedTurn = page.locator('[data-group-job="verified-job"]');
  await completedTurn.getByLabel('成果正文摘录').getByText(job.result.delivery.content, { exact: true }).waitFor();
  await button('查看完整成果', completedTurn).click();
  await page.locator('.artifact-panel .artifact-preview-scroll').getByText(job.result.delivery.content, { exact: true }).waitFor();
  await button('收起成果面板', page).click();
  assert.equal(await composer.inputValue(), prompt);

  await page.getByLabel('打开项目群聊').selectOption(rooms[1].id);
  await page.setViewportSize({ width: 390, height: 844 });
  await button('更多群聊操作', page).click();
  await button('群聊自动化', page.getByRole('dialog', { name: '群聊操作' })).click();
  await button('新建例程').click();
  assert.equal(await goal.inputValue(), '');
  assert.equal(await button('使用群聊草稿').count(), 0);
  await dialog.getByLabel('例程名称').fill('新卡片摘要');
  await goal.fill('从新卡片提取交付要求。');
  await dialog.getByLabel('触发方式').selectOption('board_card');
  await dialog.getByLabel('卡片标签').fill('报告');
  const save = button('保存例程');
  assert.equal(await save.evaluate((element) => {
    const button = element.getBoundingClientRect();
    const modal = element.closest('dialog').getBoundingClientRect();
    return button.bottom < modal.bottom && modal.right <= innerWidth && modal.bottom <= innerHeight;
  }), true);
  await shot('group-routine-editor-mobile');
  await save.click();
  await button('运行一次').waitFor();
  assert.equal(routines.size, 2);
  assert.equal(createPosts.at(-1).roomId, rooms[1].id);
  assert.deepEqual(createPosts.at(-1).body.trigger, { kind: 'board_card', tag: '报告' });
  assert.deepEqual(errors, []);
  assert.deepEqual(fixture.writes, []);
  console.log('Group routines passed scoped creation, preserved draft, one durable create and run after retry, verified job navigation, enable and pause, and the 390px card-trigger editor.');
} finally {
  await browser?.close();
  await fixture.close();
}
