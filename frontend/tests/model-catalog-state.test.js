import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogOptions, failCatalogRefresh, filterModels, gatewayDiscovery, initialModels, mergeDiscoveries, publisherOnlyModels, retainCatalogOnFailure, selectionOptions } from '../src/model-catalog-state.js';

const source = (changes = {}) => ({ connection: { id: 'a', name: '团队服务', updated_at: 'revision-1', enabled: true }, models: [{ id: 'new-model-2027' }], ...changes });
const status = { gateway: { online: true }, providers: [{ id: 'connection:a', enabled: true }], models: [{ id: 'saved', provider: 'codex', verified: true }, { id: 'connection:a:same', label: 'Custom name', provider: 'connection:a', testing: true }] };

test('dynamic catalog exposes discoveries separately from enabled models and preserves aliases', () => {
  const list = catalogOptions(status, { sources: [source({ models: [{ id: 'same' }, { id: 'new-model-2027' }] })] });
  assert.equal(list.length, 3);
  assert.equal(list.find((item) => item.id === 'connection:a:same').label, 'Custom name');
  const discovered = list.find((item) => item.model_id === 'new-model-2027');
  assert.equal(discovered.id, 'connection:a:new-model-2027');
  assert.equal(discovered.registered, false);
  assert.equal(discovered.verified, false);
  assert.equal(discovered.expected_updated_at, 'revision-1');
});

test('discovery cannot restore removed or disabled connections', () => {
  for (const providers of [[], [{ id: 'connection:a', enabled: false }]]) {
    assert.deepEqual(catalogOptions({ models: [], providers }, { sources: [source()] }), []);
  }
});

test('failed refresh retains only last results for the same connection revision', () => {
  const prior = { sources: [source()] };
  const next = mergeDiscoveries(prior, { sources: [source({ models: [], error: 'offline' })] });
  assert.equal(next.sources[0].models.length, 1);
  assert.equal(catalogOptions(status, next).at(-1).stale, true);
  const changed = mergeDiscoveries(prior, { sources: [source({ connection: { ...source().connection, updated_at: 'revision-2' }, models: [], error: 'offline' })] });
  assert.equal(changed.sources[0].models.length, 0);
});

test('a scoped refresh updates only its provider and preserves other refresh timestamps and API discoveries', () => {
  const global = mergeDiscoveries(null, { checked_at: 'global-read', sources: [source()], gateway: { stale: true, error: 'gateway timeout' } });
  const google = mergeDiscoveries(global, { scope: 'antigravity', checked_at: 'google-read', sources: [], gateway: { updated: true } });
  const current = { ...status, models: [...status.models, { id: 'gemini-current', provider: 'antigravity' }] };
  const [codex, savedApi, gemini, discoveredApi] = catalogOptions(current, google);
  assert.equal(codex.stale, true);
  assert.equal(gemini.stale, undefined);
  assert.equal(savedApi.stale, undefined);
  assert.equal(discoveredApi.stale, false);
  assert.equal(gatewayDiscovery(google, 'codex').checked_at, 'global-read');
  assert.equal(gatewayDiscovery(google, 'antigravity').checked_at, 'google-read');

  const failed = failCatalogRefresh(google, 'codex', 'chatgpt timeout');
  assert.equal(gatewayDiscovery(failed, 'codex').error, 'chatgpt timeout');
  assert.equal(gatewayDiscovery(failed, 'antigravity').checked_at, 'google-read');
  assert.equal(failed.sources, google.sources);

  const complete = mergeDiscoveries(failed, { checked_at: 'latest-global-read', sources: [], gateway: { updated: true } });
  assert.equal(gatewayDiscovery(complete, 'codex').error, undefined);
  assert.equal(gatewayDiscovery(complete, 'antigravity').checked_at, 'latest-global-read');
  assert.equal(complete.sources.length, 0);
});

test('only status models become selectable and publisher-only metadata remains specific to its login channel', () => {
  const discovery = mergeDiscoveries(null, {
    scope: 'antigravity', sources: [], gateway: { providers: {
      antigravity: { listed: ['unconfirmed-gateway-id'], catalog_only: ['gemini-current', 'gemini-publisher', 'gemini-publisher'] },
    } },
  });
  const current = { models: [{ id: 'gemini-current', provider: 'antigravity' }, { id: 'gemini-publisher', provider: 'google' }] };
  assert.deepEqual(publisherOnlyModels(current, discovery, 'antigravity'), ['gemini-publisher']);
  assert.deepEqual(publisherOnlyModels(current, discovery, 'codex'), []);
  assert.deepEqual(filterModels(catalogOptions(current, discovery), { provider: 'antigravity' }).map((model) => model.id), ['gemini-current']);
  const retained = failCatalogRefresh(discovery, 'antigravity', 'offline');
  assert.deepEqual(publisherOnlyModels(current, retained, 'antigravity'), ['gemini-publisher']);
});

test('temporary gateway outage keeps visible choices but removes verified status', () => {
  const next = retainCatalogOnFailure(status, { gateway: { online: false }, models: [status.models[1]] });
  assert.equal(next.models.find((item) => item.id === 'saved').unavailable, true);
  assert.equal(next.models.find((item) => item.id === 'saved').verified, false);
  assert.deepEqual(initialModels(['saved', 'new-user-alias'], []), ['saved', 'new-user-alias']);
  assert.equal(retainCatalogOnFailure(status, { gateway: { online: true }, models: [] }).models.length, 0);
});

test('search uses labels, IDs and providers while excluding images and tests from text roles', () => {
  const catalog = [{ id: 'future-text', label: 'Researcher', provider: 'connection:b', provider_label: 'Lab' }, { id: 'gpt-image-2.5' }, { id: 'text-embedding-3-small' }, { id: 'test-model', testing: true }];
  assert.deepEqual(filterModels(catalog, { query: 'lab researcher' }).map((m) => m.id), ['future-text']);
  assert.deepEqual(filterModels(catalog, { capability: 'image' }).map((m) => m.id), ['gpt-image-2.5']);
  assert.equal(filterModels(catalog).length, 1);
  assert.equal(filterModels(catalog, { testing: true }).length, 2);
  assert.equal(filterModels(catalog, { selected: ['test-model'] }).length, 2);
  assert.deepEqual(initialModels([], catalog), ['future-text']);
});

test('removed selected models remain inspectable and removable without changing selection', () => {
  const selected = ['deleted'];
  const options = selectionOptions([{ id: 'fresh' }], selected);
  assert.deepEqual(selected, ['deleted']);
  assert.equal(options.at(-1).unavailable, true);
  assert.equal(filterModels(options, { selected })[0].id, 'deleted');
});
