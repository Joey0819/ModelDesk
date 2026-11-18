import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const screenshots = process.env.SCREENSHOT_DIR;
if (screenshots) await mkdir(screenshots, { recursive: true });
const shot = (page, name) => screenshots && page.screenshot({ path: join(screenshots, `${name}.png`) });
const tool = { id: 'gpt-6.1-sol', label: 'Fixture GPT', provider: 'openai' };
const job = {
  id: 'fixture-complete-turn', conversation_id: 'fixture-conversation', project_id: null,
  mode: 'chat', status: 'complete', prompt: '阅读一篇论文', models: [tool.id],
  created_at: '2026-11-01T00:00:00Z', events: [], result: null,
};
const provider = { id: 'openai', label: 'Fixture OpenAI', authenticated: true, verified_models: [tool.id] };
const focusIs = (locator) => locator.evaluate((element) => document.activeElement === element);
const errors = [];
const saves = [];
const exportPaths = [];
let refreshes = 0;
let browser;
const fixture = await startNavigationFixture(undefined, (url, response) => {
  if (url.pathname !== `/api/workbench/jobs/${job.id}/export`) return false;
  const format = url.searchParams.get('format');
  exportPaths.push(`${url.pathname}${url.search}`);
  const isJson = format === 'json';
  response.writeHead(200, { 'Content-Type': isJson ? 'application/json' : 'text/markdown' });
  response.end(isJson ? JSON.stringify(job) : `# ${job.prompt}\n`);
  return true;
});

async function scene(viewport, { saved = false, dark = false } = {}) {
  const page = await browser.newPage({ viewport, acceptDownloads: true });
  page.setDefaultTimeout(8000);
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(({ jobId, darkMode }) => {
    localStorage.setItem('modeldesk.view', 'tools');
    localStorage.setItem('modeldesk.mode', 'chat');
    if (darkMode) localStorage.setItem('modeldesk.theme', 'dark');
    if (jobId) localStorage.setItem('modeldesk.sessions.v1', JSON.stringify({ 'unassigned::chat': { job_id: jobId } }));
  }, { jobId: saved ? job.id : null, darkMode: dark });
  await page.route('**/api/workbench/status', (route) => {
    refreshes++;
    return route.fulfill({ json: { gateway: { online: true }, providers: [provider], models: [tool], tools: {} } });
  });
  await page.route('**/api/workbench/jobs', (route) => route.fulfill({ json: saved ? [job] : [] }));
  await page.route(`**/api/workbench/jobs/${job.id}`, (route) => route.fulfill({ json: job }));
  await page.route('**/api/platform/prompts', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const prompt = route.request().postDataJSON();
    saves.push(prompt);
    return route.fulfill({ json: { ...prompt, id: 'fixture-prompt' } });
  });
  await page.goto(fixture.origin);
  await page.getByRole('heading', { name: saved ? job.prompt : '协作工作台' }).waitFor();
  return page;
}

