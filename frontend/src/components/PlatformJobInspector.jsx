import { useEffect, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { applyEvent, hydrateJob } from '../workbench-state';
import { Icon, JobOutput, Markdown, ResultProvenance, StagePending } from './WorkbenchResults';
import WorkflowFailureNotice from './WorkflowFailureNotice';
import JobAttachments from './JobAttachments';

const labels = { queued: '等待开始', running: '运行中', complete: '已完成', completed: '已完成', error: '失败', failed: '失败', cancelled: '已停止', interrupted: '已中断' };
const active = (job) => ['queued', 'running'].includes(job?.status);

export default function PlatformJobInspector({ jobId, onClose, onOpenArtifacts }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [canceling, setCanceling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const streamRef = useRef(null);
  useEffect(() => {
    let disposed = false; let source;
    workbenchApi.getJob(jobId).then((saved) => {
      if (disposed) return; setJob(hydrateJob(saved)); setError('');
      if (!active(saved)) return;
      source = workbenchApi.events(saved.id, Math.max(0, ...(saved.events || []).map((event) => event.seq || 0))); streamRef.current = source;
      source.onmessage = (message) => {
        if (disposed) return; let event; try { event = JSON.parse(message.data); } catch { return; }
        setReconnecting(false); setJob((previous) => applyEvent(previous, event));
        if (['complete', 'error', 'cancelled'].includes(event.type)) { source.close(); workbenchApi.getJob(saved.id).then((full) => { if (!disposed) setJob(hydrateJob(full)); }).catch(() => {}); }
      };
      source.onerror = () => { if (!disposed) setReconnecting(true); };
    }).catch((failure) => { if (!disposed) setError(failure.message); });
    return () => { disposed = true; source?.close(); streamRef.current = null; };
  }, [jobId]);
  const stop = async () => { setCanceling(true); try { const stopped = await workbenchApi.cancelJob(job.id); setJob(hydrateJob(stopped)); streamRef.current?.close(); } catch (failure) { setError(failure.message); } finally { setCanceling(false); } };
  const download = (format) => { const anchor = document.createElement('a'); anchor.href = workbenchApi.exportUrl(job.id, format); anchor.download = `ModelDesk-${job.id}.${format}`; anchor.hidden = true; document.body.appendChild(anchor); anchor.click(); anchor.remove(); };
  const logs = (job?.events || []).filter((event) => event.type === 'log' && event.text);

  return <section className="platform-job-inspector">{error && <div className="error-banner" role="alert">{error}</div>}{job ? <><header><div><strong>{job.prompt || '运行记录'}</strong><span>{labels[job.status] || job.status} · {job.mode}</span></div><div>{active(job) && <button className="stop-button" disabled={canceling} onClick={stop}><Icon name="stop" size={14} />{canceling ? '正在停止' : '停止'}</button>}{onOpenArtifacts && <button className="subtle-button" onClick={() => onOpenArtifacts(job)}><Icon name="layers" size={14} />打开成果</button>}<button className="subtle-button" onClick={() => download('md')}>Markdown</button><button className="subtle-button" onClick={() => download('json')}>JSON</button>{onClose && <button className="icon-button" aria-label="关闭运行详情" onClick={onClose}><Icon name="close" size={17} /></button>}</div></header><JobAttachments job={job} /><WorkflowFailureNotice job={job} />{reconnecting && active(job) && <p className="stream-warning">实时连接正在恢复，任务继续在后台执行。</p>}{job.mode === 'sdk_script' ? <div className="sdk-execution-output">{job.result && <section className="sdk-native-result"><strong>原生 SDK 返回结果</strong>{typeof job.result.answer === 'string' ? <Markdown>{job.result.answer}</Markdown> : <pre>{JSON.stringify(job.result, null, 2)}</pre>}</section>}{job.result && <details className="sdk-full-result"><summary>完整 SDK 返回数据</summary><pre>{JSON.stringify(job.result, null, 2)}</pre></details>}<strong>实际运行输出</strong><pre>{job.result?.stdout || job.result?.output || logs.map((event) => event.text).join('\n') || (active(job) ? '等待脚本输出…' : '本次脚本没有输出内容。')}</pre>{job.result?.stderr && <pre className="sdk-stderr">{job.result.stderr}</pre>}{job.result?.exit_code != null && <p>退出码：{job.result.exit_code}</p>}<details><summary>本次实际执行的源码</summary><pre>{job.source}</pre></details></div> : job.mode === 'review' ? <><p className="result-note">项目评审完整上下文与定位信息可在“项目与代码评审”中查看。</p>{(job.result?.findings || []).map((finding, index) => <section className="code-finding" key={index}><header><span className="finding-severity">{finding.severity}</span><h3>{finding.title}</h3></header><p className="finding-location">{finding.file}:{finding.line_start}</p><Markdown>{finding.body}</Markdown></section>)}{job.result?.raw && <JobOutput job={{ ...job, mode: job.engine, result: job.result.raw }} />}</> : <JobOutput job={job} />}<ResultProvenance job={job} />{logs.length > 0 && <details className="review-item execution-record"><summary>运行记录<span>{logs.length} 条</span><Icon name="chevron" size={15} /></summary><pre className="execution-log">{logs.map((event) => event.text).join('\n')}</pre></details>}</> : !error && <StagePending text="正在读取持久化运行记录" running />}</section>;
}
