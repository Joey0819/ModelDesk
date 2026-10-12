import { safeDiagnostic, workflowFailure } from '../workflow-failure';
import { Icon } from './WorkbenchResults';

export default function WorkflowFailureNotice({ job, onPrepare }) {
  const failure = workflowFailure(job);
  if (!failure) return job?.error ? <div className="output-error" role="alert">{safeDiagnostic(job.error)}</div> : null;
  return <section className="workflow-failure" role="alert" aria-label="协作任务失败原因">
    <header><div><strong>{failure.title}</strong><span>{failure.role} · {failure.source}</span></div>{onPrepare && <button type="button" className="subtle-button" onClick={() => onPrepare(failure.category)}><Icon name="settings" size={15} />{failure.category === "source_unverified" ? "填回任务并补充材料" : "填回任务并检查配置"}</button>}</header>
    <p>{failure.summary}</p>
    {failure.evidence && <blockquote>{failure.evidence}</blockquote>}
    <p className="failure-next-step">{failure.suggestion}</p>
    <div className="failure-tool-summary"><strong>本次实际角色工具</strong>{failure.tools.map((agent) => <span key={agent.id}>{agent.role}：{agent.labels.join('、') || '未启用工具'}</span>)}</div>
    {failure.description && <details className="failure-task-description"><summary>实际失败任务{failure.taskId && <small>{failure.taskId}</small>}</summary><p>{failure.description}</p></details>}
    <details className="failure-raw-details"><summary>原始失败说明与技术错误</summary><pre>{failure.reason}</pre>{failure.technical && failure.technical !== failure.reason && <p>{failure.technical}</p>}</details>
    {onPrepare && <small className="failure-prepare-note">填回仅供编辑，不会自动更改模型、启用工具或提交任务。</small>}
  </section>;
}
