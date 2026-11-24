import assert from 'node:assert/strict';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const projects = [
  { id: 'fixture-project', name: '甲项目', managed: true, instructions: '甲的工作要求', path: '/fixture/a' },
  { id: 'fixture-second', name: '乙项目', managed: true, instructions: '乙的工作要求', path: '/fixture/b' },
];
const rooms = ['稿件讨论', '运行验证'].map((title, index) => ({
  id: `ab00000${index + 1}-cd00-4000-8000-000000000001`, title,
  project_id: projects[0].id, team_snapshot: { members: [] },
}));
const asset = { id: 'fixture-asset', name: '共享研究手册.txt', kind: 'text', status: 'ready', shared: true, size: 22 };
const prompts = [
  ...projects.map((project) => ({ id: `${project.id}-prompt`, name: `${project.name}提示词`, content: project.instructions, project_id: project.id })),
  { id: 'global-prompt', name: '全局提示词', content: '全局工作要求', project_id: null },
];
const agent = {
  id: 'fixture-agent', name: '研究资料审阅员', role: '审阅员', goal: '核对实际资料', model: 'gpt-6.1-sol',
  project_id: projects[0].id, tools: [],
};
const flow = {
  id: 'fixture-flow', name: '核查研究记录', description: '先读取资料，再复核记录',
  project_id: projects[0].id, mode: 'camel', models: ['gpt-6.1-sol', 'gemini-3-flash'],
};

function readFixture(url, response) {
  const send = (value) => {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
    return true;
  };
  const projectId = url.searchParams.get('project_id');
  if (url.pathname === '/api/workbench/projects') return send(projects);
  if (url.pathname === `/api/workbench/projects/${projects[0].id}/assets`) return send([asset]);
  const project = projects.find((item) => url.pathname === `/api/workbench/projects/${item.id}`);
  if (project) return send(project);
  if (url.pathname === '/api/groups/catalog') return send(rooms);
  if (url.pathname === '/api/groups') return send(rooms.filter((room) => room.project_id === projectId));
  const room = rooms.find((item) => url.pathname === `/api/groups/${item.id}`);
  if (room) return send(room);
  if (/^\/api\/groups\/[^/]+\/messages$/.test(url.pathname)) return send({ jobs: [] });
  const resources = { '/api/platform/prompts': prompts, '/api/platform/agents': [agent], '/api/platform/flows': [flow] };
  if (resources[url.pathname]) {
    const expectedOwner = url.searchParams.get('scope') === 'global' ? null : projectId;
    return send(url.searchParams.get('archived') === 'true' ? [] : resources[url.pathname].filter((item) => item.project_id === expectedOwner));
  }
  return false;
}

async function checkSdkAttachments(page) {
  const sdk = page.locator('.sdk-studio');
  assert.equal(await sdk.locator('label .attachment-composer').count(), 0);
  assert.deepEqual(await sdk.locator('input[type=file]').evaluateAll((inputs) => inputs.map((input) => getComputedStyle(input).display)), ['none', 'none']);
  await sdk.getByRole('textbox', { name: '本次运行说明' }).fill('使用上传的资料');

  const uploaded = { ...asset, id: 'sdk-text', name: 'sdk-notes.txt', shared: false };
  const assetsPath = `**/api/workbench/projects/${projects[0].id}/assets`;
  let uploadRequest;
  await page.route(`${assetsPath}/batch`, async (route) => {
    uploadRequest = route.request().postData();
    await route.fulfill({ json: { assets: [uploaded] } });
  });
  await page.route(assetsPath, async (route) => {
    await route.fulfill({ json: [asset, uploaded] });
  });
  await sdk.getByRole('button', { name: '添加图片、文件或文件夹' }).click();
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    sdk.getByRole('button', { name: '图片或文件', exact: true }).click(),
  ]);
  await chooser.setFiles({ name: uploaded.name, mimeType: 'text/plain', buffer: Buffer.from('SDK test fixture') });
  const chip = sdk.getByRole('button', { name: '移除本轮附件 sdk-notes.txt' });
  await chip.waitFor();
  assert.match(uploadRequest, /SDK test fixture/);
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, 'after-sdk-attachment.png') });
  await chip.click();
  await chip.waitFor({ state: 'hidden' });
  await page.unroute(assetsPath);
  await page.unroute(`${assetsPath}/batch`);
}