try {
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const desktop = await scene({ width: 1320, height: 900 }, { saved: true });
  await desktop.locator('textarea[aria-label="输入问题"]').fill('为下一轮准备的提示');
  const desktopTrigger = desktop.getByRole('button', { name: '更多操作', exact: true });
  await desktopTrigger.focus();
  await desktop.keyboard.press('Space');
  const desktopDialog = desktop.getByRole('dialog', { name: '更多操作' });
  const close = desktopDialog.getByRole('button', { name: '关闭更多操作' });
  assert.equal(await focusIs(close), true);
  await desktop.keyboard.press('Tab');
  const account = desktopDialog.getByRole('button', { name: '已有账户' });
  assert.equal(await focusIs(account), true);
  await desktop.keyboard.press('Enter');
  await desktopDialog.getByText('已发现登录凭据 · 1 个模型已验证', { exact: true }).waitFor();
  const expectedRefresh = desktop.waitForRequest('**/api/workbench/status');
  await desktopDialog.getByRole('button', { name: '刷新连接状态' }).click();
  await expectedRefresh;
  await desktopDialog.getByRole('button', { name: '刷新连接状态' }).waitFor();
  assert.ok(refreshes >= 2);
  assert.equal(await desktopDialog.getByRole('button', { name: '保存提示' }).isEnabled(), true);
  await shot(desktop, 'conversation-actions-desktop');

  for (const [format, label] of [['md', '导出 Markdown'], ['json', '导出完整 JSON']]) {
    const link = desktopDialog.getByRole('link', { name: label, exact: true });
    assert.equal(await link.getAttribute('href'), `${fixture.origin}/api/workbench/jobs/${job.id}/export?format=${format}`);
    await link.focus();
    const [download] = await Promise.all([desktop.waitForEvent('download'), desktop.keyboard.press('Enter')]);
    assert.equal(download.suggestedFilename(), `ModelDesk-chat-${job.id.slice(0, 8)}.${format}`);
    const content = await readFile(await download.path(), 'utf8');
    assert.equal(format === 'json' ? JSON.parse(content).id : content, format === 'json' ? job.id : `# ${job.prompt}\n`);
  }
  assert.deepEqual(exportPaths, ['md', 'json'].map((format) => `/api/workbench/jobs/${job.id}/export?format=${format}`));
  await desktop.keyboard.press('Escape');
  await desktopDialog.waitFor({ state: 'hidden' });
  assert.equal(await focusIs(desktopTrigger), true);

  const mobile = await scene({ width: 320, height: 640 }, { dark: true });
  const mobileTrigger = mobile.getByRole('button', { name: '更多操作', exact: true });
  const header = mobile.locator('.conversation-header');
  assert.equal(await header.evaluate((element) => element.scrollWidth === element.clientWidth), true);
  assert.ok((await header.getByRole('heading').boundingBox()).width >= 72);
  for (const label of ['设置', '最终产物', '更多操作']) {
    const bounds = await header.getByRole('button', { name: label, exact: true }).boundingBox();
    assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 320);
  }
  await shot(mobile, 'conversation-actions-mobile-header');
  await mobileTrigger.click();
  const mobileDialog = mobile.getByRole('dialog', { name: '更多操作' });
  assert.equal(await mobileDialog.getByRole('button', { name: '保存提示' }).isDisabled(), true);
  await mobileDialog.getByText('输入提示后即可保存到提示词库。').waitFor();
  await mobileDialog.getByText('当前会话有运行记录后即可导出 Markdown 或 JSON。').waitFor();
  await mobileDialog.getByRole('button', { name: '已有账户' }).click();
  await mobileDialog.getByText('Fixture OpenAI', { exact: true }).waitFor();
  assert.equal(await mobileDialog.evaluate((element) => element.scrollWidth === element.clientWidth), true);
  await shot(mobile, 'conversation-actions-mobile-dark');
  await mobileDialog.getByRole('button', { name: '关闭更多操作' }).click();
  const text = '把这段提示保存下来';
  await mobile.locator('textarea[aria-label="输入问题"]').fill(text);
  await mobileTrigger.click();
  await mobileDialog.getByRole('button', { name: '保存提示' }).click();
  await mobile.getByRole('heading', { name: '提示词库', exact: true }).waitFor();
  assert.deepEqual(saves.map((prompt) => prompt.content), [text]);

  const savedMobile = await scene({ width: 320, height: 640 }, { saved: true, dark: true });
  await savedMobile.getByRole('button', { name: '更多操作', exact: true }).click();
  const savedMobileDialog = savedMobile.getByRole('dialog', { name: '更多操作' });
  await savedMobileDialog.getByRole('button', { name: '已有账户' }).click();
  for (const label of ['导出 Markdown', '导出完整 JSON']) {
    await savedMobileDialog.getByRole('link', { name: label, exact: true }).waitFor();
  }
  assert.equal(await savedMobileDialog.evaluate((element) => element.scrollWidth === element.clientWidth), true);
  await shot(savedMobile, 'conversation-actions-mobile-export');
  assert.deepEqual(fixture.writes, []);
  assert.deepEqual(errors, []);
  console.log('Conversation actions passed keyboard, account refresh, both downloads and prompt save at 1320px and 320px.');
} finally {
  await browser?.close();
  await fixture.close();
}
