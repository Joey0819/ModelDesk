import assert from 'node:assert/strict';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const localOrigin = 'http://127.0.0.1:8001';
const oldModel = { id: 'gpt-6.1-sol', provider: 'codex', verified: true };
const newModels = ['gpt-6.2-sol', 'gpt-6.2-astra'].map((id) => ({ id, provider: 'codex', verified: false }));
const gemini = { id: 'gemini-3.8-flash', provider: 'antigravity', verified: false };
const olderGemini = { id: 'gemini-3.5-flash', provider: 'antigravity', verified: true };
const settingsGemini = { id: 'gemini-3.1-pro', provider: 'antigravity', verified: false };
const removedImage = { id: 'gemini-3.1-flash-image', provider: 'antigravity', capability: 'image' };
const alternate = { id: 'claude-sonnet-4-6', provider: 'antigravity', verified: false };
const google = { id: 'gemini-4-argon-high', provider: 'google', provider_label: 'Google Gemini 网关' };
const updatedGemini = { id: 'gemini-3.10-flash', provider: 'antigravity', verified: false };
const updatedChatGpt = { id: 'gpt-6.3-sol', provider: 'codex', verified: false };
const nativeProvider = 'antigravity-agent';
const nativeAgent = { id: 'agent:antigravity', provider: nativeProvider, label: 'Antigravity Agent', provider_label: 'Antigravity Agent · Google CLI', source: 'native_agent', capability: 'text', group_only: true, installed: true };
const nativeExact = { ...nativeAgent, id: 'agent:antigravity:gemini-4-neon-high', label: 'Gemini 4 Neon (High)' };
const nativeExactRoleLabel = `${nativeAgent.label} · ${nativeExact.label}`;
const publisherGemini = 'gemini-4-carbon-high';
const publisherIds = [publisherGemini, 'gemini-4-hydrogen-xhigh', google.id, 'gemini-4-neon-xhigh'];
const newlyAvailablePublisher = { id: publisherGemini, provider: 'antigravity', verified: false };
const cachedGateway = { cached_publication: { antigravity: { catalog_only: publisherIds }, codex: { catalog_only: [] } } };
const gatewayOnlyReceipt = 'not-in-current-status';
const connection = { id: 'fixture', enabled: true, name: '测试 API 连接', updated_at: 'revision-1' };
const apiModel = { id: 'api-only-model' };
const gatewayError = 'ChatGPT 模型目录暂时无法刷新。';
const geminiError = 'Gemini 模型目录暂时无法刷新。';
const nativeError = 'Antigravity CLI 暂时无法读取型号。';
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
const geminiDirectory = (current) => {
  const listed = current.models.filter((model) => model.provider === 'antigravity').map((model) => model.id);
  return { antigravity: { listed: [...listed, gatewayOnlyReceipt], catalog_only: publisherIds.filter((id) => !listed.includes(id)) } };
};
const unexpected = [];
const responseErrors = [];
let browser;

