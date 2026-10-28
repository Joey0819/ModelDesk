import { useEffect, useRef, useState } from 'react';
import { API_BASE, apiFetch } from '../api-base';
import { loadMediaCapabilities } from '../delivery-media';
import { groupOutputOptions } from '../group-state';

const providerNames = { openai: 'ChatGPT / OpenAI', gemini: 'Gemini', xai: 'Grok' };

export function MediaCapabilityStatus({ state, onRefresh }) {
  const entries = state.entries || [];
  return <section className="group-media-capabilities" aria-label="实际生图能力" aria-live="polite">
    <header><strong>实际生图能力</strong><button type="button" disabled={state.status === 'loading'} onClick={onRefresh}>{state.status === 'loading' ? '检查中…' : '重新检查'}</button></header>
    {state.status === 'error' ? <p className="group-media-capability-error">{state.error || '暂时无法读取生图能力，可继续提交任务。'}</p> : state.status === 'idle' ? <p>展开后检查当前登录网关与模型连接。</p> : state.status === 'loading' && !entries.length ? <p>正在读取可用生图模型，不影响发送任务。</p> : !entries.length ? <p>未发现生图模型，请在“Agent 与连接”检查已有登录或模型连接。</p> : <ul>{entries.map((entry) => <li key={entry.id}><div><strong>{providerNames[entry.provider] || entry.provider}</strong><span title={entry.model}>{entry.label || entry.model}</span></div><span className={entry.verified ? 'group-media-capability-verified' : ''}>{!entry.available ? '暂不可用' : entry.verified ? '已实测生图' : '待验证'}</span></li>)}</ul>}
    {entries.length > 0 && <p>“已实测”表示该模型曾返回并保存了实际图片；候选模型仍需本次调用确认。</p>}
  </section>;
}

export default function DeliveryOutputOptions({ value, onChange, disabled = false }) {
  const [capabilities, setCapabilities] = useState({ status: 'idle', entries: [], error: '' });
  const controller = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  const refreshCapabilities = async () => {
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setCapabilities((previous) => ({ ...previous, status: 'loading', error: '' }));
    try {
      const entries = await loadMediaCapabilities(API_BASE, apiFetch, pending.signal);
      if (!pending.signal.aborted) setCapabilities({ status: 'ready', entries, error: '' });
    } catch (error) {
      if (!pending.signal.aborted) setCapabilities((previous) => ({ ...previous, status: 'error', error: error.message }));
    }
  };
  const options = groupOutputOptions(value);
  const update = (change) => onChange(groupOutputOptions(options, change));
  const providers = options.outputFormat === 'video' ? [['auto', '自动选择'], ['muse', 'Muse（Mac 本机）']] : [['auto', '自动选择'], ['openai', 'OpenAI 生图'], ['gemini', 'Gemini 生图'], ['muse', 'Muse（Mac 本机）']];
  return <div className="group-output-options">
    <label>成果形式<select aria-label="成果形式" disabled={disabled} value={options.outputFormat} onChange={(event) => update({ outputFormat: event.target.value })}><option value="auto">按要求自动</option><option value="text">仅文字</option><option value="illustrated">图文并茂</option><option value="video">视频成果</option></select></label>
    {options.outputFormat !== 'text' && <details className="group-output-settings" onToggle={(event) => { if (event.currentTarget.open) refreshCapabilities(); }}><summary>生成设置</summary><div>
      {options.outputFormat !== 'video' && <label>{options.outputFormat === 'auto' ? '未指定时的配图数量' : '配图数量'}<select aria-label="配图数量" disabled={disabled} value={options.imageCount} onChange={(event) => update({ imageCount: Number(event.target.value) })}>{[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}</select></label>}
      <label>生成工具<select aria-label="图片与视频生成工具" disabled={disabled} value={options.mediaProvider} onChange={(event) => update({ mediaProvider: event.target.value })}>{providers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <p>{options.outputFormat === 'auto' ? '按任务描述确定实际数量；例如“每章一图”会逐章配图，单轮最多 12 份媒体。上方数量只在未指定时使用。' : options.outputFormat === 'video' ? '视频需由可用的 Muse 生成并回传实际文件。' : '正文与实际配图将一起呈现在成果栏。'} 复用当前登录网关或已添加的 API 连接；实际能力以调用结果为准。Muse 需要 Mac 原生应用。</p>
      <MediaCapabilityStatus state={capabilities} onRefresh={refreshCapabilities} />
    </div></details>}
  </div>;
}
