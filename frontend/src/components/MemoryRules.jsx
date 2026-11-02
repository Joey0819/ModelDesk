export default function MemoryRules({ draft, update, disabled = false }) {
  const versions = draft.versions || [];
  return <section className="memory-rules" aria-label="项目记忆使用规则">
    <label className="platform-field"><span>记忆用途</span>
      <select value={draft.memory_type || 'reference'} disabled={disabled} onChange={(event) => update({ memory_type: event.target.value, pinned: event.target.value === 'reference' ? false : draft.pinned })}>
        <option value="reference">参考资料 · 选择后使用</option>
        <option value="constraint">项目约束 · 例如语言、受众与交付格式</option>
        <option value="decision">已确认决策 · 例如选定方案与原因</option>
      </select>
    </label>
    <label className="memory-pin"><input type="checkbox" checked={Boolean(draft.pinned)} disabled={disabled || !draft.project_id || (draft.memory_type || 'reference') === 'reference'} onChange={(event) => update({ pinned: event.target.checked })} />固定到项目：每次派发任务自动完整带入</label>
    <p>固定记忆只用于所属项目，包括新任务。修改从下一次派发起生效；正在执行的任务保留当时版本。本轮明确修改优先，不会自动增加 Agent 权限。最多 20 条、合计 8000 字符。</p>
    {!draft.project_id && <p>选择“当前项目”作为保存位置后，可以固定项目约束与决策。</p>}
    {versions.length > 0 && <details className="memory-version-history"><summary>版本历史 · 当前第 {draft.revision || 1} 版</summary>
      {versions.slice().reverse().map((version, index) => <article key={`${version.revision}-${index}`}>
        <header><strong>第 {version.revision || 1} 版 · {version.name}</strong><time>{version.updated_at && new Date(version.updated_at).toLocaleString()}</time></header>
        <pre>{version.content}</pre>
        <small>{version.archived ? '已归档' : version.pinned ? '固定记忆' : '手动选择使用'}</small>
        <button type="button" disabled={disabled || version.revision === draft.revision} onClick={() => update({ content: version.content, name: version.name, memory_type: version.memory_type || 'reference', pinned: Boolean(version.pinned && draft.project_id) })}>恢复此内容到编辑区</button>
      </article>)}
    </details>}
  </section>;
}
