import { ANTIGRAVITY_AGENT_ID, ANTIGRAVITY_AGENT_LABEL, isAntigravityAgentId, isImageModel } from './mission-teams.js';

export const subscriptionSources = { codex: 'ChatGPT 网关', antigravity: 'Google / Antigravity 网关' };
export const isSubscriptionSource = (source) => Object.hasOwn(subscriptionSources, source);
export const nativeAgentSource = 'antigravity-agent';
export function modelName(model) {
  if (model?.label) return model.label;
  if (isAntigravityAgentId(model?.id)) return model.id === ANTIGRAVITY_AGENT_ID ? ANTIGRAVITY_AGENT_LABEL : model.id.slice(ANTIGRAVITY_AGENT_ID.length + 1);
  return model?.id?.replace(/^connection:[^:]+:/, '') || '';
}
export function roleModelName(model) {
  const name = modelName(model);
  return isAntigravityAgentId(model?.id) && model.id !== ANTIGRAVITY_AGENT_ID ? `${ANTIGRAVITY_AGENT_LABEL} · ${name}` : name;
}
export const modelCapability = (model) => model.capability || (isImageModel(model.id) ? 'image' : /embedding|rerank|whisper|(?:^|-)tts(?:-|$)|transcri|realtime|sora|veo-/i.test(modelName(model)) ? 'other' : 'text');
export const modelSource = (model) => model.provider_label || model.connection_name || (isSubscriptionSource(model.provider) ? subscriptionSources[model.provider] : '订阅网关');
export const catalogReadTime = (checkedAt) => new Date(checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export function gatewayDiscovery(discovery, source) {
  return (isSubscriptionSource(source) && discovery?.subscriptionGateways?.[source]) || discovery?.gateway;
}

export function publisherOnlyModels(status, discovery, source) {
  const ids = gatewayDiscovery(discovery, source)?.providers?.[source]?.catalog_only ?? status?.gateway?.cached_publication?.[source]?.catalog_only;
  if (!Array.isArray(ids)) return [];
  const available = new Set((status?.models || []).filter((model) => model.provider === source && !model.unavailable).map((model) => model.id));
  return [...new Set(ids.filter((id) => typeof id === 'string' && id && !available.has(id)))];
}

export function retainCatalogOnFailure(previous, next) {
  if (!previous) return next;
  const missingSource = (model) => !isAntigravityAgentId(model.id) && (model.id.startsWith('connection:') ? Boolean(next.connections_error) : !next.gateway?.online);
  const known = new Set((next.models || []).map((model) => model.id));
  return { ...next, models: [...(next.models || []), ...(previous.models || []).filter((model) => !known.has(model.id) && missingSource(model)).map((model) => ({ ...model, unavailable: true, verified: false }))] };
}

export function catalogOptions(status, discovery) {
  const entries = new Map((status?.models || []).map((model) => {
    const gateway = gatewayDiscovery(discovery, model.provider);
    const stale = !isAntigravityAgentId(model.id) && !model.id.startsWith('connection:') && Boolean(status?.gateway?.stale || gateway?.stale || gateway?.error);
    return [model.id, { ...model, ...(isAntigravityAgentId(model.id) && !model.installed ? { unavailable: true } : {}), registered: true, ...(stale ? { stale } : {}) }];
  }));
  for (const source of discovery?.sources || []) {
    const connection = source.connection;
    // Never resurrect a deleted/disabled connection from an earlier discovery.
    if (!connection.enabled || source.skipped || !status?.providers?.some((provider) => provider.id === `connection:${connection.id}` && provider.enabled !== false)) continue;
    for (const model of source.models || []) {
      const id = `connection:${connection.id}:${model.id}`;
      if (entries.has(id)) continue;
      entries.set(id, { ...model, id, model_id: model.id, connection_id: connection.id, connection_name: connection.name, provider: `connection:${connection.id}`, provider_label: connection.name, testing: connection.testing, registered: false, expected_updated_at: connection.updated_at, stale: Boolean(source.error || source.stale), verified: false });
    }
  }
  return [...entries.values()];
}

function hostModelIds(status, source) {
  if (!status?.gateway?.online || status.gateway.stale || status.gateway.error || !Array.isArray(status.models)) return null;
  return status.models.filter((model) => model.provider === source && !model.unavailable).map((model) => model.id);
}

function availabilityChanges(prior, current) {
  if (!Array.isArray(current)) return null;
  if (!Array.isArray(prior)) return { added: [], removed: [], compared: false };
  const before = new Set(prior), after = new Set(current);
  return {
    added: [...after].filter((id) => !before.has(id)),
    removed: [...before].filter((id) => !after.has(id)),
    compared: true,
  };
}

export function mergeDiscoveries(previous, next, statusBeforeRead) {
  if (next.scope === nativeAgentSource) {
    const current = next.native_agent;
    const baseline = previous?.nativeAgent?.listed ?? nativeModelIds(statusBeforeRead);
    return { ...previous, nativeAgent: { ...current, listed: current.listed ?? baseline,
      changes: current.error || current.stale ? null : availabilityChanges(baseline, current.listed) } };
  }
  const gateway = { ...next.gateway, checked_at: next.gateway?.checked_at || next.checked_at };
  const mergeGateway = (source) => {
    const prior = gatewayDiscovery(previous, source);
    if (!gateway.stale && !gateway.error) {
      const baseline = prior?.providers?.[source]?.listed ?? hostModelIds(statusBeforeRead, source);
      return { ...gateway, changes: availabilityChanges(baseline, gateway.providers?.[source]?.listed) };
    }
    return { ...gateway, checked_at: gateway.checked_at || prior?.checked_at, providers: gateway.providers || prior?.providers };
  };
  if (isSubscriptionSource(next.scope)) {
    return { ...previous, subscriptionGateways: { ...previous?.subscriptionGateways, [next.scope]: mergeGateway(next.scope) } };
  }
  return {
    ...next,
    nativeAgent: previous?.nativeAgent,
    gateway,
    subscriptionGateways: Object.fromEntries(Object.keys(subscriptionSources).map((source) => [source, mergeGateway(source)])),
    sources: next.sources.map((source) => ({ ...source, models: source.error ? previous?.sources?.find((item) => item.connection.id === source.connection.id && item.connection.updated_at === source.connection.updated_at)?.models || [] : source.models })),
  };
}

function nativeModelIds(status) {
  if (!Array.isArray(status?.models)) return null;
  const agent = status.models.find((item) => item.id === ANTIGRAVITY_AGENT_ID);
  if (!agent?.installed || !agent.catalog_checked_at) return null;
  return status.models.filter((item) => item.provider === nativeAgentSource && item.id !== ANTIGRAVITY_AGENT_ID && !item.unavailable).map((item) => item.id);
}

export function mergeHostModelRead(previous, scope, before, current) {
  if (scope === nativeAgentSource) {
    const listed = nativeModelIds(current);
    if (!listed) return previous;
    return mergeDiscoveries(previous, { scope, native_agent: {
      checked_at: current.models.find((item) => item.id === ANTIGRAVITY_AGENT_ID).catalog_checked_at, listed,
    } }, before);
  }
  if (!current?.gateway?.online || current.gateway.stale || current.gateway.error || !Array.isArray(current.models)) return previous;
  const sources = isSubscriptionSource(scope) ? [scope] : Object.keys(subscriptionSources);
  const providers = Object.fromEntries(sources.map((source) => [source, { listed: hostModelIds(current, source) }]));
  return mergeDiscoveries(previous, { scope, checked_at: current.checked_at, gateway: { providers }, sources: previous?.sources || [] }, before);
}

export function failCatalogRefresh(previous, scope, error) {
  if (scope === nativeAgentSource) return mergeDiscoveries(previous, { scope, native_agent: { error, stale: true } });
  return mergeDiscoveries(previous, {
    scope,
    gateway: { stale: true, error },
    sources: (previous?.sources || []).map((source) => ({ ...source, stale: true })),
  });
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
  const text = available.filter((model) => !model.group_only && !isAntigravityAgentId(model.id) && !model.testing && !model.unavailable && modelCapability(model) === 'text');
  return [...new Set([...text.filter((model) => model.verified), ...text].map((model) => model.id))].slice(0, 2);
}
