import assert from 'node:assert/strict';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const localOrigin = 'http://127.0.0.1:8001';
const oldModel = { id: 'gpt-6.1-sol', provider: 'codex', verified: true };
const newModels = ['gpt-6.2-sol', 'gpt-6.2-astra'].map((id) => ({ id, provider: 'codex', verified: false }));
const connection = { id: 'fixture', enabled: true, name: '测试 API 连接', updated_at: 'revision-1' };
const apiModel = { id: 'api-only-model' };
const gatewayError = 'ChatGPT 模型目录暂时无法刷新。';
const connectionError = 'API 模型目录暂时无法读取。';
const checkedAt = '2026-11-21T01:00:00Z';
const status = (models, gateway = {}) => ({
  gateway: { online: true, ...gateway }, models, checked_at: checkedAt,
  providers: [{ id: 'connection:fixture', enabled: true }], tools: {},
});
const discover = (source, gateway = {}) => ({ checked_at: checkedAt, gateway, sources: [{ connection, ...source }] });
const unexpected = [];
const responseErrors = [];
let browser;

async function openPicker(page, origin) {
  page.setDefaultTimeout(8000);
  page.on('pageerror', (error) => responseErrors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('modeldesk.view', 'agents'));
  await page.goto(origin);
  await page.getByRole('button', { name: '成员与分工', exact: true }).click();
  await page.getByRole('button', { name: '统筹模型（Ruler）', exact: true }).click();
  return page.getByRole('dialog', { name: '统筹模型（Ruler）', exact: true });
}

const refresh = (picker) => picker.getByRole('button', { name: '刷新列表', exact: true });
const model = (picker, id) => picker.locator('[data-model-option]').filter({ hasText: id });

try {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, channel: process.env.CHROMIUM_EXECUTABLE ? undefined : 'chrome', headless: true });
  const page = await browser.newPage();
  await page.clock.install();
  const slowPollStarted = Promise.withResolvers();
  const releaseSlowPoll = Promise.withResolvers();
  const discoveryStarted = Promise.withResolvers();
  const releaseDiscovery = Promise.withResolvers();
  let holdNextPoll = false;
  let postCount = 0;
  let statusReads = 0;
  let latestStatus = status([oldModel]);

  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== localOrigin) return route.abort();
    if (request.method() === 'POST' && url.pathname === '/api/model-connections/refresh-catalog') {
      postCount += 1;
      if (postCount === 1) {
        discoveryStarted.resolve();
        await releaseDiscovery.promise;
        latestStatus = status([oldModel, ...newModels]);
        return route.fulfill({ json: discover({ models: [apiModel] }, { checked_at: checkedAt }) });
      }
      if (postCount === 2) {
        latestStatus = status([oldModel, ...newModels], { error: gatewayError, stale: true });
        return route.fulfill({ json: discover({ models: [], error: connectionError }, { error: gatewayError, stale: true }) });
      }
      if (postCount === 3) {
        latestStatus = null;
        return route.fulfill({ json: discover({ models: [apiModel] }) });
      }
    }
    if (request.method() !== 'GET') {
      unexpected.push(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    if (url.pathname === '/api/readiness') return route.fulfill({ json: { checked_at: checkedAt, agents: [] } });
    if (url.pathname === '/api/workbench/status') {
      statusReads += 1;
      if (holdNextPoll) {
        holdNextPoll = false;
        slowPollStarted.resolve();
        await releaseSlowPoll.promise;
        return route.fulfill({ json: status([oldModel]) });
      }
      return latestStatus ? route.fulfill({ json: latestStatus }) : route.fulfill({ json: { unexpected: true } });
    }
    // All reads go to the fixture. The browser never talks to the installed app.
    return route.fulfill({ response: await route.fetch({ url: `${fixture.origin}${url.pathname}${url.search}` }) });
  });

  const picker = await openPicker(page, localOrigin);
  await model(picker, oldModel.id).waitFor();
  assert.equal(await model(picker, newModels[0].id).count(), 0);
  holdNextPoll = true;
  await Promise.all([page.waitForRequest('**/api/workbench/status'), page.clock.fastForward(15000)]);
  await slowPollStarted.promise;
  const readsBeforeRefresh = statusReads;
  await Promise.all([page.waitForRequest('**/api/model-connections/refresh-catalog'), refresh(picker).click()]);
  await discoveryStarted.promise;
  await picker.getByRole('button', { name: '正在刷新', exact: true }).waitFor();
  assert.equal(statusReads, readsBeforeRefresh, 'the picker reads the model list after the refresh finishes');
  releaseDiscovery.resolve();
  for (const { id } of newModels) await model(picker, id).getByText('尚未验证调用', { exact: true }).waitFor();
  await model(picker, apiModel.id).getByText('新发现 · 加入选用', { exact: true }).waitFor();
  const oldResponse = page.waitForResponse('**/api/workbench/status');
  releaseSlowPoll.resolve();
  await (await oldResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await model(picker, newModels[0].id).click();
  const ruler = page.getByRole('button', { name: '统筹模型（Ruler）', exact: true });
  assert.match(await ruler.innerText(), /gpt-6\.2-sol/);
  await ruler.click();

  await refresh(picker).click();
  await picker.getByRole('alert').filter({ hasText: gatewayError }).waitFor();
  assert.equal((await picker.getByRole('alert').textContent()).split(gatewayError).length, 2);
  await model(picker, newModels[1].id).getByText('使用上次目录', { exact: true }).waitFor();
  await model(picker, apiModel.id).getByText('上次发现 · 加入选用', { exact: true }).waitFor();
  await picker.getByText('部分来源需要检查').click();
  await picker.getByText(connectionError, { exact: false }).waitFor();

  await refresh(picker).click();
  await picker.getByRole('alert').filter({ hasText: '工作台返回的模型状态无法读取。' }).waitFor();
  await model(picker, newModels[1].id).getByText('暂不可用 · 保留选择', { exact: true }).waitFor();
  await page.close();

  const pairedPage = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  let pairedGets = 0;
  await pairedPage.route('**/api/workbench/status', (route) => route.fulfill({ json: status(++pairedGets > 1 ? [oldModel, ...newModels] : [oldModel]) }));
  const pairedPicker = await openPicker(pairedPage, fixture.origin);
  await model(pairedPicker, oldModel.id).waitFor();
  await refresh(pairedPicker).click();
  await model(pairedPicker, newModels[0].id).waitFor();
  assert.deepEqual(fixture.writes, [], 'paired refresh only reads the host catalog');
  assert.deepEqual(unexpected, [], 'desktop refresh and selection make no other writes');
  assert.deepEqual(responseErrors, []);
  console.log('Model picker refresh passed for Ruler, stale results, source failures, and paired access.');
} finally {
  await browser?.close();
  await fixture.close();
}
