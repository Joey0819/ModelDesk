import { useEffect, useId, useRef, useState } from 'react';
import { useModelCatalog } from '../model-catalog-context';
import { catalogReadTime, filterModels, isSubscriptionSource, modelCapability, modelName, modelSource, nativeAgentSource as agentProvider, roleModelName, selectionOptions, subscriptionSources } from '../model-catalog-state';
import { ANTIGRAVITY_AGENT_ID, ANTIGRAVITY_AGENT_LABEL, isAntigravityAgentId } from '../mission-teams';
import { PublisherCatalog } from './SubscriptionCatalog';
import { Icon } from './WorkbenchResults';
import './ModelPicker.css';

const isGeminiId = (id) => /(?:^|[/:])gemini(?:-|$)/i.test(id);
const geminiVersion = (id) => id.match(/(?:^|[/:])gemini-(\d+(?:\.\d+)*)/i)?.[1] || '';
const newestGeminiFirst = (a, b) => geminiVersion(b).localeCompare(geminiVersion(a), undefined, { numeric: true });
const agentReady = (agent) => Boolean(agent?.installed && !agent.unavailable);

function AntigravityFallback({ agent, loginVerified, active, selected, busy, onSelect, onBrowse }) {
  const ready = agentReady(agent);
  const setup = <>运行 <code>agy</code> 登录 Google。</>;
  return <section className="model-picker-agent" aria-label="Antigravity Agent">
    <div>
      <strong>{ANTIGRAVITY_AGENT_LABEL}</strong>
      <small>{ready ? loginVerified ? '在主机使用 Google 官方 CLI 执行文字任务。' : <>已检测到主机 CLI。若尚未登录，请{setup}</> : <>在主机安装官方 CLI，{setup}</>}</small>
      {(!ready || !loginVerified) && <a href="https://antigravity.google/docs/getting-started?tab=cli" target="_blank" rel="noreferrer">官方安装与登录指南</a>}
    </div>
    {active && <div className="model-picker-agent-actions">
      <button type="button" className="subtle-button" aria-pressed={selected} disabled={busy || !ready} onClick={onSelect}>{selected ? '当前使用 Antigravity Agent' : '改用 Antigravity Agent'}</button>
      {onBrowse && <button type="button" className="subtle-button" disabled={busy} onClick={onBrowse}>查看 Agent 可选型号</button>}
    </div>}
  </section>;
}

function GatewayRefreshFeedback({ details, refreshing, native }) {
  const changes = details?.changes;
  if (!refreshing && (!changes || details.stale || details.error)) return null;
  let result = native ? '正在读取 Agent 型号…' : '正在读取网关目录…';
  if (!refreshing) {
    const counts = [changes.added.length && `新增 ${changes.added.length} 个`, changes.removed.length && `移除 ${changes.removed.length} 个`].filter(Boolean);
    const label = native ? 'Agent 型号列表' : '网关可选模型';
    result = !changes.compared ? `已读取${label}` : counts.length ? `${native ? label : '网关目录'}更新 · ${counts.join(' · ')}` : `${label}未变化`;
  }
  return <p className="model-picker-refresh-result" role="status">{result}</p>;
}

function InlineGeminiDirectory({ ids, stale }) {
  if (!ids.length) return null;
  return <section className="model-picker-published" aria-label="已发布的 Gemini 模型">
    <h3>网关已发布 · 本机网关暂不可选{stale && <small>上次读取</small>}</h3>
    <ul role="list">{ids.map((id) => <li key={id}><code>{id}</code></li>)}</ul>
  </section>;
}

function publisherMatches(service, details, provider, query, visible) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const matches = (id) => terms.every((term) => id.toLocaleLowerCase().includes(term));
  if (provider) return { models: (details?.publisherOnly || []).filter(matches), stale: details?.publisherStale };
  if (!service || !terms.some((term) => /\d|\w-\w/.test(term))) return { models: [] };

  const selectableIds = new Set(visible.filter((model) => !model.unavailable && (model.registered !== false || !service.remote))
    .map((model) => model.model_id || model.id.replace(/^connection:[^:]+:/, '')));
  const reports = Object.keys(subscriptionSources).map((source) => {
    const result = service.sourceDetails(source);
    return { models: result.publisherOnly.filter((id) => matches(id) && !selectableIds.has(id)), stale: result.publisherStale };
  });
  return {
    models: [...new Set(reports.flatMap((report) => report.models))],
    stale: reports.some((report) => report.models.length && report.stale),
  };
}

