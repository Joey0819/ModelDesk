import { useState } from 'react';
import { API_BASE, apiFetch } from '../api-base';
import { coordinationState } from '../coordination-state';
import { Markdown } from './WorkbenchResults';
import './CoordinationProgress.css';

const statuses = { pending: '等待前置工作', running: '执行中', complete: '已完成', error: '失败', blocked: '等待处理', cancelled: '已停止' };
const versionComponents = { img: ({ alt }) => <em>〔{alt || '媒体'}：请在当前成果中查看实际文件〕</em> };

export default function CoordinationProgress({ job }) {
  const [busyRevision, setBusyRevision] = useState(null);
  const [error, setError] = useState('');
  const state = coordinationState(job);
  if (!state) return null;
  const busy = busyRevision === state.workflow.revision;
  const continueTask = async () => {
    setBusyRevision(state.workflow.revision); setError('');
    try {
      const query = job.project_id ? `?project_id=${encodeURIComponent(job.project_id)}` : '';
      const response = await apiFetch(`${API_BASE}/api/groups/${encodeURIComponent(job.group_room_id)}/messages/${encodeURIComponent(job.id)}/continue${query}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_revision: state.workflow.revision }),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '暂时无法继续，请刷新任务查看原因');
      globalThis.dispatchEvent?.(new CustomEvent('modeldesk-coordination-continued', { detail: { roomId: job.group_room_id, projectId: job.project_id, jobId: job.id } }));
    } catch (failure) { setError(failure.message); setBusyRevision(null); }
  };
  return <section className="coordination-progress" aria-label="任务要求与交付检查">
    <header><strong>{state.reviewLabel}</strong><span>{state.steps.filter((step) => step.status === 'complete').length}/{state.steps.length} 项工作完成</span></header>
    {state.requirements.length > 0 && <details open={state.requirements.some((item) => item.state === 'missing')}><summary>验收要求（{state.requirements.filter((item) => item.state === 'met').length}/{state.requirements.length}）</summary><ul className="coordination-checks">{state.requirements.map((item) => <li key={item.id} data-state={item.state}>
      <span aria-hidden="true">{item.state === 'met' ? '✓' : item.state === 'missing' ? '!' : '○'}</span><div><strong>{item.description}</strong><small>{item.state === 'met' ? '已有交付依据' : item.state === 'missing' ? '尚未满足' : '等待检查'}{item.reason && `：${item.reason}`}</small></div>
    </li>)}</ul></details>}
    {state.steps.length > 0 && <details><summary>查看分工与依赖</summary><ol>{state.steps.map((step) => <li key={step.id}><strong>{step.member_name}：{step.expected_output || step.instruction}</strong><small>{statuses[step.status] || '状态待同步'}{step.depends_on?.length > 0 && `，依赖：${step.depends_on.map((id) => state.steps.find((item) => item.id === id)?.member_name || id).join('、')}`}{step.attempts?.length > 1 && `，第 ${step.attempts.length} 次尝试`}</small></li>)}</ol></details>}
    {state.versions.length > 1 && <details><summary>成果版本（{state.versions.length}）</summary>{state.versions.map((version) => <details key={version.number}><summary>第 {version.number} 版：{version.reason}</summary><Markdown components={versionComponents}>{version.content}</Markdown></details>)}</details>}
    {job.result.delivery.visual_evidence?.assets?.length > 0 && <details><summary>图片与视频检查依据</summary><ul>{job.result.delivery.visual_evidence.assets.map((asset) => <li key={asset.asset_id}><strong>{asset.kind === 'video' ? '视频' : '图片'}：{asset.status === 'sampled' ? `${asset.width} × ${asset.height}${asset.duration_seconds ? `，${asset.duration_seconds} 秒` : ''}` : '画面尚未取得'}</strong><small>{asset.samples?.length ? `提供 ${asset.samples.length} 处画面${asset.kind === 'video' ? `（${asset.samples.map((item) => `${item.timestamp_seconds}s`).join('、')}）` : ''}` : asset.reason}</small></li>)}</ul><p className="coordination-review-note">视频仅抽取画面，不包含音轨或全片连续动作检查。</p></details>}
    {job.result.delivery.validation?.review && <p className="coordination-review-note">{job.result.delivery.validation.review.independent_model ? '另一模型已审阅正文与执行记录。' : '统筹模型已自检正文与执行记录。'}{job.result.delivery.validation.review.visual_input_count > 0 ? `同时检查了 ${job.result.delivery.validation.review.visual_input_count} 处实际画面。` : '本次未审阅媒体画面。'}外部事实仍需可核查来源。</p>}
    {state.canContinue && <div className="coordination-continue"><button type="button" disabled={busy} onClick={continueTask}>{busy ? '正在继续…' : '继续未完成的工作'}</button><small>保留完成步骤和文件；不重发状态未确认的任务，已确认的 Muse 创作最多自动修订一次。</small></div>}
    {error && <p role="alert" className="output-error">{error}</p>}
  </section>;
}
