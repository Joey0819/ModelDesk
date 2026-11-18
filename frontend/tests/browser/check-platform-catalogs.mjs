import assert from 'node:assert/strict';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const gate = () => Promise.withResolvers();
const serverA = { id: 'a', name: '服务器 A', url: 'http://127.0.0.1:8101/mcp', transport: 'streamable_http', enabled: true, allowed_tools: ['shared_tool', 'only_a'] };
const serverB = { ...serverA, id: 'b', name: '服务器 B', url: 'http://127.0.0.1:8102/mcp', allowed_tools: ['shared_tool'] };
const tool = (name) => ({ name, allowed: true, inputSchema: { properties: { note: { type: 'string' } } } });
const failure = (route, detail) => route.fulfill({ status: 503, json: { detail } });
const click = (page, name) => page.getByRole('button', { name, exact: typeof name === 'string' }).click();
let browser;

async function openPlatform(section) {
  const page = await browser.newPage();
  page.setDefaultTimeout(8000);
  await page.addInitScript((activeSection) => {
    localStorage.setItem('modeldesk.view', 'platform');
    localStorage.setItem('modeldesk.platformSection', activeSection);
  }, section);
  return page;
}

try {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, channel: process.env.CHROMIUM_EXECUTABLE ? undefined : 'chrome', headless: true });
  const errors = [];
  const page = await openPlatform('tools');
  page.on('pageerror', (error) => errors.push(error.message));
  const toolsRead = gate();
  const archivedRead = gate();
  const slowDiscovery = gate();
  const slowCall = gate();
  const counts = { tools: 0, active: 0, archived: 0, discoverA: 0 };
  const calls = [];
  let toolsAvailable = false;
  await page.route('**/api/platform/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/platform/tools') {
      counts.tools += 1;
      if (!toolsAvailable) { await toolsRead.promise; return failure(route, '内置工具服务暂时不可用'); }
      return route.fulfill({ json: [{ id: 'fixture', name: 'fixture', label: '验收工具', description: '本地测试工具' }] });
    }
    if (url.pathname === '/api/platform/mcp') {
      if (url.searchParams.has('scope')) return route.fallback();
      if (url.searchParams.get('archived') === 'true') {
        counts.archived += 1;
        if (counts.archived === 1) return failure(route, '归档列表暂时不可用');
        await archivedRead.promise;
        return route.fulfill({ json: [] });
      }
      counts.active += 1;
      return route.fulfill({ json: [serverA, serverB] });
    }
    const operation = url.pathname.match(/^\/api\/platform\/mcp\/([ab])\/(discover|call)$/);
    if (!operation) return route.fallback();
    const [, id, action] = operation;
    if (action === 'discover') {
      if (id === 'a' && ++counts.discoverA === 1) await slowDiscovery.promise;
      return route.fulfill({ json: { id, tools: id === 'a' ? [tool('shared_tool'), tool('only_a')] : [tool('shared_tool')] } });
    }
    calls.push({ id, ...route.request().postDataJSON() });
    if (id === 'b') await slowCall.promise;
    return route.fulfill({ json: { output: `来自服务器 ${id.toUpperCase()} 的结果` } });
  });
  await page.goto(fixture.origin);
  await page.getByText('正在读取内置工具…', { exact: true }).waitFor();
  toolsRead.resolve();
  const toolsError = page.getByRole('alert').filter({ hasText: '无法读取内置工具' });
  await toolsError.waitFor();
  assert.equal(await page.getByText('没有可用的内置工具。', { exact: true }).count(), 0);
  const firstReads = counts.tools;
  toolsAvailable = true;
  await click(page, '重试读取内置工具');
  await page.getByRole('button', { name: /^验收工具/ }).waitFor();
  assert.equal(counts.tools, firstReads + 1);

  const categories = page.getByRole('group', { name: '工具分类' });
  await click(categories, 'MCP 服务');
  await page.getByRole('button', { name: /^服务器 A/ }).waitFor();
  const draft = page.getByRole('textbox', { name: '服务名称' });
  await draft.fill('保留草稿');
  await page.getByRole('textbox', { name: '服务 URL' }).fill('http://127.0.0.1:8103/mcp');
  const archive = page.getByRole('checkbox', { name: '查看归档服务' });
  await archive.check();
  const archivedError = page.getByRole('alert').filter({ hasText: '无法读取归档 MCP 服务' });
  await archivedError.waitFor();
  assert.equal(await page.getByText('没有归档的 MCP 服务。', { exact: true }).count(), 0);
  await page.getByRole('button', { name: '重试读取归档 MCP 服务' }).evaluate((button) => { button.click(); button.click(); });
  await page.getByText('正在读取归档 MCP 服务…', { exact: true }).waitFor();
  archivedRead.resolve();
  await page.getByText('没有归档的 MCP 服务。', { exact: true }).waitFor();
  await archive.uncheck();
  await page.getByRole('button', { name: /^服务器 B/ }).waitFor();
  assert.equal(counts.active, 1);
  assert.equal(counts.archived, 2);
  assert.equal(await draft.inputValue(), '保留草稿');
  assert.equal(await page.getByRole('textbox', { name: '服务 URL' }).inputValue(), 'http://127.0.0.1:8103/mcp');

  await click(page, /^服务器 A/);
  const discoveringA = page.waitForRequest((request) => request.url().endsWith('/a/discover'));
  await click(page, '连接并发现工具');
  await discoveringA;
  await click(page, /^服务器 B/);
  await click(page, '连接并发现工具');
  await click(page, 'shared_tool');
  const oldDiscoveryResponse = page.waitForResponse((response) => response.url().endsWith('/a/discover'));
  slowDiscovery.resolve();
  await (await oldDiscoveryResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole('button', { name: 'only_a' }).count(), 0);
  const argumentsInput = page.getByRole('textbox', { name: '调用参数（JSON）' });
  await argumentsInput.fill('{"note":"B"}');
  const callingB = page.waitForRequest((request) => request.url().endsWith('/b/call'));
  await click(page, '调用工具');
  await callingB;
  await click(page, /^服务器 A/);
  assert.equal(await page.getByRole('button', { name: '调用工具', exact: true }).count(), 0);
  await click(page, '连接并发现工具');
  await click(page, 'shared_tool');
  await argumentsInput.fill('{"note":"A"}');
  await click(page, '调用工具');
  await page.getByText(/来自服务器 A 的结果/).waitFor();
  const oldCallResponse = page.waitForResponse((response) => response.url().endsWith('/b/call'));
  slowCall.resolve();
  await (await oldCallResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByText(/来自服务器 B 的结果/).count(), 0);
  assert.deepEqual(calls, [
    { id: 'b', tool: 'shared_tool', arguments: { note: 'B' } },
    { id: 'a', tool: 'shared_tool', arguments: { note: 'A' } },
  ]);

  const originals = await openPlatform('originals');
  originals.on('pageerror', (error) => errors.push(error.message));
  const originalsRead = gate();
  let originalsCount = 0;
  await originals.route('**/api/platform/originals', async (route) => {
    originalsCount += 1;
    if (originalsCount === 1) { await originalsRead.promise; return failure(route, '原版应用清单暂时不可用'); }
    return route.fulfill({ json: { apps: [{ id: 'chathub', label: 'ChatHub', installed: true }] } });
  });
  await originals.goto(fixture.origin);
  await originals.getByText('正在读取原版应用…', { exact: true }).waitFor();
  originalsRead.resolve();
  await originals.getByRole('alert').filter({ hasText: '无法读取原版应用' }).waitFor();
  await click(originals, '重试读取原版应用');
  await originals.getByRole('heading', { name: 'ChatHub' }).waitFor();
  assert.equal(originalsCount, 2);
  assert.deepEqual(errors, []);
  assert.deepEqual(fixture.writes, []);
  console.log('PASS tools and original apps load/retry, partial MCP catalog reads, unsaved drafts, and delayed cross-server responses');
} finally {
  await browser?.close();
  await fixture.close();
}
