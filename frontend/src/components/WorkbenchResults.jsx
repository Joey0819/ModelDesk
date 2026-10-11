import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
const active = (job) => ['queued', 'running'].includes(job?.status);
const modelLabel = (model = '') => model.replace(/^openai\//, '');
const asText = (value) => typeof value === 'string' ? value : value?.content || value?.response || '';

export function Icon({ name, size = 20 }) {
  const paths = {
    chat: <><path d="M4 5h16v11H9l-5 4V5Z" /><path d="M8 9h8M8 12h5" /></>,
    council: <><path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Z" /><path d="m8 12 3 3 5-6" /></>,
    dialogue: <><path d="M3 4h13v9H8l-5 3V4Z" /><path d="M9 16v2h7l5 3V10h-3" /></>,
    tasks: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m7 8 1 1 2-2M12 8h5m-10 5 1 1 2-2m2 1h5M7 18h10" /></>,
    group: <><circle cx="12" cy="7" r="3"/><circle cx="4" cy="12" r="2"/><circle cx="20" cy="12" r="2"/><path d="M7 21v-3a5 5 0 0 1 10 0v3M1 20v-3a3 3 0 0 1 4-3m18 6v-3a3 3 0 0 0-4-3"/></>,
    folder: <path d="M3 6h7l2 3h9v11H3V6Z" />,
    code: <><path d="m7 6-5 6 5 6m10-12 5 6-5 6m-3-15-4 18"/></>,
    plus: <path d="M12 5v14M5 12h14" />,
    send: <><path d="m4 4 17 8-17 8 4-8-4-8Z" /><path d="M8 12h13" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
    export: <><path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5" /></>,
    chevron: <path d="m8 10 4 4 4-4" />,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 2M18 17A7 7 0 0 1 6 19l-2-2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.chat}</svg>;
}

export function Markdown({ children }) {
  return <div className="markdown"><ReactMarkdown>{asText(children)}</ReactMarkdown></div>;
}

function ResponsePane({ model, text, error, waiting, index }) {
  return <section className="response-pane">
    <header className="pane-header"><span className={`model-mark model-mark-${index % 3}`}>{model.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><div><strong>{modelLabel(model)}</strong><span>{error ? '调用失败' : waiting ? text ? '正在生成' : '等待回答' : '模型回答'}</span></div>{waiting && <span className="activity-dot" />}</header>
    <div className="pane-body">{error && <div className="output-error">{error}{text && <span> 已收到的部分回答保留如下。</span>}</div>}{text ? <Markdown>{text}</Markdown> : !error && <div className="pane-placeholder">{waiting ? '模型正在组织回答…' : '暂无回答'}</div>}</div>
  </section>;
}

export function StagePending({ text, running }) {
  return <div className="stage-pending">{running ? <span className="small-spinner" /> : <Icon name="clock" size={22} />}<span>{text}</span></div>;
}

function ToolCalls({ job }) {
  const calls = (job.events || []).filter((event) => event.type === 'tool_result');
  if (!calls.length) return null;
  const text = (value) => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);
  return <details className="review-item tool-call-record"><summary>工具调用记录<span>{calls.length} 次</span><Icon name="chevron" size={16} /></summary>{calls.map((call, index) => <section className="tool-call" key={call.seq || index}><header><strong>{call.tool === 'math' ? '数学计算' : call.tool === 'text_stats' ? '文本统计' : call.tool}</strong>{call.model && <span>{call.model}</span>}</header>{call.input != null && <pre><span>输入</span>{text(call.input)}</pre>}{call.output != null && <pre><span>输出</span>{text(call.output)}</pre>}</section>)}</details>;
}

function GroupResult({ job }) {
  const messages = job.result?.messages || [];
  const partialFailure = job.result?.partial_failure || messages.some((message) => message.error || message.incomplete);
  return <div className="group-transcript">{partialFailure && <div className="connection-warning"><strong>部分发言未完整返回</strong><span>失败或达到长度限制的发言已标记，已收到的内容保留。</span></div>}{messages.length ? messages.map((message, index) => <section className="group-message" key={index}><header><span className={`model-mark model-mark-${job.models.indexOf(message.model) % 3}`}>{message.model?.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><strong>{modelLabel(message.model)}</strong><span>第 {message.round} 轮</span></header>{message.error && <div className="output-error">发言失败：{message.error}</div>}{message.content && <Markdown>{message.content}</Markdown>}{message.incomplete && <p className="result-note group-incomplete-note">本条发言达到输出长度限制，内容可能不完整。</p>}</section>) : <StagePending text={active(job) ? '群聊参与者正在阅读话题，准备依次发言' : '没有保存的群聊发言'} running={active(job)} />}</div>;
}

function WorkerPending({ job, text }) {
  if (active(job)) return <StagePending text={text} running />;
  const terminal = { cancelled: '任务已停止', interrupted: '任务已中断', failed: '任务失败', error: '任务失败' };
  const logs = (job.events || []).filter((event) => event.type === 'log' && event.text);
  return <div className="worker-incomplete"><p className="result-note">{terminal[job.status] || '任务已结束'}，运行记录已保存；完整结果未生成。</p><details className="review-item execution-record"><summary>运行记录<span>{logs.length} 条</span><Icon name="chevron" size={16} /></summary>{logs.length ? <pre className="execution-log">{logs.map((event) => event.text).join('\n')}</pre> : <p className="result-note execution-log-empty">任务信息已保存，本次运行尚未产生输出记录。</p>}</details></div>;
}

function CouncilResult({ result, running }) {
  const [tab, setTab] = useState('synthesis');
  const responses = result?.stage1 || [];
  const rankings = result?.stage2 || [];
  const synthesis = result?.stage3;
  const currentStage = synthesis ? 3 : rankings.length ? 2 : responses.length ? 1 : 0;
  return <div className="council-output">
    <div className="stage-navigation" aria-label="评审阶段">{[['responses', '独立回答', 1], ['rankings', '匿名互评', 2], ['synthesis', '主席结论', 3]].map(([id, label, number]) => <button key={id} className={tab === id ? 'selected' : ''} onClick={() => setTab(id)}><span className={currentStage >= number ? 'stage-number done' : 'stage-number'}>{currentStage >= number ? <Icon name="check" size={13} /> : number}</span>{label}</button>)}</div>
    {tab === 'responses' && <div className="comparison-grid">{responses.length ? responses.map((r, i) => <ResponsePane key={r.model} model={r.model} text={r.response} error={r.error} index={i} />) : <StagePending text="等待各模型独立回答" running={running} />}</div>}
    {tab === 'rankings' && <div className="review-output"><p className="result-note">评审时使用匿名标签。展开每个评审模型，查看原始评价与排序。</p>{rankings.length ? rankings.map((r) => <details className="review-item" key={r.model} open={rankings.length === 1}><summary>{modelLabel(r.model)}<span>查看评审</span><Icon name="chevron" size={16} /></summary><Markdown>{r.ranking}</Markdown>{r.parsed_ranking?.length > 0 && <div className="ranking-order">{r.parsed_ranking.map((label, i) => <span key={`${label}-${i}`}>{i + 1}. {result?.metadata?.label_to_model?.[label] || label}</span>)}</div>}</details>) : <StagePending text="独立回答完成后，模型将匿名互评" running={running} />}{result?.metadata?.aggregate_rankings?.length > 0 && <div className="aggregate-ranking"><strong>综合排序</strong>{result.metadata.aggregate_rankings.map((r, i) => <div key={r.model}><span>{i + 1}. {modelLabel(r.model)}</span><span>平均名次 {Number(r.average_rank).toFixed(2)}</span></div>)}</div>}</div>}
    {tab === 'synthesis' && (synthesis ? <section className="final-answer"><header><span className="final-label">综合结论</span><span>主席：{modelLabel(synthesis.model)}</span></header><Markdown>{synthesis.response}</Markdown><p className="result-note">模型间的一致意见仍需外部证据核查。</p></section> : <StagePending text={currentStage === 2 ? '主席正在综合回答与评审结果' : currentStage === 1 ? '已收到独立回答，正在匿名互评' : '完整评审结束后，主席结论将在这里显示'} running={running} />)}
  </div>;
}

export function JobOutput({ job }) {
  const result = job.result;
  if (job.mode === 'chat') {
    const replies = result?.responses || [];
    return <div className="comparison-grid">{job.models.map((model, i) => { const reply = replies.find((r) => r.model === model); return <ResponsePane key={model} model={model} text={reply?.text || reply?.response} error={reply?.error} waiting={active(job) && !reply?.done} index={i} />; })}</div>;
  }
  if (job.mode === 'council') return <CouncilResult result={result} running={active(job)} />;
  if (job.mode === 'group') return <GroupResult job={job} />;
  if (job.mode === 'camel') return <div className="role-transcript">{result?.partial && <p className="result-note">已保留收到的部分角色发言，完整对话尚未完成。</p>}{result?.messages?.length ? result.messages.map((message, index) => { const role = { researcher: '研究员', reviewer: '审稿人' }[message.role] || message.role || '参与角色'; const reviewer = /review|审/.test(message.role || ''); return <section key={index} className={`role-message role-${reviewer ? 1 : 0}`}><div className="role-line"><span className="role-avatar">{role.slice(0, 1)}</span><strong>{role}</strong>{message.round && <span className="message-round">第 {message.round} 轮</span>}{message.model && <span>{modelLabel(message.model)}</span>}</div><Markdown>{message.content}</Markdown></section>; }) : <WorkerPending job={job} text="参与角色正在建立讨论" />}<ToolCalls job={job} /></div>;
  const tasks = result?.tasks || [];
  return <div className="crew-output">{tasks.length > 0 && <div className="task-results">{tasks.map((task, i) => <details className="review-item" key={i}><summary><span className="task-number">{i + 1}</span>{task.name || task.agent || task.description || `协作任务 ${i + 1}`}<Icon name="chevron" size={16} /></summary><Markdown>{task.output || task.result || task.raw || task}</Markdown></details>)}</div>}{result?.answer ? <section className="final-answer"><header><span className="final-label">{result.partial ? '部分协作结果' : '协作结果'}</span><span>{result.partial ? '完整任务流程尚未完成' : '任务流程已完成'}</span></header><Markdown>{result.answer}</Markdown></section> : <WorkerPending job={job} text="团队正在执行任务步骤" />}<ToolCalls job={job} /></div>;
}

