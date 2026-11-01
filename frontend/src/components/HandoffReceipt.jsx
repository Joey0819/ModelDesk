/** A receipt describes supplied input, never a claim that a model understood it. */
export default function HandoffReceipt({ step }) {
  const receipt = step.input_context;
  if (!receipt) return null;
  const sources = Array.isArray(receipt.sources) ? receipt.sources : [];
  const assets = Array.isArray(receipt.reference_asset_ids) ? receipt.reference_asset_ids : [];
  return <details className="handoff-receipt">
    <summary>交接上下文 · {receipt.user_request_count || 0} 条要求 · {sources.length} 份上游成果{assets.length > 0 ? ` · ${assets.length} 份素材引用` : ''}</summary>
    <p>这是本步骤已准备的输入记录；执行状态与成果验收另行显示。上游原稿会沿依赖链传递，审核意见不会替换原稿。</p>
    {sources.length > 0 ? <ul>{sources.map((source) => <li key={source.step_id}>
      <strong>{source.member_name || source.step_id}</strong> · {source.direct ? '直接前置' : '沿依赖链保留'} · {source.characters || 0} 字符
      {source.output_sha256 && <code title={source.output_sha256}>{source.output_sha256.slice(0, 12)}</code>}
    </li>)}</ul> : <p>此步骤没有前置成果，使用当前任务要求与已选资料。</p>}
    {receipt.image_input_count > 0 && <p>模型输入已附 {receipt.image_input_count} 张图片，其中 {receipt.image_input_asset_ids?.length || 0} 张来自已校验的上游成果。</p>}
    {receipt.native_reference_asset_ids?.length > 0 && <p>已为原生 Agent 准备 {receipt.native_reference_asset_ids.length} 张参考图；接收状态以原生回执为准。</p>}
    {assets.length > 0 && !receipt.image_input_count && !receipt.native_reference_asset_ids?.length && <p>此步骤使用素材清单引用，未将这些素材作为图片输入。</p>}
    {step.handoff?.background_compacted && <p>讨论背景已压缩；用户要求与必要前置稿原文保留。</p>}
    {receipt.context_sha256 && <small>任务记忆指纹 <code title={receipt.context_sha256}>{receipt.context_sha256.slice(0, 12)}</code></small>}
  </details>;
}