function PickerPanel({ label, catalog, selected, multiple, max, capability, emptyLabel, allowGroupAgents, onSelect, onClose, service }) {
  const dialog = useRef(null), search = useRef(null);
  const titleId = useId(), listId = useId();
  const [query, setQuery] = useState(''), [testing, setTesting] = useState(false);
  const [provider, setProvider] = useState(() => {
    const current = !multiple && catalog.find((model) => model.id === selected[0]);
    if (allowGroupAgents && isAntigravityAgentId(current?.id)) return agentProvider;
    return current?.provider === 'antigravity' && isGeminiId(current.id) ? current.provider : '';
  });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const candidates = selectionOptions(catalog, selected);
  const visible = filterModels(candidates, { query, provider, capability, selected, testing });
  const nativeOptions = visible.filter((model) => isAntigravityAgentId(model.id));
  const visibleInOrder = !provider && nativeOptions.length ? [...visible.filter((model) => !isAntigravityAgentId(model.id)), ...nativeOptions] : visible;
  const geminiScope = provider === 'antigravity';
  const nativeScope = provider === agentProvider;
  const agent = allowGroupAgents && candidates.find((model) => model.id === ANTIGRAVITY_AGENT_ID);
  const loginVerified = allowGroupAgents && candidates.some((row) => row.provider === agentProvider && agentReady(row) && row.verified);
  const showAgentHelp = allowGroupAgents && (geminiScope || nativeScope || (!agentReady(agent) && query.trim() && visible.includes(agent)));
  const geminiApiSetup = geminiScope && !service?.remote;
  const gemini = geminiScope ? visible.filter((model) => isGeminiId(model.id)).sort((a, b) => newestGeminiFirst(a.id, b.id)) : [];
  const other = geminiScope ? visible.filter((model) => !isGeminiId(model.id)) : [];
  const selectableGemini = gemini.filter((model) => !model.unavailable).length;
  const roleName = { text: '文字', image: '生图' }[capability] || '';
  const resultCount = geminiScope ? `${selectableGemini} 个可选 Gemini ${roleName}模型`
    : nativeScope ? `${nativeOptions.filter((model) => model.id !== ANTIGRAVITY_AGENT_ID && agentReady(model)).length} 个可选 CLI 型号`
      : `${visible.length - nativeOptions.length} 个模型${nativeOptions.length ? ` · ${nativeOptions.length} 个 Agent 选项` : ''}`;
  const providers = new Map(candidates.filter((model) => capability === 'all' || modelCapability(model) === capability).filter((model) => !model.testing || testing || selected.includes(model.id)).map((model) => [model.provider || 'other', modelSource(model)]));
  if (service) for (const [id, name] of Object.entries(subscriptionSources)) providers.set(id, name);
  if (allowGroupAgents) providers.set(agentProvider, ANTIGRAVITY_AGENT_LABEL);
  if (provider.startsWith('connection:') && !providers.has(provider)) {
    const connectionModel = candidates.find((model) => model.provider === provider);
    providers.set(provider, connectionModel ? modelSource(connectionModel) : 'API 连接');
  }
  const details = service?.sourceDetails(provider);
  const publisher = publisherMatches(service, details, provider, query, visible);
  const publishedGemini = geminiScope ? publisher.models.filter(isGeminiId).sort(newestGeminiFirst) : [];
  const otherPublished = geminiScope ? publisher.models.filter((id) => !isGeminiId(id)) : publisher.models;
  const showOther = Boolean(query.trim() || other.some((model) => selected.includes(model.id)));
  useEffect(() => {
    const modal = dialog.current;
    const trigger = document.activeElement;
    modal.showModal(); search.current?.focus();
    return () => { modal.close(); if (trigger?.isConnected) trigger.focus(); };
  }, []);
  const choose = async (model) => {
    if (!model || busy || (isAntigravityAgentId(model.id) && !agentReady(model))) return;
    setError('');
    if (multiple && selected.includes(model.id)) { onSelect(selected.filter((id) => id !== model.id)); return; }
    setBusy(true);
    try {
      const id = service?.ensureModel ? await service.ensureModel(model) : model.id;
      onSelect(multiple ? [...selected, id] : id);
      if (!multiple) onClose();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const openConnections = () => {
    if (geminiApiSetup) {
      service.onOpenConnections('gemini', (connectionId) => {
        if (!connectionId) return;
        setProvider(`connection:${connectionId}`);
        setQuery('');
      });
      return;
    }
    onClose();
    service.onOpenConnections();
  };
  const keyNav = (event) => {
    if (event.target !== search.current && !event.target.closest('[data-model-option]')) return;
    const submitSearch = event.key === 'Enter' && event.target === search.current;
    const movement = event.target === search.current ? ['ArrowDown', 'ArrowUp'] : ['ArrowDown', 'ArrowUp', 'Home', 'End'];
    if (!submitSearch && !movement.includes(event.key)) return;
    const options = [...dialog.current.querySelectorAll('[data-model-option]:not(:disabled)')].filter((option) => !option.closest('details:not([open])'));
    if (submitSearch) {
      event.preventDefault();
      options[0]?.click();
      return;
    }
    if (!options.length) return;
    const index = options.indexOf(document.activeElement);
    const forward = event.key === 'ArrowDown';
    const next = event.key === 'Home' || (index < 0 && forward) ? 0
      : event.key === 'End' || index < 0 ? options.length - 1
        : (index + (forward ? 1 : -1) + options.length) % options.length;
    event.preventDefault(); options[next].focus();
  };
  const renderOption = (model) => {
    const chosen = selected.includes(model.id);
    const nativeAgent = isAntigravityAgentId(model.id);
    const unavailable = model.unavailable || (model.registered === false && service?.remote);
    const added = isSubscriptionSource(provider) && !details?.stale && details?.changes?.compared && details.changes.added.includes(model.id);
    return <button key={model.id} type="button" data-model-option className={`model-picker-option ${chosen ? 'is-selected' : ''}`} aria-pressed={chosen} disabled={busy || (nativeAgent && !agentReady(model)) || (!chosen && (unavailable || (multiple && selected.length >= max)))} onClick={() => choose(model)}>
      <span className="model-picker-identity"><strong>{modelName(model)}</strong><small>{nativeAgent ? model.id === ANTIGRAVITY_AGENT_ID ? '使用 Google 官方 CLI 默认型号' : 'Antigravity Agent · Google 官方 CLI' : modelSource(model)}{model.testing ? ' · 测试连接' : ''}</small>{!nativeAgent && model.label && model.label !== model.id.replace(/^connection:[^:]+:/, '') && <small>{model.id.replace(/^connection:[^:]+:/, '')}</small>}</span>
      <span className="model-picker-badges"><span>{added && <small className="is-new">本次新增</small>}{model.registered && model.stale && !model.unavailable && <small>使用上次目录</small>}<small className={model.verified ? 'is-verified' : ''}>{nativeAgent && !agentReady(model) ? '需要主机 CLI' : model.unavailable ? '暂不可用 · 保留选择' : model.registered === false ? model.stale ? '上次发现 · 加入选用' : '新发现 · 加入选用' : model.verified ? model.verification_scope === 'stored_image' ? '曾实测生图' : '近期调用通过' : '尚未验证调用'}</small></span>{chosen && <Icon name="check" size={16} />}</span>
    </button>;
  };
  return <dialog ref={dialog} className="model-picker-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }} onClick={(event) => { if (event.target === dialog.current && !busy) onClose(); }} onKeyDown={keyNav}>
    <header className="model-picker-heading"><div><h2 id={titleId}>{label}</h2><p>{capability === 'image' ? '选择实际生成图片的模型' : '选择参与推理、写作和协作的模型'}{multiple && ` · 最多 ${max} 个`}</p></div><button type="button" className="icon-button" aria-label="关闭模型选择" disabled={busy} onClick={onClose}><Icon name="close" size={18} /></button></header>
    <div className="model-picker-tools"><label className="model-picker-search"><Icon name="search" size={17} /><input ref={search} aria-label="搜索模型" aria-controls={listId} placeholder="搜索模型名称、ID 或服务来源" value={query} onChange={(event) => setQuery(event.target.value)} /></label><button type="button" className="subtle-button" disabled={busy || service?.refreshing || !service?.refresh} onClick={() => service.refresh(provider)}><Icon name="refresh" size={15} />{service?.refreshing ? '正在刷新' : '刷新列表'}</button></div>
    <div className="model-picker-filters"><select aria-label="筛选模型来源" value={provider} onChange={(event) => setProvider(event.target.value)}><option value="">全部来源</option>{[...providers].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><span aria-live="polite">{resultCount}{multiple ? ` · 已选 ${selected.length}/${max}` : ''}</span>{candidates.some((model) => model.testing) && <label><input type="checkbox" checked={testing} onChange={(event) => setTesting(event.target.checked)} />显示测试连接</label>}</div>
    {(isSubscriptionSource(provider) || nativeScope) && <GatewayRefreshFeedback details={details} refreshing={service?.refreshingSource === provider} native={nativeScope} />}
    {(error || details?.error) && <p className="model-picker-warning" role="alert">{error || details.error}</p>}
    {details?.sources?.some((source) => source.error || source.truncated) && <details className="model-picker-sources"><summary>部分来源需要检查</summary>{details.sources.filter((source) => source.error || source.truncated).map((source) => <p key={source.connection.id}>{source.connection.name}：{source.error || '模型目录较大，显示前 1000 个；可在连接设置中手动添加其他 ID。'}</p>)}</details>}
    <div id={listId} className="model-picker-list" role="group" aria-label="可选模型">
      {emptyLabel && !query && !provider && <button type="button" data-model-option className={`model-picker-option ${!selected[0] ? 'is-selected' : ''}`} aria-pressed={!selected[0]} disabled={busy} onClick={() => { onSelect(''); onClose(); }}><span><strong>{emptyLabel}</strong><small>自动使用协作组的首位文字模型</small></span>{!selected[0] && <Icon name="check" size={16} />}</button>}
      {(geminiScope ? gemini : visibleInOrder).map(renderOption)}
      {other.length > 0 && <details key={query.trim()} className="model-picker-other-models" open={showOther}><summary>同一网关的其他模型（{other.length}）</summary>{other.map(renderOption)}</details>}
      {!visible.length && <p className="model-picker-empty">{publisher.models.length ? '没有可选的匹配模型。' : query ? '没有匹配的模型。换个关键词，或刷新已连接服务。' : '此来源尚无可选模型。刷新列表，或添加模型连接。'}</p>}
    </div>
    <InlineGeminiDirectory ids={publishedGemini} stale={publisher.stale} />
    {showAgentHelp && <AntigravityFallback agent={agent} loginVerified={loginVerified} active={geminiScope} selected={selected.includes(ANTIGRAVITY_AGENT_ID)} busy={busy || service?.refreshing} onSelect={() => choose(agent)} onBrowse={service?.refresh && (() => { setProvider(agentProvider); setQuery(''); service.refresh(agentProvider); })} />}
    <footer className="model-picker-footer"><div><span>{details?.checkedAt ? `模型列表检查于 ${catalogReadTime(details.checkedAt)}` : nativeScope ? '点击刷新，读取主机 CLI 型号' : '点击刷新，读取已连接服务的模型'}</span>{details?.gatewayReread && !details?.changes && <small>已读取网关当前的模型列表。</small>}{!geminiScope && !nativeScope && <small>刷新只读取目录，不发送生成任务。新发现的 API 模型在选用时保存到连接。</small>}</div>{details?.publisherWarning && <p className="publisher-catalog-hint" role="status">{details.publisherWarning}</p>}<PublisherCatalog key={provider} models={otherPublished} stale={publisher.stale} scoped={Boolean(provider)} /><div>{!nativeScope && service?.onOpenConnections && <button type="button" className="subtle-button" disabled={busy} onClick={openConnections}>{geminiApiSetup ? '设置 Gemini API 连接' : '添加模型连接'}</button>}{multiple && <button type="button" className="primary-button" disabled={busy} onClick={onClose}>完成选择</button>}</div></footer>
  </dialog>;
}

export default function ModelPicker({ label = '选择模型', value, onChange, catalog: fallback = [], multiple = false, max = 4, capability = 'text', allowGroupAgents = false, emptyLabel, disabled = false, open, onOpenChange }) {
  const service = useModelCatalog();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = open ?? internalOpen;
  const setOpen = onOpenChange || setInternalOpen;
  const groupText = allowGroupAgents && capability === 'text';
  const sourceCatalog = service?.catalog || fallback;
  const catalog = groupText ? sourceCatalog : sourceCatalog.filter((model) => !model.group_only && !isAntigravityAgentId(model.id));
  const selected = multiple ? value || [] : value ? [value] : [];
  const names = selected.map((id) => roleModelName(catalog.find((model) => model.id === id) || { id }));
  return <div className="model-picker">
    <button type="button" className="model-picker-trigger" aria-label={label} aria-haspopup="dialog" aria-expanded={isOpen} title={names.join(' + ')} disabled={disabled} onClick={() => setOpen(true)}><span>{names.join(' + ') || emptyLabel || '选择模型'}</span><Icon name="chevron" size={15} /></button>
    {isOpen && <PickerPanel label={label} catalog={catalog} selected={selected} multiple={multiple} max={max} capability={capability} allowGroupAgents={groupText} emptyLabel={emptyLabel} onSelect={onChange} onClose={() => setOpen(false)} service={service} />}
  </div>;
}
