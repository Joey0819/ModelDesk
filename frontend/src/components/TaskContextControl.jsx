import { useRef } from 'react';

export default function TaskContextControl({ value, onChange, disabled, jobs = [] }) {
  const memoryDialog = useRef(null);
  const latest = jobs.at(-1);
  const previous = latest?.context_history;
  const memory = latest?.task_context;
  const brief = latest?.result?.delivery?.task_brief || latest?.task_brief;
  const fresh = value === 'fresh';
  const documents = memory?.reference_documents || [];
  const documentCount = documents.reduce((total, item) => total + (item.asset_ids?.length ?? item.names?.length ?? 0), 0);
  const mediaCount = memory?.reference_media?.length || 0;
  return <div className="task-context-control">
    <label>本轮上下文 <select aria-label="本轮上下文" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      <option value="continue">继续本群任务</option>
      <option value="fresh">开始新任务，不带入旧对话</option>
    </select></label>
    <small>{value === 'fresh' ? '保留旧记录；本轮从新目标开始。已选项目资料仍有效。' : '沿用原始目标、历次修改和最新底稿；失败不会清空，所有执行成员共享。'}</small>
    {memory && <><button type="button" className="task-memory-button" onClick={() => memoryDialog.current?.showModal()}>{fresh ? '查看上次执行记忆（本轮不带入）' : `上次执行记忆 · ${memory.requests?.length || 0} 条要求${memory.artifact ? ' · 已保留底稿' : ''}${mediaCount ? ` · ${mediaCount} 份素材` : ''}`}</button>
      <dialog ref={memoryDialog} className="task-memory-dialog" aria-label="上次执行的任务记忆">
        <header><strong>上次执行的任务记忆</strong><button type="button" onClick={() => memoryDialog.current?.close()} aria-label="关闭任务记忆">关闭</button></header>
      <div className="task-memory-body">
        <p className="task-memory-scope">{fresh ? '已选择开始新任务：以下是上次执行的记录，不会作为本轮任务记忆带入。已选项目资料仍有效。' : `上次执行实际准备的记忆（第 ${memory.revision} 轮），不包含该次执行后才生成的成果。下一轮发送时会重新整理最新成果，并追加本轮要求；后续修改覆盖冲突的旧要求。`}</p>
        <div className="task-memory-inventory" aria-label="记忆内容清单"><span>{memory.requests?.length || 0} 条用户要求</span><span>{documentCount} 份历史文本资料</span><span>{mediaCount} 份可复用素材</span></div>
        {brief && <section className="task-brief-summary" aria-label="版本化任务要点"><h3>任务要点 · 第 {brief.revision} 版</h3><p>原始目标与后续修改逐条保留。最新修改覆盖冲突项，未修改的要求继续有效；每个成员的交接绑定本版本。</p>{brief.pending_items?.length > 0 && <><strong>此前未完成</strong><ul>{brief.pending_items.map((item, index) => <li key={index}>{item}</li>)}</ul></>}{brief.requirements?.length > 0 && <><strong>本轮整理的验收项</strong><small>由统筹者整理，原始用户要求优先。可在群聊中补充或修改，下一轮会形成新版本。</small><ul>{brief.requirements.map((item) => <li key={item.id}>{item.description}</li>)}</ul></>}<small>版本指纹 {brief.sha256?.slice(0, 12)} · {brief.sources?.length || 0} 条原文来源</small></section>}
        <section aria-label="本轮使用的固定项目记忆">{memory.project_memory?.entries?.length > 0 && <><h3>本轮使用的固定项目记忆</h3>{memory.project_memory.entries.map((item) => <details key={item.id}><summary>{item.name} · 第 {item.revision || 1} 版</summary><p style={{ whiteSpace: 'pre-wrap' }}>{item.content}</p></details>)}</>}</section><ol>{(memory.requests || []).map((item, index) => <li key={item.job_ids?.[0] || index}><strong>{index === 0 ? '原始目标' : '后续修改'}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{item.prompt}</p></li>)}</ol>
        {memory.artifact && <p>当时沿用的底稿：{memory.artifact.title || '未命名成果'} · {memory.artifact.status === 'complete' ? '此前已交付' : '尚未完整完成'}。下一轮仍需重新验收。</p>}
        {memory.baseline_artifact && <p>同时保留修改前的完整稿：{memory.baseline_artifact.title || '未命名成果'}。</p>}
        {documents.length > 0 && <details className="task-memory-files"><summary>历史资料来源</summary><ul>{documents.map((item) => <li key={item.source_job_id}>{(item.names || []).join('、')}{item.truncated && <strong> · 仅保留已读取的部分</strong>}<small>来源任务 {item.source_job_id}</small></li>)}</ul></details>}
        {mediaCount > 0 && <p>素材独立于正文保留；统筹者选择后校验实际文件，不会当作新生成。来源已撤销或文件缺失时会明确报错。</p>}
        {memory.warnings?.length > 0 && <ul className="task-memory-warnings">{memory.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        <small>任务：{memory.task_id} · 上下文指纹：{memory.sha256?.slice(0, 12)}</small>
      </div>
      </dialog></>}
    {previous && <details><summary>最近任务使用的上下文 · {previous.used_turns} 轮</summary>
      <div className="task-context-report">
      <p>仅当前项目、当前群。{previous.context_mode === 'fresh' ? '从新任务边界开始。' : '最新要求优先。'}</p>
      <ul>{(previous.references || []).map((item) => <li key={item.job_id}>{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '历史任务'} · {item.delivery_status === 'partial' || item.delivery_status === 'blocked' || item.user_intent_only ? '未完成要求与进度' : '历史交付'}{item.truncated ? '（长度受限）' : ''}</li>)}</ul>
      {previous.truncated && <p>部分历史受上下文长度限制，完整记录仍可在群内查看。</p>}
      </div>
    </details>}
  </div>;
}