async function checkLibraryBrowsing(page, project, resourceNav) {
  await resourceNav.getByRole('button', { name: '智能体与流程', exact: true }).click();
  const agents = page.getByRole('region', { name: '智能体库', exact: true });
  const flows = page.getByRole('region', { name: '流程库', exact: true });
  const editor = page.locator('.resource-editor');
  const agentRow = agents.getByRole('button', { name: new RegExp(agent.name) });
  await agentRow.waitFor();
  assert.equal(await editor.count(), 0);
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, 'after-agent-library.png') });
  await agents.getByRole('textbox', { name: '搜索资源' }).fill('不存在的模板');
  await agents.getByRole('status').filter({ hasText: '没有匹配的资源' }).waitFor();
  await agents.getByRole('textbox', { name: '搜索资源' }).clear();
  await agentRow.click();
  await editor.getByRole('heading', { name: agent.name }).waitFor();
  assert.equal(await editor.getByRole('textbox', { name: '目标' }).inputValue(), agent.goal);
  await editor.getByRole('button', { name: '返回智能体库' }).click();
  assert.ok(await agentRow.evaluate((element) => document.activeElement === element));

  await project.selectOption(projects[1].id);
  await agents.getByText('还没有智能体。').waitFor();
  await agents.getByRole('button', { name: '新建智能体' }).click();
  await editor.getByRole('heading', { name: '新建智能体' }).waitFor();
  await editor.getByRole('textbox', { name: '名称', exact: true }).fill('独立审阅助手');
  await editor.getByRole('textbox', { name: '角色名称' }).fill('审阅助手');
  await editor.getByRole('textbox', { name: '目标' }).fill('核对引用');
  await editor.getByRole('combobox', { name: '保存位置' }).selectOption('');
  let agentSave;
  await page.route('**/api/platform/agents', async (route) => {
    assert.equal(route.request().method(), 'POST');
    agentSave = route.request().postDataJSON();
    await route.fulfill({ json: { ...agentSave, id: 'created-agent' } });
  });
  await editor.getByRole('button', { name: '保存', exact: true }).click();
  await editor.getByText('已保存到本机。').waitFor();
  assert.equal(agentSave.name, '独立审阅助手');
  assert.equal(agentSave.project_id, null);

  await page.getByRole('button', { name: '流程库与条件分支' }).click();
  await flows.getByText('还没有流程。').waitFor();
  await flows.getByRole('button', { name: '新建流程' }).click();
  await editor.getByRole('heading', { name: '新建流程' }).waitFor();
  await editor.getByRole('button', { name: '返回流程库' }).click();
  await project.selectOption(projects[0].id);
  const flowRow = flows.getByRole('button', { name: new RegExp(flow.name) });
  await flowRow.waitFor();
  assert.equal(await editor.count(), 0);

  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await flows.evaluate((element) => element.scrollWidth <= element.clientWidth));
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, 'after-flow-library-phone.png') });
  await flowRow.click();
  await editor.getByRole('textbox', { name: '流程说明' }).fill('核对数据与引用');
  let flowSave;
  await page.route(`**/api/platform/flows/${flow.id}`, async (route) => {
    assert.equal(route.request().method(), 'PUT');
    flowSave = route.request().postDataJSON();
    await route.fulfill({ json: { ...flowSave, id: flow.id } });
  });
  await editor.getByRole('button', { name: '保存', exact: true }).click();
  await editor.getByText('已保存到本机。').waitFor();
  assert.equal(flowSave.description, '核对数据与引用');
  assert.equal(flowSave.project_id, projects[0].id);
}

