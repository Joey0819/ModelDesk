import { useEffect, useId, useRef, useState } from 'react';
import { useModelCatalog } from '../model-catalog-context';
import { filterModels, modelCapability, modelName, modelSource, selectionOptions } from '../model-catalog-state';
import { Icon } from './WorkbenchResults';
import './ModelPicker.css';

function PickerPanel({ label, catalog, selected, multiple, max, capability, emptyLabel, onSelect, onClose, service }) {
  const dialog = useRef(null), search = useRef(null);
  const titleId = useId(), listId = useId();
  const [query, setQuery] = useState(''), [provider, setProvider] = useState(''), [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const candidates = selectionOptions(catalog, selected);
  const visible = filterModels(candidates, { query, provider, capability, selected, testing });
  const providers = [...new Map(candidates.filter((model) => capability === 'all' || modelCapability(model) === capability).filter((model) => !model.testing || testing || selected.includes(model.id)).map((model) => [model.provider || 'other', modelSource(model)])).entries()];
  useEffect(() => {
    const modal = dialog.current;
    const trigger = document.activeElement;
    modal.showModal(); search.current?.focus();
    return () => { modal.close(); if (trigger?.isConnected) trigger.focus(); };
  }, []);
  const choose = async (model) => {
    if (busy) return;
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
  const keyNav = (event) => {
    if (event.key === 'Enter' && event.target === search.current) {
      event.preventDefault();
      dialog.current.querySelector('[data-model-option]:not(:disabled)')?.click();
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || event.target.tagName === 'SELECT') return;
    if (event.target === search.current && !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    const options = [...dialog.current.querySelectorAll('[data-model-option]:not(:disabled)')];
    if (!options.length) return;
    const index = options.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    event.preventDefault(); options[next].focus();
  };
  return <dialog ref={dialog} className="model-picker-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }} onClick={(event) => { if (event.target === dialog.current && !busy) onClose(); }} onKeyDown={keyNav}>
    <header className="model-picker-heading"><div><h2 id={titleId}>{label}</h2><p>{capability === 'image' ? '选择实际生成图片的模型' : '选择参与推理、写作和协作的模型'}{multiple && ` · 最多 ${max} 个`}</p></div><button type="button" className="icon-button" aria-label="关闭模型选择" disabled={busy} onClick={onClose}><Icon name="close" size={18} /></button></header>
    <div className="model-picker-tools"><label className="model-picker-search"><Icon name="search" size={17} /><input ref={search} aria-label="搜索模型" aria-controls={listId} placeholder="搜索模型名称、ID 或服务来源" value={query} onChange={(event) => setQuery(event.target.value)} /></label><button type="button" className="subtle-button" disabled={busy || service?.refreshing || !service?.refresh} onClick={() => service.refresh()}><Icon name="refresh" size={15} />{service?.refreshing ? '正在刷新' : '刷新列表'}</button></div>
    <div className="model-picker-filters"><select aria-label="筛选模型来源" value={provider} onChange={(event) => setProvider(event.target.value)}><option value="">全部来源</option>{providers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><span aria-live="polite">{visible.length} 个模型{multiple ? ` · 已选 ${selected.length}/${max}` : ''}</span>{candidates.some((model) => model.testing) && <label><input type="checkbox" checked={testing} onChange={(event) => setTesting(event.target.checked)} />显示测试连接</label>}</div>
    {(error || service?.error) && <p className="model-picker-warning" role="alert">{error || service.error}</p>}
    {service?.sources?.some((source) => source.error || source.truncated) && <details className="model-picker-sources"><summary>部分来源需要检查</summary>{service.sources.filter((source) => source.error || source.truncated).map((source) => <p key={source.connection.id}>{source.connection.name}：{source.error || '模型目录较大，显示前 1000 个；可在连接设置中手动添加其他 ID。'}</p>)}</details>}
    <div id={listId} className="model-picker-list" role="group" aria-label="可选模型">
      {emptyLabel && !query && !provider && <button type="button" data-model-option className={`model-picker-option ${!selected[0] ? 'is-selected' : ''}`} aria-pressed={!selected[0]} disabled={busy} onClick={() => { onSelect(''); onClose(); }}><span><strong>{emptyLabel}</strong><small>自动使用协作组的首位文字模型</small></span>{!selected[0] && <Icon name="check" size={16} />}</button>}
      {visible.map((model) => {
        const chosen = selected.includes(model.id);
        const unavailable = model.unavailable || (model.registered === false && service?.remote);
        return <button key={model.id} type="button" data-model-option className={`model-picker-option ${chosen ? 'is-selected' : ''}`} aria-pressed={chosen} disabled={busy || (!chosen && (unavailable || (multiple && selected.length >= max)))} onClick={() => choose(model)}>
          <span className="model-picker-identity"><strong>{modelName(model)}</strong><small>{modelSource(model)}{model.testing ? ' · 测试连接' : ''}</small>{model.label && model.label !== model.id.replace(/^connection:[^:]+:/, '') && <small>{model.id.replace(/^connection:[^:]+:/, '')}</small>}</span>
          <span className="model-picker-badges"><small className={model.verified ? 'is-verified' : ''}>{model.unavailable ? '暂不可用 · 保留选择' : model.registered === false ? model.stale ? '上次发现 · 加入选用' : '新发现 · 加入选用' : model.verified ? model.verification_scope === 'stored_image' ? '曾实测生图' : '近期调用通过' : '尚未验证调用'}</small>{chosen && <Icon name="check" size={16} />}</span>
        </button>;
      })}
      {!visible.length && <p className="model-picker-empty">{query ? '没有匹配的模型。换个关键词，或刷新已连接服务。' : '此来源尚无可选模型。刷新列表，或添加模型连接。'}</p>}
    </div>
    <footer className="model-picker-footer"><div><span>{service?.checkedAt ? `${service.discovered ? '服务目录' : '网关目录'}更新于 ${new Date(service.checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : '点击刷新，读取已连接服务的模型'}</span><small>刷新只读取目录，不发送生成任务。新发现的模型在选用时保存到连接。</small></div><div>{service?.onOpenConnections && <button type="button" className="subtle-button" disabled={busy} onClick={() => { onClose(); service.onOpenConnections(); }}>添加模型连接</button>}{multiple && <button type="button" className="primary-button" disabled={busy} onClick={onClose}>完成选择</button>}</div></footer>
  </dialog>;
}

export default function ModelPicker({ label = '选择模型', value, onChange, catalog: fallback = [], multiple = false, max = 4, capability = 'text', emptyLabel, disabled = false, open, onOpenChange }) {
  const service = useModelCatalog();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = open ?? internalOpen;
  const setOpen = onOpenChange || setInternalOpen;
  const catalog = service?.catalog || fallback;
  const selected = multiple ? value || [] : value ? [value] : [];
  const names = selected.map((id) => modelName(catalog.find((model) => model.id === id) || { id }));
  return <div className="model-picker">
    <button type="button" className="model-picker-trigger" aria-label={label} aria-haspopup="dialog" aria-expanded={isOpen} title={names.join(' + ')} disabled={disabled} onClick={() => setOpen(true)}><span>{names.join(' + ') || emptyLabel || '选择模型'}</span><Icon name="chevron" size={15} /></button>
    {isOpen && <PickerPanel label={label} catalog={catalog} selected={selected} multiple={multiple} max={max} capability={capability} emptyLabel={emptyLabel} onSelect={onChange} onClose={() => setOpen(false)} service={service} />}
  </div>;
}
