import { isImageModel } from './mission-teams.js';

export const modelName = (model) => model?.label || model?.id?.replace(/^connection:[^:]+:/, '') || '';
export const modelCapability = (model) => model.capability || (isImageModel(model.id) ? 'image' : /embedding|rerank|whisper|(?:^|-)tts(?:-|$)|transcri|realtime|sora|veo-/i.test(modelName(model)) ? 'other' : 'text');
export const modelSource = (model) => model.provider_label || model.connection_name || ({ codex: 'ChatGPT 网关', antigravity: 'Google / Antigravity 网关' }[model.provider]) || '订阅网关';

export function retainCatalogOnFailure(previous, next) {
  if (!previous) return next;
  const missingSource = (model) => model.id.startsWith('connection:') ? Boolean(next.connections_error) : !next.gateway?.online;
  const known = new Set((next.models || []).map((model) => model.id));
  return { ...next, models: [...(next.models || []), ...(previous.models || []).filter((model) => !known.has(model.id) && missingSource(model)).map((model) => ({ ...model, unavailable: true, verified: false }))] };
}

export function catalogOptions(status, discovery) {
  const entries = new Map((status?.models || []).map((model) => [model.id, { ...model, registered: true }]));
  for (const source of discovery?.sources || []) {
    const connection = source.connection;
    // Never resurrect a deleted/disabled connection from an earlier discovery.
    if (!connection.enabled || source.skipped || !status?.providers?.some((provider) => provider.id === `connection:${connection.id}` && provider.enabled !== false)) continue;
    for (const model of source.models || []) {
      const id = `connection:${connection.id}:${model.id}`;
      if (entries.has(id)) continue;
      entries.set(id, { ...model, id, model_id: model.id, connection_id: connection.id, connection_name: connection.name, provider: `connection:${connection.id}`, provider_label: connection.name, testing: connection.testing, registered: false, expected_updated_at: connection.updated_at, stale: Boolean(source.error), verified: false });
    }
  }
  return [...entries.values()];
}

export function mergeDiscoveries(previous, next) {
  return { ...next, sources: next.sources.map((source) => ({ ...source, models: source.error ? previous?.sources?.find((item) => item.connection.id === source.connection.id && item.connection.updated_at === source.connection.updated_at)?.models || [] : source.models })) };
}

export function filterModels(catalog, { query = '', provider = '', capability = 'text', selected = [], testing = false } = {}) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return catalog.filter((model) => (capability === 'all' || modelCapability(model) === capability)
    && (!model.testing || testing || selected.includes(model.id))
    && (!provider || model.provider === provider)
    && terms.every((term) => `${modelName(model)} ${model.id} ${modelSource(model)}`.toLocaleLowerCase().includes(term)))
    .sort((a, b) => Number(selected.includes(b.id)) - Number(selected.includes(a.id)) || Number(a.unavailable || false) - Number(b.unavailable || false) || Number(b.verified || false) - Number(a.verified || false) || Number(b.registered || false) - Number(a.registered || false) || modelSource(a).localeCompare(modelSource(b)) || modelName(a).localeCompare(modelName(b), undefined, { numeric: true }));
}

export function selectionOptions(catalog, selected) {
  return [...catalog, ...selected.filter((id) => id && !catalog.some((model) => model.id === id)).map((id) => ({ id, registered: true, unavailable: true, provider: 'unavailable', provider_label: '已保存选择' }))];
}

export function initialModels(saved, available) {
  // A temporary outage must never replace a user's persisted choice.
  if (saved?.length) return [...new Set(saved)].slice(0, 4);
  const text = available.filter((model) => !model.testing && !model.unavailable && modelCapability(model) === 'text');
  return [...new Set([...text.filter((model) => model.verified), ...text].map((model) => model.id))].slice(0, 2);
}
