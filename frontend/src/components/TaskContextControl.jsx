export default function TaskContextControl({ value, onChange, disabled, jobs = [] }) {
  const latest = jobs.at(-1);
  const previous = latest?.context_history;
  return <div className="task-context-control">
    <label>本轮上下文 <select aria-label="本轮上下文" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      <option value="continue">继续本群任务</option>
      <option value="fresh">开始新任务，不带入旧对话</option>
    </select></label>
    <small>{value === 'fresh' ? '保留旧记录；本轮从新目标开始。已选项目资料仍有效。' : '保留近期用户要求与未完成项；失败不会清空任务记忆。'}</small>
    {previous && <details><summary>最近任务使用的上下文 · {previous.used_turns} 轮</summary>
      <div className="task-context-report">
      <p>仅当前项目、当前群。{previous.context_mode === 'fresh' ? '从新任务边界开始。' : '最新要求优先。'}</p>
      <ul>{(previous.references || []).map((item) => <li key={item.job_id}>{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '历史任务'} · {item.delivery_status === 'partial' || item.delivery_status === 'blocked' || item.user_intent_only ? '未完成要求与进度' : '历史交付'}{item.truncated ? '（长度受限）' : ''}</li>)}</ul>
      {previous.truncated && <p>部分历史受上下文长度限制，完整记录仍可在群内查看。</p>}
      </div>
    </details>}
  </div>;
}
