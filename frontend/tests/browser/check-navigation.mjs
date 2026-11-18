import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const screenshots = process.env.SCREENSHOT_DIR;
if (screenshots) await mkdir(screenshots, { recursive: true });
const fixture = await startNavigationFixture();
let browser;
const faults = [];
const destinations = {
  missions: ['协作任务', '.automatic-workspace'], groups: ['项目群聊', '.group-workspace'],
  agents: ['Agent 与连接', '.agent-settings-workspace'], todo: ['任务看板', '.todo-board'],
  projects: ['项目与资料', '.project-workspace'], tools: ['单项协作', '.workspace:not(.platform-workspace)'],
  platform: ['项目资源', '.platform-workspace'],
};
const initial = { workspace: 'missions', mode: '聊天', modeTitle: '多模型聊天', section: '快速开始' };
const core = (page) => page.getByRole('navigation', { name: '核心工作台切换' });
const modes = (page) => page.getByRole('navigation', { name: '工作模式' });
const resources = (page) => page.getByRole('navigation', { name: '项目资源' });
const current = '[aria-current="page"]';
const click = (scope, name) => scope.getByRole('button', { name, exact: typeof name === "string" }).click();
const shot = (page, name) => screenshots && page.screenshot({ path: join(screenshots, `${name}.png`) });

function trackFaults(page) {
  page.on('pageerror', (error) => { faults.push(error.message); console.error(error); });
  page.on('request', (request) => { if (!request.url().startsWith(fixture.origin)) faults.push(`External request: ${request.url()}`); });
}

async function check(page, state) {
  const [label, pane] = destinations[state.workspace];
  await page.locator(`.workspace-shell > ${pane}`).waitFor();
  assert.equal(await core(page).locator(current).count(), 1);
  assert.match(await core(page).locator(current).innerText(), new RegExp(`^${label}`));
  assert.equal(await modes(page).count(), Number(state.workspace === 'tools'));
  assert.equal(await resources(page).count(), Number(state.workspace === 'platform'));
  if (state.workspace === 'tools') {
    assert.equal(await modes(page).locator(current).count(), 1);
    assert.equal(await modes(page).locator(current).innerText(), state.mode);
    assert.match(await page.locator('.workspace-shell .header-title').innerText(), new RegExp(state.modeTitle));
  }
  if (state.workspace === 'platform') {
    assert.equal(await resources(page).locator(current).count(), 1);
    assert.equal(await resources(page).locator(current).innerText(), state.section);
    assert.equal(await page.locator('.workspace-shell h1').innerText(), state.section);
  }
}

async function visit(page, state, next) {
  if (next === 'projects') {
    await page.getByRole('combobox', { name: '选择共享项目' }).selectOption('fixture-project');
    await click(core(page), /项目与资料/);
    await click(page.getByRole('dialog', { name: '项目资料与历史' }), '代码审查');
    await page.locator('.project-heading').waitFor();
  } else {
    await click(core(page), new RegExp(`^${destinations[next][0]}`));
  }
  state.workspace = next;
}

async function chooseMode(page, state, label, heading) {
  await visit(page, state, 'tools');
  const quick = ['聊天', '评审', '团队'];
  if (!quick.includes(label)) {
    await modes(page).locator('[aria-expanded=false]').click();
  }
  await click(modes(page), label);
  state.mode = label;
  state.modeTitle = heading;
}

async function chooseResource(page, state, section) {
  await visit(page, state, 'platform');
  if (!['快速开始', '提示词库', '智能体与流程'].includes(section)) {
    await resources(page).locator('[aria-expanded=false]').click();
  }
  await click(resources(page), section);
  state.section = section;
}