async function openPicker(page, origin, label = '统筹模型（Ruler）') {
  page.setDefaultTimeout(8000);
  page.on('pageerror', (error) => responseErrors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('modeldesk.view', 'agents'));
  await page.goto(origin);
  await page.getByRole('button', { name: '成员与分工', exact: true }).click();
  await page.getByRole('button', { name: label, exact: true }).click();
  return page.getByRole('dialog', { name: label, exact: true });
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
  let latestStatus = status([oldModel], cachedGateway);

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
            latestStatus = status([...latestStatus.models, alternate, gemini, olderGemini, google, removedImage]);
            return route.fulfill({ json: scopedDiscovery(scope, { providers: geminiDirectory(latestStatus), publisher_warning: publisherWarning }) });
          }
          if (attempt === 2) return route.fulfill({ status: 502, json: { detail: geminiError } });
          if (attempt === 3) latestStatus = status([...latestStatus.models, settingsGemini]);
          if (attempt === 4) latestStatus = status([...latestStatus.models.filter((row) => row.id !== removedImage.id), updatedGemini, newlyAvailablePublisher]);
          return route.fulfill({ json: scopedDiscovery(scope, { providers: geminiDirectory(latestStatus), publisher_warning: publisherWarning }) });
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
        latestStatus = status([oldModel, ...newModels], cachedGateway);
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
  await picker.getByText('0 个可选 Gemini 文字模型', { exact: true }).waitFor();
  const publishedInRuler = picker.getByRole('region', { name: '已发布的 Gemini 模型' });
  await publishedInRuler.getByText('上次读取', { exact: true }).waitFor();
  for (const id of publisherIds) await publishedInRuler.getByText(id, { exact: true }).waitFor();
  await refresh(picker).click();
  await model(picker, gemini.id).getByText('尚未验证调用', { exact: true }).waitFor();
  await picker.getByText('同一网关的其他模型（1）', { exact: true }).waitFor();
  assert.equal(await model(picker, alternate.id).isVisible(), false);
  await picker.getByText('2 个可选 Gemini 文字模型', { exact: true }).waitFor();
  await picker.getByRole('status').filter({ hasText: publisherWarning }).waitFor();
  assert.equal(await picker.getByRole('alert').count(), 0, 'publisher warnings do not present the gateway as failed');
  assert.equal(await model(picker, gemini.id).getByText('使用上次目录', { exact: true }).count(), 0);
  assert.equal(await model(picker, gemini.id).isEnabled(), true);
  assert.equal(await model(picker, gatewayOnlyReceipt).count(), 0, 'refresh receipts cannot add selectable models before status does');
  assert.equal(await model(picker, google.id).count(), 0, 'a distinct Google route is outside this subscription');
  await publishedInRuler.getByText('上次读取', { exact: true }).waitFor();
  for (const id of publisherIds) await publishedInRuler.getByText(id, { exact: true }).waitFor();
  const search = picker.getByRole('textbox', { name: '搜索模型' });
  await search.fill('gemini-3.8');
  await picker.getByText('1 个可选 Gemini 文字模型', { exact: true }).waitFor();
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
  await geminiCard.getByText('网关可选模型（4）', { exact: true }).click();
  await geminiCard.getByText(gemini.id, { exact: true }).waitFor();
  await geminiCard.getByText(alternate.id, { exact: true }).waitFor();
  await geminiCard.getByRole('button', { name: '刷新 Gemini 模型列表' }).click();
  await geminiCard.getByText('网关可选模型（5）', { exact: true }).waitFor();
  const groups = geminiCard.getByRole('list');
  assert.equal(await groups.nth(0).getAttribute('aria-label'), 'Gemini 系列（4）');
  assert.deepEqual(await groups.nth(0).getByRole('listitem').allTextContents(), [gemini.id, olderGemini.id, removedImage.id, settingsGemini.id]);
  assert.equal(await groups.nth(1).getAttribute('aria-label'), '同一网关的其他模型（1）');
  assert.deepEqual(await groups.nth(1).getByRole('listitem').allTextContents(), [alternate.id]);
  await geminiCard.getByRole('status').filter({ hasText: publisherWarning }).waitFor();
  await geminiCard.getByText('网关发布目录 · 此连接暂不可选（4） · 上次读取', { exact: true }).waitFor();
  assert.equal(await geminiCard.getByRole('alert').count(), 0);
  assert.equal(await geminiCard.getByText('使用上次目录', { exact: false }).count(), 0);
  assert.equal(await chatGptCard.getByRole('status').filter({ hasText: publisherWarning }).count(), 0);
  await chatGptCard.getByRole('button', { name: '刷新 ChatGPT 模型列表' }).click();
  await chatGptCard.getByText('网关可选模型（4）', { exact: true }).click();
  await chatGptCard.getByText(updatedChatGpt.id, { exact: true }).waitFor();
  assert.deepEqual(scopedPosts, ['antigravity', 'antigravity', 'antigravity', 'codex']);

  await page.getByRole('button', { name: '项目 Agent', exact: true }).click();
  await page.getByRole('button', { name: '成员与分工', exact: true }).click();
  const member = page.getByRole('button', { name: '成员 1 模型', exact: true });
  await member.click();
  const memberPicker = page.getByRole('dialog', { name: '成员 1 模型', exact: true });
  await sourcePicker(memberPicker).selectOption('antigravity');
  const publishedInMember = memberPicker.getByRole('region', { name: '已发布的 Gemini 模型' });
  for (const id of publisherIds) {
    await publishedInMember.getByText(id, { exact: true }).waitFor();
    assert.equal(await model(memberPicker, id).count(), 0);
  }
  assert.equal(await publishedInMember.getByRole('button').count(), 0, 'published IDs are informational');
  assert.equal(await memberPicker.getByRole('button', { name: '设置 Gemini API 连接', exact: true }).isVisible(), true);
  await memberPicker.getByText('3 个可选 Gemini 文字模型', { exact: true }).waitFor();
  const memberResults = memberPicker.locator('[data-model-option]:visible .model-picker-identity strong');
  assert.deepEqual(await memberResults.allTextContents(), [gemini.id, olderGemini.id, settingsGemini.id], 'Gemini versions precede other families and sort before verification badges');
  await model(memberPicker, olderGemini.id).getByText('近期调用通过', { exact: true }).waitFor();
  assert.equal(await model(memberPicker, removedImage.id).count(), 0, 'a writing role excludes image routes');

  const memberSearch = memberPicker.getByRole('textbox', { name: '搜索模型' });
  await memberSearch.press('ArrowDown');
  assert.equal(await model(memberPicker, gemini.id).evaluate((element) => element === document.activeElement), true);
  await page.keyboard.press('End');
  assert.equal(await model(memberPicker, settingsGemini.id).evaluate((element) => element === document.activeElement), true, 'keyboard traversal skips collapsed other-family rows');
  await memberSearch.fill('claude');
  await model(memberPicker, alternate.id).waitFor();
  await memberSearch.press('ArrowDown');
  assert.equal(await model(memberPicker, alternate.id).evaluate((element) => element === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.match(await member.innerText(), /claude-sonnet-4-6/);
  await member.click();
  await sourcePicker(memberPicker).selectOption('antigravity');
  await model(memberPicker, alternate.id).waitFor();
  assert.equal(await model(memberPicker, alternate.id).getAttribute('aria-pressed'), 'true', 'a selected Claude model remains visible');

  const desktopViewport = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  for (const id of publisherIds) {
    const rect = await publishedInMember.getByText(id, { exact: true }).boundingBox();
    assert.ok(rect && rect.x >= 0 && rect.x + rect.width <= 390 && rect.y + rect.height <= 844, `${id} fits onscreen at 390px before expanding anything`);
  }
  await page.setViewportSize(desktopViewport);

  await refresh(memberPicker).click();
  await memberPicker.getByText('网关目录更新 · 新增 2 个 · 移除 1 个', { exact: true }).waitFor();
  await memberPicker.getByText('5 个可选 Gemini 文字模型', { exact: true }).waitFor();
  assert.deepEqual((await memberResults.allTextContents()).slice(0, 4), [publisherGemini, updatedGemini.id, gemini.id, olderGemini.id]);
  await model(memberPicker, updatedGemini.id).getByText('本次新增', { exact: true }).waitFor();
  assert.equal(await publishedInMember.getByText(publisherGemini, { exact: true }).count(), 0);
  assert.equal(await model(memberPicker, publisherGemini).isEnabled(), true, 'the ID becomes selectable only after entering status');
  assert.equal(await model(memberPicker, alternate.id).getAttribute('aria-pressed'), 'true');

  await refresh(memberPicker).click();
  await memberPicker.getByText('网关可选模型未变化', { exact: true }).waitFor();
  assert.equal(await model(memberPicker, updatedGemini.id).getByText('本次新增', { exact: true }).count(), 0);
  await model(memberPicker, publisherGemini).click();
  assert.match(await member.innerText(), new RegExp(publisherGemini));
  await member.click();
  await sourcePicker(memberPicker).selectOption('antigravity');
  await memberPicker.getByRole('button', { name: '设置 Gemini API 连接', exact: true }).click();
  const connectionEditor = page.getByRole('dialog', { name: '模型连接设置' });
  const apiPreset = connectionEditor.getByRole('combobox', { name: '模型服务类型' });
  await apiPreset.waitFor();
  assert.equal(await apiPreset.inputValue(), 'gemini');
  await connectionEditor.getByRole('button', { name: '关闭模型连接设置' }).click();
  await memberPicker.waitFor();
  assert.equal(await sourcePicker(memberPicker).inputValue(), 'antigravity', 'closing Gemini API settings restores the current picker');
  assert.equal(await model(memberPicker, publisherGemini).getAttribute('aria-pressed'), 'true');
  await closePicker(memberPicker);
  assert.deepEqual(scopedPosts, ['antigravity', 'antigravity', 'antigravity', 'codex', 'antigravity', 'antigravity']);
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

  const apiRouteId = 'connection:gemini-api-fixture';
  const savedModel = { id: 'gemini-via-api-fixture' };
  const savedConnection = { id: 'gemini-api-fixture', provider: 'gemini', name: 'Google Gemini API', enabled: true, models: [savedModel] };
  const savedRoute = { id: `${apiRouteId}:${savedModel.id}`, provider: apiRouteId, provider_label: savedConnection.name };
  const saveWrites = [];
  latestStatus = status([oldModel, gemini], cachedGateway);
  await page.route(`${localOrigin}/api/model-connections`, (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: { connections: saveWrites.length ? [savedConnection] : [] } });
    const body = route.request().postDataJSON();
    saveWrites.push(body.provider);
    assert.deepEqual(body.models.map((row) => row.id), [savedModel.id]);
    latestStatus = { ...latestStatus, models: [...latestStatus.models, savedRoute], providers: [...latestStatus.providers, { id: apiRouteId, enabled: true }] };
    return route.fulfill({ status: 201, json: savedConnection });
  });
  await page.route(`${localOrigin}/api/model-connections/refresh-catalog`, (route) => route.fulfill({ json: discover({ models: [apiModel] }) }));
  await closePicker(picker);
  await member.click();
  await sourcePicker(memberPicker).selectOption('antigravity');
  await memberSearch.fill('gemini-4');
  await memberPicker.getByRole('button', { name: '设置 Gemini API 连接', exact: true }).click();
  await connectionEditor.getByLabel('API Key', { exact: true }).fill('fixture-key');
  await connectionEditor.getByRole('button', { name: '手动填写模型' }).click();
  await connectionEditor.getByRole('textbox', { name: '模型 1 ID' }).fill(savedModel.id);
  await connectionEditor.getByRole('button', { name: '保存连接', exact: true }).click();
  await connectionEditor.getByText('连接已保存。', { exact: false }).waitFor();
  await connectionEditor.getByRole('button', { name: '关闭模型连接设置' }).click();
  await model(memberPicker, savedModel.id).waitFor();
  assert.equal(await sourcePicker(memberPicker).inputValue(), apiRouteId);
  assert.equal(await memberSearch.inputValue(), '', 'returning from a saved API model clears the old login search');
  await sourcePicker(memberPicker).selectOption('antigravity');
  await memberPicker.getByRole('button', { name: '设置 Gemini API 连接', exact: true }).click();
  await connectionEditor.getByRole('button', { name: '选用此连接', exact: true }).click();
  await model(memberPicker, savedModel.id).waitFor();
  assert.equal(await sourcePicker(memberPicker).inputValue(), apiRouteId, 'an existing Gemini API connection can also return to the picker');
  assert.deepEqual(saveWrites, ['gemini']);

  latestStatus = status([oldModel, { ...nativeAgent, installed: false }], cachedGateway);
  await Promise.all([page.waitForResponse('**/api/workbench/status'), page.clock.fastForward(15000)]);
  await sourcePicker(memberPicker).selectOption('antigravity');
  await memberPicker.getByText('0 个可选 Gemini 文字模型', { exact: true }).waitFor();
  const nativeFallback = memberPicker.getByRole('region', { name: 'Antigravity Agent', exact: true });
  const chooseDefault = nativeFallback.getByRole('button', { name: '改用 Antigravity Agent', exact: true });
  await nativeFallback.getByRole('link', { name: '官方安装与登录指南' }).waitFor();
  assert.equal(await chooseDefault.isDisabled(), true);
  latestStatus = status([oldModel, nativeAgent], cachedGateway);
  await Promise.all([page.waitForResponse('**/api/workbench/status'), page.clock.fastForward(15000)]);
  await nativeFallback.getByText('已检测到主机 CLI。', { exact: false }).waitFor();
  assert.equal(await chooseDefault.isEnabled(), true);
  assert.match(await member.innerText(), new RegExp(publisherGemini), 'installing the alternative never changes the saved role');
  await chooseDefault.focus();
  await page.keyboard.press('Enter');
  assert.equal((await member.innerText()).trim(), nativeAgent.label);

  await ruler.click();
  await sourcePicker(picker).selectOption('');
  const rulerSearch = picker.getByRole('textbox', { name: '搜索模型' });
  await rulerSearch.fill('Antigravity Agent');
  await rulerSearch.press('ArrowDown');
  const rulerAgent = model(picker, nativeAgent.label);
  assert.equal(await rulerAgent.evaluate((element) => element === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal((await ruler.innerText()).trim(), nativeAgent.label);

  const nativeRefreshes = [];
  await page.route(`${localOrigin}/api/model-connections/refresh-catalog?source=${nativeProvider}`, (route) => {
    nativeRefreshes.push(route.request().method());
    if (nativeRefreshes.length === 1) return route.fulfill({ json: { scope: nativeProvider, checked_at: checkedAt, native_agent: { error: nativeError, checked_at: checkedAt }, sources: [] } });
    latestStatus = status([oldModel, nativeAgent, nativeExact], cachedGateway);
    return route.fulfill({ json: { scope: nativeProvider, checked_at: checkedAt, native_agent: { listed: [nativeExact.id], checked_at: checkedAt }, sources: [] } });
  });
  await member.click();
  await sourcePicker(memberPicker).selectOption('antigravity');
  await nativeFallback.getByRole('button', { name: '查看 Agent 可选型号', exact: true }).click();
  await memberPicker.getByRole('alert').filter({ hasText: nativeError }).waitFor();
  await refresh(memberPicker).click();
  await memberPicker.getByText('1 个可选 CLI 型号', { exact: true }).waitFor();
  assert.equal(await sourcePicker(memberPicker).inputValue(), nativeProvider);
  assert.equal(await model(memberPicker, nativeExact.label).isEnabled(), true);
  assert.equal(await model(memberPicker, publisherGemini).count(), 0, 'the native catalog only offers IDs returned by the official Agent');
  await model(memberPicker, nativeExact.label).click();
  assert.equal((await member.innerText()).trim(), nativeExactRoleLabel);
  assert.deepEqual(nativeRefreshes, ['POST', 'POST']);

  const teamEditor = page.locator('.mission-team-editor');
  if (await teamEditor.getByRole('textbox', { name: '成员 2 名称' }).count() === 0) await teamEditor.getByRole('button', { name: '添加 Agent', exact: true }).click();
  const memberTwoKind = teamEditor.getByRole('combobox', { name: '成员 2 类型' });
  await memberTwoKind.selectOption('image');
  await teamEditor.getByRole('button', { name: '成员 2 模型', exact: true }).click();
  const imagePicker = page.getByRole('dialog', { name: '成员 2 模型', exact: true });
  assert.equal(await imagePicker.getByRole('option', { name: nativeAgent.label, exact: true }).count(), 0);
  assert.equal(await model(imagePicker, nativeExact.label).count(), 0);
  await closePicker(imagePicker);
  await memberTwoKind.selectOption('model');
  const duties = await teamEditor.getByRole('textbox', { name: /^成员 \d+ 职责$/ }).all();
  for (const duty of duties) await duty.fill('整理信息并返回有依据的文字结果。');
  let savedTeam;
  await page.route(`${localOrigin}/api/harness/teams`, (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: savedTeam ? [savedTeam] : [] });
    savedTeam = { id: 'agent-team-fixture', ...route.request().postDataJSON() };
    return route.fulfill({ status: 201, json: savedTeam });
  });
  await teamEditor.getByRole('button', { name: '保存协作组' }).click();
  const summaries = page.locator('.mission-team-members, .mission-coordinator-summary');
  await summaries.getByText(nativeExactRoleLabel, { exact: true }).waitFor();
  await summaries.getByText(nativeAgent.label, { exact: true }).waitFor();
  assert.equal(savedTeam.coordinator_model, nativeAgent.id);
  assert.equal(savedTeam.members[0].model, nativeExact.id);
  await page.close();

  const pairedPage = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  let pairedGets = 0;
  let pairedModels = [oldModel];
  await pairedPage.route('**/api/workbench/status', (route) => {
    pairedGets += 1;
    return route.fulfill({ json: status(pairedModels, cachedGateway) });
  });
  const pairedPicker = await openPicker(pairedPage, fixture.origin, '成员 1 模型');
  await model(pairedPicker, oldModel.id).waitFor();
  const pairedReadsBeforeRefresh = pairedGets;
  await sourcePicker(pairedPicker).selectOption('antigravity');
  const publishedOnPhone = pairedPicker.getByRole('region', { name: '已发布的 Gemini 模型' });
  for (const id of publisherIds) await publishedOnPhone.getByText(id, { exact: true }).waitFor();
  await publishedOnPhone.getByText('上次读取', { exact: true }).waitFor();
  pairedModels = [oldModel, gemini];
  await refresh(pairedPicker).click();
  await model(pairedPicker, gemini.id).waitFor();
  await pairedPicker.getByText('1 个可选 Gemini 文字模型', { exact: true }).waitFor();
  await pairedPicker.getByText('网关目录更新 · 新增 1 个', { exact: true }).waitFor();
  assert.equal(await pairedPicker.getByRole('button', { name: '设置 Gemini API 连接', exact: true }).count(), 0);
  assert.ok(pairedGets > pairedReadsBeforeRefresh, 'paired refresh rereads the host status');
  pairedModels = [oldModel, gemini, nativeAgent, nativeExact];
  await pairedPicker.getByRole('button', { name: '查看 Agent 可选型号', exact: true }).click();
  await model(pairedPicker, nativeExact.label).waitFor();
  assert.equal(await sourcePicker(pairedPicker).inputValue(), nativeProvider);
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
