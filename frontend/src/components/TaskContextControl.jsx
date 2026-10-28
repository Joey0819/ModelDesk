import { useRef } from 'react';

export default function TaskContextControl({ value, onChange, disabled, jobs = [] }) {
  const memoryDialog = useRef(null);
  const latest = jobs.at(-1);
  const previous = latest?.context_history;
  const memory = latest?.task_context;
  return <div className="task-context-control">
    <label>本轮上下文 <select aria-label="本轮上下文" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      <option value="continue">继续本群任务</option>
      <option value="fresh">开始新任务，不带入旧对话</option>
    </select></label>
    <small>{value === 'fresh' ? '保留旧记录；本轮从新目标开始。已选项目资料仍有效。' : '沿用原始目标、历次修改和最新底稿；失败不会清空，所有执行成员共享。'}</small>
    {memory && <><button type="button" className="task-memory-button" onClick={() => memoryDialog.current?.showModal()}>任务记忆 · {memory.requests?.length || 0} 条要求{memory.artifact ? ' · 已保留底稿' : ''}</button>
      <dialog ref={memoryDialog} className="task-memory-dialog" aria-label="任务记忆">
        <header><strong>本任务记忆</strong><button type="button" onClick={() => memoryDialog.current?.close()} aria-label="关闭任务记忆">关闭</button></header>
      <div className="task-memory-body">
        <p>当前项目与群聊内的任务第 {memory.revision} 轮。后续修改覆盖冲突的旧要求；开始新任务会隔离这些记忆。</p>
        <ol>{(memory.requests || []).map((item, index) => <li key={item.job_ids?.[0] || index}><strong>{index === 0 ? '原始目标' : '后续修改'}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{item.prompt}</p></li>)}</ol>
        {memory.artifact && <p>沿用底稿：{memory.artifact.title || '未命名成果'} · {memory.artifact.status === 'complete' ? '此前已交付' : '尚未完整完成'}。本轮仍需重新验收。</p>}
        {memory.baseline_artifact && <p>同时保留修改前的完整稿：{memory.baseline_artifact.title || '未命名成果'}。</p>}
        {memory.reference_documents?.length > 0 && <p>已保留附件副本：{memory.reference_documents.flatMap((item) => item.names || []).join('、')}。</p>}
        {memory.reference_media?.length > 0 && <p>可复用素材 {memory.reference_media.length} 份；统筹者选择后校验实际文件，不会当作新生成。</p>}
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