export async function checkProjectNavigation(browser) {
  const fixture = await startNavigationFixture(undefined, readFixture);
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1320, height: 900 } });
    page.setDefaultTimeout(8000);
    const faults = [];
    page.on('pageerror', (error) => faults.push(error.message));
    const core = page.getByRole('navigation', { name: '核心工作台切换' });
    const project = page.getByRole('combobox', { name: '选择共享项目' });
    const manager = page.getByRole('dialog', { name: '项目资料与历史' });
    const materialButton = page.locator('.project-materials-trigger');
    const closeManager = () => manager.getByRole('button', { name: '关闭项目资料' }).click();

    await page.goto(fixture.origin);
    await core.getByRole('button', { name: /项目与资料/ }).click();
    await manager.getByRole('combobox', { name: '选择已有项目' }).selectOption(projects[0].id);
    await manager.getByText(asset.name, { exact: true }).first().waitFor();
    await closeManager();

    await project.selectOption('');
    await core.getByRole('button', { name: /项目与资料/ }).click();
    await manager.getByRole('button', { name: '新建项目' }).click();
    await manager.waitFor({ state: 'hidden' });
    const create = page.getByRole('region', { name: '新建或选择项目' });
    await create.getByRole('textbox', { name: '项目名称' }).fill('可输入的新项目');
    await create.getByRole('button', { name: '关闭新建项目' }).click();

    await project.selectOption(projects[0].id);
    await materialButton.click();
    await manager.getByText(asset.name, { exact: true }).first().waitFor();
    await closeManager();
    await page.getByRole('button', { name: '打开快捷命令面板' }).click();
    await page.getByRole('combobox', { name: '搜索命令、项目或会话' }).fill('当前项目');
    await page.getByRole('option', { name: /当前项目：甲项目/ }).click();
    await materialButton.click();
    await manager.getByText(asset.name, { exact: true }).first().waitFor();

    await manager.getByRole('button', { name: '项目说明', exact: true }).click();
    await manager.getByRole('button', { name: '编辑说明与工作要求' }).click();
    await manager.getByRole('textbox', { name: '项目工作要求' }).fill('甲项目的未保存编辑');
    await closeManager();
    await project.selectOption(projects[1].id);
    await materialButton.click();
    await manager.getByRole('button', { name: '项目说明', exact: true }).click();
    await manager.getByText(projects[1].instructions, { exact: true }).waitFor();
    assert.equal(await manager.getByRole('textbox', { name: '项目工作要求' }).count(), 0);
    await closeManager();

    await project.selectOption(projects[0].id);
    const sidebarRooms = page.getByRole('region', { name: '当前项目群聊' });
    const [roomA, roomB] = rooms.map((room) => sidebarRooms.getByRole('button', { name: room.title }));
    const roomSwitcher = page.getByRole('combobox', { name: '打开项目群聊' });
    await roomA.click();
    assert.equal(await roomSwitcher.inputValue(), rooms[0].id);
    await roomSwitcher.selectOption(rooms[1].id);
    await sidebarRooms.locator('[aria-current="page"]').getByText(rooms[1].title).waitFor();
    assert.equal(await roomB.getAttribute('aria-current'), 'page');
    await roomA.click();
    assert.equal(await roomSwitcher.inputValue(), rooms[0].id);

    await core.getByRole('button', { name: '项目资源', exact: true }).click();
    await page.locator('.quick-start-advanced > summary').click();
    await page.getByRole('button', { name: /原生 SDK 工作室/ }).click();
    await page.getByRole('heading', { name: '原生 SDK 工作室' }).waitFor();
    await checkSdkAttachments(page);
    await page.locator('.context-attachments > summary').click();
    await page.getByRole('button', { name: '预览当前项目代码' }).click();
    await page.getByRole('button', { name: '准备评审' }).waitFor();
    assert.equal(await project.inputValue(), projects[0].id);

    await core.getByRole('button', { name: '项目资源', exact: true }).click();
    const resourceNav = page.getByRole('navigation', { name: '项目资源' });
    await resourceNav.getByRole('button', { name: '快速开始' }).click();
    await page.getByRole('button', { name: /添加项目用于代码审查/ }).click();
    const registration = page.getByRole('dialog', { name: '添加本地项目' });
    await registration.getByRole('textbox', { name: '本地文件夹路径' }).fill('/fixture/independent-project');
    await registration.getByRole('button', { name: '取消', exact: true }).click();

    await core.getByRole('button', { name: '项目资源', exact: true }).click();
    await resourceNav.getByRole('button', { name: '提示词库' }).click();
    const list = page.locator('.resource-list');
    const editor = page.locator('.resource-editor');
    const promptA = prompts[0].name;
    const promptB = prompts[1].name;
    await list.getByRole('button', { name: new RegExp(promptA) }).click();
    await editor.getByRole('heading', { name: promptA }).waitFor();
    await project.selectOption(projects[1].id);
    await list.getByRole('button', { name: new RegExp(promptB) }).waitFor();
    await editor.getByRole('heading', { name: '新建提示词库' }).waitFor();
    assert.equal(await editor.getByRole('heading', { name: promptA }).count(), 0);

    await list.getByRole('combobox', { name: '资源范围' }).selectOption('global');
    await list.getByRole('button', { name: /全局提示词/ }).click();
    await editor.getByRole('heading', { name: '全局提示词' }).waitFor();
    await project.selectOption(projects[0].id);
    assert.equal(await editor.getByRole('heading', { name: '全局提示词' }).count(), 1);
    await checkLibraryBrowsing(page, project, resourceNav);
    assert.deepEqual(fixture.writes, []);
    assert.deepEqual(faults, []);
  } finally {
    await page?.close();
    await fixture.close();
  }
}
