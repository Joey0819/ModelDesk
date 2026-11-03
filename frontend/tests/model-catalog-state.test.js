import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogOptions, filterModels, initialModels, mergeDiscoveries, retainCatalogOnFailure, selectionOptions } from '../src/model-catalog-state.js';

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
