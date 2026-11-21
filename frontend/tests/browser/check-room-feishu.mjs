import assert from 'node:assert/strict';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const projectId = 'fixture-project';
const rooms = [
  'ab000001-cd00-4000-8000-000000000001',
  'ab000002-cd00-4000-8000-000000000002',
].map((id) => ({
  id, project_id: projectId, title: '编辑组', team_snapshot: { members: [
    { id: 'writer', name: '作者', kind: 'model', model: 'gpt-6.1-sol' },
    { id: 'reviewer', name: '审阅', kind: 'model', model: 'gemini-3-flash' },
  ] },
}));
const globalReads = [];
const observedRooms = new Set();
const fixture = await startNavigationFixture(undefined, (url, response) => {
  const send = (value) => {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
    return true;
  };
  if (url.pathname.startsWith('/api/feishu')) globalReads.push(url.pathname);
  if (url.pathname === '/api/groups') return send(rooms);
  const room = rooms.find((item) => url.pathname.startsWith('/api/groups/' + item.id));
  if (!room) return false;
  if (url.pathname.endsWith('/feishu')) {
    assert.equal(url.searchParams.get('project_id'), projectId);
    observedRooms.add(room.id);
    return send({ room_id: room.id, project_id: projectId, mode: 'all_groups', status: 'connected',
      destination: 'private', switch_command: '/modeldesk use ' + room.id,
      bot_url: 'https://applink.feishu.cn/client/bot/open?appId=cli_fixture' });
  }
  if (url.pathname.endsWith(room.id)) return send(room);
  return send(url.pathname.endsWith('/jobs') ? { jobs: [] } : []);
});

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE,
    channel: process.env.CHROMIUM_EXECUTABLE ? undefined : 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 320, height: 800 } });
  await page.addInitScript(({ projectId, roomId }) => {
    localStorage.setItem('modeldesk.view', 'groups');
    localStorage.setItem('modeldesk.activeProject', projectId);
    localStorage.setItem('modeldesk.groups.v1:' + projectId, JSON.stringify({roomId}));
  }, { projectId, roomId: rooms[0].id });
  await page.goto(fixture.origin);

  for (const room of rooms) {
    await page.getByRole('combobox', { name: '打开项目群聊' }).selectOption(room.id);
    const trigger = page.getByRole('button', { name: '飞书机器人', exact: true });
    await trigger.waitFor();
    const box = await trigger.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= 320);
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: '飞书机器人连接' });
    await dialog.getByText('/modeldesk use ' + room.id, { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '复制切换指令' }).count(), 1);
    assert.equal(await dialog.getByRole('button', { name: '飞书连接设置' }).count(), 0);
    await dialog.getByRole('button', { name: '关闭飞书连接' }).click();
  }
  assert.equal(observedRooms.size, 2);
  assert.deepEqual(globalReads, []);
  assert.deepEqual(fixture.writes, []);
  console.log('Paired-phone room entry remained visible and selected the right route for both same-name groups.');
} finally {
  await browser?.close();
  await fixture.close();
}
