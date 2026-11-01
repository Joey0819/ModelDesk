import { CONTEXT_SOURCES, missingSourceSelections } from '../context-source-state';

export function SourceStatus({ kind, state, retry }) {
  if (state.status === 'loading') return <p role="status">正在读取{CONTEXT_SOURCES[kind]}…</p>;
  if (state.status === 'error') return <div className="context-source-error" role="alert"><span>{CONTEXT_SOURCES[kind]}读取失败：{state.error}</span><button type="button" className="subtle-button" onClick={retry}>重试{CONTEXT_SOURCES[kind]}</button></div>;
  return <button type="button" className="context-source-refresh" onClick={retry}>刷新{CONTEXT_SOURCES[kind]}</button>;
}

export function MissingSelections({ ids, state, remove }) {
  const missing = missingSourceSelections(ids, state);
  return missing.length > 0 && <ul className="context-missing-selections" aria-label="尚未确认的已选来源">{missing.map((item) => <li key={item.id}><div><code>{item.id}</code><small>{item.reason}</small></div><button type="button" className="subtle-button" onClick={() => remove(item.id)}>移除选择</button></li>)}</ul>;
}

export function ContextSourceRows({ kind, state, selected, toggle, retry }) {
  return <>
    <SourceStatus kind={kind} state={state} retry={retry} />
    {state.items.map((item) => <div key={item.id} className="context-resource-row"><label><input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} /><span>{item.name}</span><small>{item.project_id ? '本项目' : '全局'}</small></label><details><summary>查看内容与来源</summary><p>{item.scope && `范围：${item.scope} · `}{item.updated_at ? `更新于 ${new Date(item.updated_at).toLocaleString('zh-CN')}` : '未记录更新时间'}</p>{typeof item.content === 'string' && item.content ? <pre>{item.content.slice(0, 6000)}</pre> : <p>此来源暂无可预览的文字。</p>}{item.content?.length > 6000 && <p>此处预览前 6000 字符；执行时的引用范围以运行记录为准。</p>}{item.source_job_id && <small>来源任务：{item.source_job_id}</small>}{item.sha256 && <small>内容指纹：{item.sha256.slice(0, 12)}</small>}<p>来源内容作为参考材料，不会因此增加工具或执行权限。</p></details></div>)}
    {state.status === 'ready' && !state.items.length && <p>没有可选择的{CONTEXT_SOURCES[kind]}。可在“项目资源”中添加。</p>}
    <MissingSelections ids={selected} state={state} remove={toggle} />
  </>;
}