try {
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const desktop = await browser.newPage({ viewport: { width: 1320, height: 900 } });
  desktop.setDefaultTimeout(8000);
  trackFaults(desktop);
  await desktop.goto(fixture.origin);
  const state = { ...initial };
  await check(desktop, state);
  await chooseResource(desktop, state, '提示词库');
  await shot(desktop, 'after-resource');
  await click(core(desktop), '新建会话');
  state.workspace = 'tools';
  await check(desktop, state);
  assert.equal(await desktop.locator('textarea[aria-label="输入问题"]').evaluate((element) => document.activeElement === element), true);
  await shot(desktop, 'after-desktop');

  // An empty room or session must stay visible when a result shortcut is pressed.
  await desktop.keyboard.press('Control+.');
  assert.ok(await desktop.locator('.work-surface').isVisible());
  await desktop.getByRole('button', { name: '打开快捷命令面板' }).click();
  const commands = desktop.getByRole('dialog', { name: '全局快捷命令面板' });
  const search = commands.getByRole('combobox');
  await search.fill('qzx-no-match');
  assert.match(await commands.getByRole('status').innerText(), /没有找到/);
  await search.fill('任务看板');
  const activeOption = await search.getAttribute('aria-activedescendant');
  assert.equal(await commands.getByRole('option', { selected: true }).getAttribute('id'), activeOption);
  await desktop.keyboard.press('Tab');
  assert.ok(await commands.getByRole('button', { name: '关闭快捷命令' }).evaluate((el) => el === document.activeElement));
  await desktop.keyboard.press('Escape');
  assert.ok(await desktop.getByRole('button', { name: '打开快捷命令面板' }).evaluate((el) => el === document.activeElement));
  await desktop.getByRole('combobox', { name: '选择共享项目' }).selectOption('fixture-project');
  const materials = desktop.locator('.project-materials-trigger');
  await materials.click();
  const projectDialog = desktop.getByRole('dialog', { name: '项目资料与历史' });
  assert.ok(await projectDialog.evaluate((el) => el.matches(':modal')));
  await click(projectDialog, '文件夹');
  await desktop.keyboard.press('Escape');
  assert.ok(await materials.evaluate((el) => el === document.activeElement));


  await desktop.locator('textarea[aria-label="输入问题"]').fill('旧草稿');
  await desktop.keyboard.press('Control+k');
  await desktop.getByRole('combobox', { name: '搜索命令、项目或会话' }).fill('新建单项协作会话');
  await desktop.keyboard.press('Enter');
  assert.equal(await desktop.locator('textarea[aria-label="输入问题"]').inputValue(), '');
  assert.equal(await desktop.locator('textarea[aria-label="输入问题"]').evaluate((element) => document.activeElement === element), true);

  const modeDisclosure = modes(desktop).getByRole('button', { name: '其他协作方式' });
  await modeDisclosure.focus();
  await desktop.keyboard.press('Space');
  await desktop.keyboard.press('Tab');
  await desktop.keyboard.press('Escape');
  assert.equal(await modeDisclosure.getAttribute('aria-expanded'), 'false');
  assert.equal(await modeDisclosure.evaluate((element) => document.activeElement === element), true);
  await chooseMode(desktop, state, '条件流程', '条件流程');
  await visit(desktop, state, 'projects');
  await check(desktop, state);
  await shot(desktop, 'after-projects');
  await visit(desktop, state, 'tools');
  await check(desktop, state);
  await visit(desktop, state, 'platform');
  await check(desktop, state);

  const actions = [
    ...Object.keys(destinations).map((next) => () => visit(desktop, state, next)),
    () => chooseMode(desktop, state, '聊天', '多模型聊天'),
    () => chooseMode(desktop, state, '评审', '匿名评审'),
    () => chooseMode(desktop, state, '团队', '任务协作'),
    () => chooseMode(desktop, state, '角色对话', '角色对话'),
    () => chooseMode(desktop, state, 'Workforce 团队', 'Workforce 团队'),
    () => chooseResource(desktop, state, '快速开始'),
    () => chooseResource(desktop, state, '提示词库'),
    () => chooseResource(desktop, state, '知识库'),
    () => chooseResource(desktop, state, '记忆'),
    async () => { await click(core(desktop), '新建会话'); state.workspace = 'tools'; },
    () => desktop.reload(),
  ];
  const coverage = new Set();
  for (let seed of [0xC0FFEE, 0xB0ABAD11]) {
    for (let index = 0; index < 48; index++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const action = (seed >>> 16) % actions.length;
      coverage.add(action);
      await actions[action]();
      await check(desktop, state);
    }
  }
  assert.equal(coverage.size, actions.length);

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  mobile.setDefaultTimeout(8000);
  trackFaults(mobile);
  await mobile.addInitScript(() => localStorage.setItem('modeldesk.sidebarCollapsed', 'on'));
  await mobile.goto(fixture.origin);
  const opener = () => mobile.getByRole('button', { name: '打开导航' });
  await opener().waitFor();
  for (let index = 0; index < 10; index++) {
    await mobile.keyboard.press('Tab');
    assert.equal(await mobile.evaluate(() => Boolean(document.activeElement?.closest('.sidebar'))), false);
  }
  await opener().focus();
  await mobile.keyboard.press('Enter');
  const drawer = mobile.getByRole('dialog', { name: '工作台导航' });
  await drawer.waitFor();
  const close = drawer.getByRole('button', { name: '关闭导航' });
  assert.equal(await close.evaluate((element) => document.activeElement === element), true);
  await mobile.keyboard.press('Shift+Tab');
  assert.equal(await mobile.evaluate(() => Boolean(document.activeElement?.closest('.sidebar'))), true);
  await mobile.keyboard.press('Tab');
  assert.equal(await close.evaluate((element) => document.activeElement === element), true);
  await mobile.keyboard.press('Escape');
  assert.equal(await opener().evaluate((element) => document.activeElement === element), true);
  await opener().click();
  await click(core(mobile), /任务看板/);
  await mobile.locator('.todo-toolbar').waitFor();
  assert.equal(await drawer.count(), 0);
  assert.equal(await opener().evaluate((element) => document.activeElement === element), true);
  await shot(mobile, 'after-mobile');
  await opener().click();
  assert.equal(await core(mobile).locator(current).count(), 1);
  assert.match(await core(mobile).locator(current).innerText(), /^任务看板/);
  await shot(mobile, 'after-mobile-drawer');
  await click(core(mobile), '新建会话');
  assert.equal(await drawer.count(), 0);
  assert.equal(await mobile.locator('textarea[aria-label="输入问题"]').evaluate((element) => document.activeElement === element), true);

  assert.deepEqual(fixture.writes, [], 'navigation must not send API writes');
  assert.deepEqual(faults, [], 'navigation must not throw client errors or contact another origin');
  console.log(`Checked 96 seeded desktop transitions across ${coverage.size} actions plus mobile focus and navigation.`);
} finally {
  await browser?.close();
  await fixture.close();
}
