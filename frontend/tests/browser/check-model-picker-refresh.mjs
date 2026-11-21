import assert from 'node:assert/strict';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const localOrigin = 'http://127.0.0.1:8001';
const oldModel = { id: 'gpt-6.1-sol', provider: 'codex', verified: true };
const newModels = ['gpt-6.2-sol', 'gpt-6.2-astra'].map((id) => ({ id, provider: 'codex', verified: false }));
const gemini = { id: 'gemini-3.5-flash', provider: 'antigravity', verified: false };
const alternate = { id: 'claude-sonnet-4-6', provider: 'antigravity', verified: false };
const google = { id: 'gemini-3.2-pro', provider: 'google', provider_label: 'Google Gemini 网关' };
const updatedGemini = { id: 'gemini-3.6-flash', provider: 'antigravity', verified: false };
const updatedChatGpt = { id: 'gpt-6.3-sol', provider: 'codex', verified: false };
const publisherGemini = 'gemini-4';
const gatewayOnlyReceipt = 'not-in-current-status';
const connection = { id: 'fixture', enabled: true, name: '测试 API 连接', updated_at: 'revision-1' };
const apiModel = { id: 'api-only-model' };
const gatewayError = 'ChatGPT 模型目录暂时无法刷新。';
const geminiError = 'Gemini 模型目录暂时无法刷新。';
const publisherWarning = '暂时无法更新网关发布目录，已读取当前网关可选模型。';
const connectionError = 'API 模型目录暂时无法读取。';
const checkedAt = '2026-11-21T01:00:00Z';
const status = (models, gateway = {}) => ({
  gateway: { online: true, ...gateway }, models, checked_at: checkedAt,
  providers: [
    { id: 'codex', label: 'ChatGPT', authenticated: true },
    { id: 'antigravity', label: 'Gemini', authenticated: true },
    { id: 'connection:fixture', enabled: true },
  ],
  tools: {},
});
const discover = (source, gateway = {}) => ({ checked_at: checkedAt, gateway, sources: [{ connection, ...source }] });
const scopedDiscovery = (scope, gateway) => ({ scope, checked_at: checkedAt, gateway, sources: [] });
const geminiDirectory = { antigravity: { listed: [gemini.id, alternate.id, gatewayOnlyReceipt], catalog_only: [google.id, publisherGemini] } };
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
const sourcePicker = (picker) => picker.getByRole('combobox', { name: '筛选模型来源' });
const closePicker = (picker) => picker.getByRole('button', { name: '关闭模型选择' }).click();
const loginSettings = async (page) => {
  await page.getByRole('button', { name: '模型连接 / API Key', exact: true }).click();
  return page.getByRole('region', { name: '已有登录连接', exact: true });
};

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
  const scopedPosts = [];
  let statusReads = 0;
  let latestStatus = status([oldModel]);

  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== localOrigin) return route.abort();
    if (request.method() === 'POST' && url.pathname === '/api/model-connections/refresh-catalog') {
      const scope = url.searchParams.get('source');
      if (scope) {
        scopedPosts.push(scope);
        if (scope === 'antigravity') {
          const attempt = scopedPosts.filter((source) => source === scope).length;
          if (attempt === 1) {
            latestStatus = status([...latestStatus.models, gemini, alternate, google]);
            return route.fulfill({ json: scopedDiscovery(scope, { providers: geminiDirectory, publisher_warning: publisherWarning }) });
          }
          if (attempt === 2) return route.fulfill({ status: 502, json: { detail: geminiError } });
          latestStatus = status([...latestStatus.models, updatedGemini]);
          return route.fulfill({ json: scopedDiscovery(scope, { providers: geminiDirectory, publisher_warning: publisherWarning }) });
        }
        if (scope === 'codex') {
          latestStatus = status([...latestStatus.models, updatedChatGpt]);
          return route.fulfill({ json: scopedDiscovery(scope, {}) });
        }
        unexpected.push(`unexpected refresh scope ${scope}`);
        return route.abort();
      }
      postCount += 1;
      if (postCount === 1) {
        discoveryStarted.resolve();
        await releaseDiscovery.promise;
        latestStatus = status([oldModel, ...newModels]);
        return route.fulfill({ json: discover({ models: [apiModel] }, { checked_at: checkedAt }) });
      }
      if (postCount === 2) {
        latestStatus = status(latestStatus.models, { error: gatewayError, stale: true });
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

  await sourcePicker(picker).selectOption({ label: 'Google / Antigravity 网关' });
  await picker.getByText('0 个模型', { exact: true }).waitFor();
  await refresh(picker).click();
  await model(picker, gemini.id).getByText('尚未验证调用', { exact: true }).waitFor();
  await model(picker, alternate.id).waitFor();
  await picker.getByText('2 个模型', { exact: true }).waitFor();
  await picker.getByRole('status').filter({ hasText: publisherWarning }).waitFor();
  assert.equal(await picker.getByRole('alert').count(), 0, 'publisher warnings do not present the gateway as failed');
  assert.equal(await model(picker, gemini.id).getByText('使用上次目录', { exact: true }).count(), 0);
  assert.equal(await model(picker, gemini.id).isEnabled(), true);
  assert.equal(await model(picker, gatewayOnlyReceipt).count(), 0, 'refresh receipts cannot add selectable models before status does');
  assert.equal(await model(picker, google.id).count(), 0, 'a distinct Google route is outside this subscription');
  await picker.getByText('网关发布目录 · 此连接暂不可选（2） · 上次读取', { exact: true }).click();
  await picker.getByText(google.id, { exact: true }).waitFor();
  const search = picker.getByRole('textbox', { name: '搜索模型' });
  await search.fill('gemini-3.5');
  await picker.getByText('1 个模型', { exact: true }).waitFor();
  assert.equal(await model(picker, alternate.id).count(), 0);
  await sourcePicker(picker).selectOption('');
  await picker.getByRole('status').filter({ hasText: publisherWarning }).waitFor();
  const unmatched = picker.getByText('网关发布目录 · 当前暂不可选（1） · 上次读取', { exact: true });
  await search.fill('gemini');
  assert.equal(await picker.locator('.publisher-catalog').count(), 0, 'a broad query does not add publisher entries');
  await search.fill(publisherGemini);
  await picker.getByText('没有可选的匹配模型。', { exact: true }).waitFor();
  await unmatched.click();
  await picker.getByText(publisherGemini, { exact: true }).waitFor();
  assert.equal(await model(picker, publisherGemini).count(), 0);
  await search.fill(google.id);
  await model(picker, google.id).waitFor();
  assert.equal(await picker.locator('.publisher-catalog').count(), 0, 'a selectable route with the same ID wins in all-source search');
  await search.fill('');
  await sourcePicker(picker).selectOption('antigravity');

  await refresh(picker).click();
  await picker.getByRole('alert').filter({ hasText: geminiError }).waitFor();
  await model(picker, gemini.id).getByText('使用上次目录', { exact: true }).waitFor();
  await sourcePicker(picker).selectOption('codex');
  assert.equal(await picker.getByRole('alert').filter({ hasText: geminiError }).count(), 0);
  assert.equal(await model(picker, newModels[0].id).getByText('使用上次目录', { exact: true }).count(), 0);
  assert.equal(await model(picker, newModels[0].id).getAttribute('aria-pressed'), 'true');
  await sourcePicker(picker).selectOption('connection:fixture');
  await model(picker, apiModel.id).getByText('新发现 · 加入选用', { exact: true }).waitFor();

  await closePicker(picker);
  const settings = await loginSettings(page);
  const geminiCard = settings.getByRole('article', { name: 'Gemini', exact: true });
  const chatGptCard = settings.getByRole('article', { name: 'ChatGPT', exact: true });
  await geminiCard.getByText('网关可选模型（2）', { exact: true }).click();
  await geminiCard.getByText(gemini.id, { exact: true }).waitFor();
  await geminiCard.getByText(alternate.id, { exact: true }).waitFor();
  await geminiCard.getByRole('button', { name: '刷新 Gemini 模型列表' }).click();
  await geminiCard.getByText('网关可选模型（3）', { exact: true }).waitFor();
  const groups = geminiCard.getByRole('list');
  assert.equal(await groups.nth(0).getAttribute('aria-label'), 'Gemini 系列（2）');
  assert.deepEqual(await groups.nth(0).getByRole('listitem').allTextContents(), [gemini.id, updatedGemini.id]);
  assert.equal(await groups.nth(1).getAttribute('aria-label'), '同一网关的其他模型（1）');
  assert.deepEqual(await groups.nth(1).getByRole('listitem').allTextContents(), [alternate.id]);
  await geminiCard.getByRole('status').filter({ hasText: publisherWarning }).waitFor();
  await geminiCard.getByText('网关发布目录 · 此连接暂不可选（2） · 上次读取', { exact: true }).waitFor();
  assert.equal(await geminiCard.getByRole('alert').count(), 0);
  assert.equal(await geminiCard.getByText('使用上次目录', { exact: false }).count(), 0);
  assert.equal(await chatGptCard.getByRole('status').filter({ hasText: publisherWarning }).count(), 0);
  await chatGptCard.getByRole('button', { name: '刷新 ChatGPT 模型列表' }).click();
  await chatGptCard.getByText('网关可选模型（4）', { exact: true }).click();
  await chatGptCard.getByText(updatedChatGpt.id, { exact: true }).waitFor();
  assert.deepEqual(scopedPosts, ['antigravity', 'antigravity', 'antigravity', 'codex']);

  await page.getByRole('button', { name: '项目 Agent', exact: true }).click();
  await page.getByRole('button', { name: '成员与分工', exact: true }).click();
  await page.getByRole('button', { name: '成员 1 模型', exact: true }).click();
  const memberPicker = page.getByRole('dialog', { name: '成员 1 模型', exact: true });
  await sourcePicker(memberPicker).selectOption('antigravity');
  await model(memberPicker, updatedGemini.id).waitFor();
  await closePicker(memberPicker);
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
  let pairedModels = [oldModel];
  await pairedPage.route('**/api/workbench/status', (route) => {
    pairedGets += 1;
    return route.fulfill({ json: status(pairedModels) });
  });
  const pairedPicker = await openPicker(pairedPage, fixture.origin);
  await model(pairedPicker, oldModel.id).waitFor();
  const pairedReadsBeforeRefresh = pairedGets;
  pairedModels = [oldModel, gemini];
  await sourcePicker(pairedPicker).selectOption('antigravity');
  await refresh(pairedPicker).click();
  await model(pairedPicker, gemini.id).waitFor();
  assert.ok(pairedGets > pairedReadsBeforeRefresh, 'paired refresh rereads the host status');
  await closePicker(pairedPicker);
  const pairedSettings = await loginSettings(pairedPage);
  const pairedGemini = pairedSettings.getByRole('article', { name: 'Gemini', exact: true });
  await pairedGemini.getByText('网关可选模型（1）', { exact: true }).click();
  await pairedGemini.getByText(gemini.id, { exact: true }).waitFor();
  pairedModels = [oldModel, gemini, updatedGemini];
  await pairedGemini.getByRole('button', { name: '读取主机的 Gemini 模型列表' }).click();
  await pairedGemini.getByText('网关可选模型（2）', { exact: true }).waitFor();
  await pairedGemini.getByText(updatedGemini.id, { exact: true }).waitFor();
  assert.deepEqual(fixture.writes, [], 'paired refresh only reads the host catalog');
  assert.deepEqual(unexpected, [], 'desktop refresh and selection make no other writes');
  assert.deepEqual(responseErrors, []);
  console.log('Model catalog refresh passed for Gemini, ChatGPT, Ruler and member selectors, source failures, and paired access.');
} finally {
  await browser?.close();
  await fixture.close();
}
