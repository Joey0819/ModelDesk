import { isValidElement, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { workflowFormatDiagnostics } from '../workflow-failure';
import { deriveArtifacts } from '../artifacts';
import ConversationActions, { CopyTextButton } from './ConversationActions';
const active = (job) => ['queued', 'running'].includes(job?.status);
const modelLabel = (model = '') => model.replace(/^connection:[^:]+:/, '').replace(/^openai\//, '');
const asText = (value) => typeof value === 'string' ? value : value?.content || value?.response || '';

export function Icon({ name, size = 20 }) {
  const paths = {
    phone: <><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M10 5h4M11 19h2" /></>,
    chat: <><path d="M4 5h16v11H9l-5 4V5Z" /><path d="M8 9h8M8 12h5" /></>,
    council: <><path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Z" /><path d="m8 12 3 3 5-6" /></>,
    dialogue: <><path d="M3 4h13v9H8l-5 3V4Z" /><path d="M9 16v2h7l5 3V10h-3" /></>,
    tasks: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m7 8 1 1 2-2M12 8h5m-10 5 1 1 2-2m2 1h5M7 18h10" /></>,
    group: <><circle cx="12" cy="7" r="3"/><circle cx="4" cy="12" r="2"/><circle cx="20" cy="12" r="2"/><path d="M7 21v-3a5 5 0 0 1 10 0v3M1 20v-3a3 3 0 0 1 4-3m18 6v-3a3 3 0 0 0-4-3"/></>,
    folder: <path d="M3 6h7l2 3h9v11H3V6Z" />,
    code: <><path d="m7 6-5 6 5 6m10-12 5 6-5 6m-3-15-4 18"/></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5m-18 5 9 5 9-5"/></>,
    library: <><rect x="3" y="4" width="4" height="16" rx="1"/><rect x="9" y="4" width="4" height="16" rx="1"/><path d="m15 5 4-1 4 15-4 1-4-15Z"/></>,
    memory: <><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/></>,
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    moon: <path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z" />,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>,
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
    file: <><path d="M14 3H5v18h14V8l-5-5Z" /><path d="M14 3v5h5M8 12h8m-8 4h6" /></>,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.chat}</svg>;
}

function MarkdownCodeBlock({ children }) {
  const child = Array.isArray(children) ? children.find(isValidElement) : children;
  const content = isValidElement(child) ? child.props.children : '';
  const code = typeof content === 'string' ? content : Array.isArray(content) ? content.join('') : '';
  const language = isValidElement(child) ? child.props.className?.replace(/^language-/, '') : '';
  return <div className="markdown-code-block"><div className="markdown-code-toolbar"><span>{language || '代码'}</span><CopyTextButton text={code} label="复制代码" /></div><pre>{children}</pre></div>;
}

const markdownComponents = { pre: MarkdownCodeBlock };

export function Markdown({ children }) {
  return <div className="markdown"><ReactMarkdown components={markdownComponents} skipHtml>{asText(children)}</ReactMarkdown></div>;
}

export function ResultProvenance({ job }) {
  const lastStage = (name) => [...(job.events || [])].reverse().find((event) => event.type === 'stage' && event.stage === name)?.data;
  const context = lastStage('context');
  const candidates = job.context_references || context?.references || job.result?.references || [];
  const references = Array.isArray(candidates) ? candidates : [];
  const memory = job.remembered_memory || lastStage('memory_saved');
  const truncated = job.context_truncated || context?.truncated;
  const history = job.context_history || lastStage('conversational_context');
  const hasHistory = Boolean(history && (history.used_turns || history.omitted_count || history.truncated));
  const attachments = job.attachments || lastStage('attachments')?.items || [];
  const manifest = job.attachment_manifest || lastStage('attachments')?.manifest;
  const historicalManifest = job.history_attachment_manifest || lastStage('attachments')?.history_manifest;
  const modes = { chat: '多模型聊天', council: '匿名评审', camel: '角色对话', crewai: '任务协作', group: '模型群聊', camel_workforce: 'Workforce 团队', crewai_flow: '条件流程', sdk_script: '原生 SDK', review: '代码评审' };
  const omissions = { project_scope_changed: '项目范围不同，未引用', not_complete: '任务未完成，未引用', no_complete_deliverable: '没有完整交付', turn_limit: '超过历史轮数限制', context_budget: '超过上下文预算', invalid_history_record: '历史记录无效，未引用' };
  if (!references.length && !memory && !hasHistory && !attachments.length) return null;
  return <div className="job-source-records">{attachments.length > 0 && <details className="attachment-source-record"><summary>实际附件来源（{attachments.length} 项）</summary><p>引用本轮保存的文件副本；项目资料后来移除也不会改变这轮输入。</p>{attachments.map((item) => <article className="provenance-source" key={`${item.id}-${item.source}`}><header><strong title={item.relative_path || item.name}>{item.name}</strong><span>{{ turn: '本轮附件', project_shared: '项目共用', history: '历史图片' }[item.source] || '附件'}</span></header><p>{item.kind === 'image' ? `${item.width || '?'} × ${item.height || '?'} 像素` : '提取的文本'}{item.truncated && ' · 文本超过预算，已截断'}</p><small>内容指纹：{item.content_sha256 || item.sha256 || '未记录'}{item.source_job_id && ` · 最初来源记录：${item.source_job_id}`}</small></article>)}{manifest?.digest && <p className="attachment-manifest-digest">本轮资料指纹：{manifest.digest}</p>}{historicalManifest?.digest && <p className="attachment-manifest-digest">历史图片指纹：{historicalManifest.digest}</p>}{manifest?.coverage && <details><summary>实际读取范围与预算</summary><pre>{JSON.stringify(manifest.coverage, null, 2)}</pre></details>}{historicalManifest?.coverage && <details><summary>历史图片引用范围</summary><pre>{JSON.stringify(historicalManifest.coverage, null, 2)}</pre></details>}</details>}{hasHistory && <details className="conversation-history-record"><summary>对话上下文 · 引用了 {history.used_turns || 0} 轮历史交付{history.omitted_count > 0 && `，跳过 ${history.omitted_count} 条记录`}</summary><p>请求保留 {history.requested_turns} 轮；本次引用 {history.chars || 0} / {history.budget_chars} 字符。{history.truncated && '超过预算的内容已截断。'}</p>{history.references?.length > 0 && <ul>{history.references.map((reference) => <li key={reference.job_id}>{modes[reference.mode] || reference.mode} · {reference.models?.join('、') || '原生框架'}<small>来源记录：{reference.job_id}{reference.created_at && ` · ${new Date(reference.created_at).toLocaleString('zh-CN')}`}</small>{reference.snapshot_id && <small>项目快照：{reference.snapshot_id}{reference.stale_snapshot && '（已发生变化，不代表当前源码）'}</small>}{reference.truncated && <small>这轮历史交付已截断。</small>}{reference.caveats?.map((caveat, index) => <small key={index}>{caveat}</small>)}</li>)}</ul>}{history.omitted?.length > 0 && <ul className="conversation-context-omissions">{history.omitted.map((item, index) => <li key={`${item.job_id}-${index}`}>{omissions[item.reason] || item.reason}<small>{item.job_id}</small></li>)}</ul>}</details>}{(references.length > 0 || memory) && <details className="result-source-record"><summary>来源与记忆记录{references.length > 0 && `（${references.length} 项来源）`}</summary>{truncated && <p className="context-invalid">上下文超过运行预算并被截断，以下引用范围不代表全部内容都已发送。</p>}{references.map((reference, index) => <article className="provenance-source" key={reference.id || index}><header><strong>{typeof reference === 'string' ? reference : reference.name || reference.project_name || (reference.kind === 'project' ? '代码快照' : '引用材料')}</strong><span>{{ knowledge: '知识文档', memory: '记忆', project: '项目代码' }[reference.kind] || '来源'}</span></header>{reference.start_line && <p>原文第 {reference.start_line}–{reference.end_line} 行{reference.chunk && `，片段 ${reference.chunk}`}</p>}{reference.scope && <p>记忆范围：{reference.scope}</p>}{reference.snapshot_id && <p>快照 {reference.snapshot_id} · 内容指纹 {reference.digest}</p>}{reference.coverage?.disclaimer && <p>{reference.coverage.disclaimer}</p>}{reference.content && <details><summary>引用内容</summary><pre>{reference.content}</pre></details>}</article>)}{memory && <p className="remembered-result">本次结果已保存为记忆：{memory.id} · 范围 {memory.scope}</p>}</details>}</div>;
}

function ResponsePane({ model, text, error, waiting, index, conversationView }) {
  if (conversationView) return <section className="conversation-response"><header><span className={`model-mark model-mark-${index % 3}`}>{model.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><div><strong>{modelLabel(model)}</strong><small>{error ? '调用失败' : waiting ? text ? '正在生成' : '等待回答' : '模型回答已保存'}</small></div>{waiting && <span className="activity-dot" />}<CopyTextButton text={text} label="复制回答" /></header>{error && <div className="output-error">{error}{text && ' 已收到的部分回答保留。'}</div>}{text ? <details><summary>{text.replace(/[#*`>]/g, '').slice(0, 180)}{text.length > 180 && '…'}<span>展开完整回答 · 可在成果面板预览</span></summary><Markdown>{text}</Markdown></details> : !error && <p className="result-note">{waiting ? '模型正在组织回答…' : '暂无回答'}</p>}</section>;
  return <section className="response-pane">
    <header className="pane-header"><span className={`model-mark model-mark-${index % 3}`}>{model.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><div><strong>{modelLabel(model)}</strong><span>{error ? '调用失败' : waiting ? text ? '正在生成' : '等待回答' : '模型回答'}</span></div>{waiting && <span className="activity-dot" />}<CopyTextButton text={text} label="复制回答" /></header>
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

function GroupResult({ job, conversationView }) {
  const messages = job.result?.messages || [];
  const partialFailure = job.result?.partial_failure || messages.some((message) => message.error || message.incomplete);
  return <div className="group-transcript">{partialFailure && <div className="connection-warning"><strong>部分发言未完整返回</strong><span>失败或达到长度限制的发言已标记，已收到的内容保留。</span></div>}{messages.length ? messages.map((message, index) => <section className="group-message" key={index}><header><span className={`model-mark model-mark-${job.models.indexOf(message.model) % 3}`}>{message.model?.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><strong>{modelLabel(message.model)}</strong><span>第 {message.round} 轮</span></header>{message.error && <div className="output-error">发言失败：{message.error}</div>}{message.content && (conversationView ? <details className="conversation-message-content"><summary>{message.content.replace(/[#*`>]/g, "").slice(0, 170)}{message.content.length > 170 && "…"}<span>展开完整发言</span></summary><Markdown>{message.content}</Markdown></details> : <Markdown>{message.content}</Markdown>)}<ConversationActions text={message.content} label="复制发言" />{message.incomplete && <p className="result-note group-incomplete-note">本条发言达到输出长度限制，内容可能不完整。</p>}</section>) : <StagePending text={active(job) ? '群聊参与者正在阅读话题，准备依次发言' : '没有保存的群聊发言'} running={active(job)} />}<ToolCalls job={job} /></div>;
}

function WorkflowFormatRecords({ job }) {
  const entries = workflowFormatDiagnostics(job);
  if (!entries.length) return null;
  const phases = { research: '研究阶段', finalization: '不调用工具的结果整理', finalization_error: '结果整理异常' };
  return <details className="review-item format-diagnostic-records"><summary>原生结果整理记录<span>{entries.length} 条</span><Icon name="chevron" size={16} /></summary><p className="format-record-note">保留原始研究输出及整理过程，任务完成状态以实际执行结果为准。</p>{entries.map((entry, index) => <details className="format-diagnostic-entry" key={index}><summary><strong>{phases[entry.phase] || entry.phase}</strong><span>{entry.agent || '原生角色'} · {modelLabel(entry.model)}</span><Icon name="chevron" size={14} /></summary><div className="format-diagnostic-metadata"><span>终止标记：{entry.finish_reasons.join('、') || '未记录'}</span>{entry.tool_calls != null && <span>工具调用：{entry.tool_calls} 次</span>}{typeof entry.parsed === 'boolean' && <span>{entry.parsed ? '格式校验通过' : '未获得有效格式'}</span>}{(entry.status_code ?? entry.http_status) != null && <span>HTTP 状态：{entry.status_code ?? entry.http_status}</span>}</div>{entry.preview_truncated && <p className="format-record-note">本条记录来自截断的实时预览，任务结束后会读取已保存的原始记录。</p>}{entry.raw_truncated && <p className="format-record-note">原始输出超出保存长度，记录中的内容已截断。</p>}{entry.evidence_truncated && <p className="format-record-note">用于结果整理的证据超过预算，引用内容已截断。</p>}{entry.error && <p className="output-error">{entry.error}</p>}<pre className="execution-log">{entry.raw || '本阶段没有返回文本输出。'}</pre></details>)}</details>;
}

function WorkflowResult({ job, conversationView }) {
  const result = job.result || {};
  const tasks = result.tasks || [];
  const routes = result.routes || [];
  const artifact = deriveArtifacts(job)[0];
  return <div className="crew-output">{routes.length > 0 && <section className="actual-flow-routes"><header><strong>实际分支决策</strong><span>根据此前步骤的真实输出决定</span></header>{routes.map((route, index) => <div key={index}><strong>{route.task}</strong><span className={route.decision === 'skip' ? 'route-skipped' : 'route-run'}>{route.decision === 'skip' ? '跳过' : '执行'}</span><small>{route.reason || (route.condition ? JSON.stringify(route.condition) : '')}</small></div>)}</section>}{tasks.length > 0 && <div className="task-results">{tasks.map((task, index) => <details className={`review-item ${task.status === 'skipped' ? 'task-skipped' : ''}`} key={task.id || index}><summary><span className="task-number">{index + 1}</span>{task.description?.slice(0, 90) || task.name || task.agent || `协作任务 ${index + 1}`}<span className={`task-execution-status task-status-${task.status || "pending"}`}>{{ skipped: "已跳过", complete: "已完成", failed: "失败", interrupted: "已中断", not_started: "未开始", running: "运行中", pending: "等待执行" }[task.status] || ""}</span><CopyTextButton text={task.output || task.result || task.raw || ""} label="复制输出" /><Icon name="chevron" size={16} /></summary>{task.status === "failed" && <><p className="failed-task-output-label">原生子任务的失败输出 · 本项要求尚未完成</p>{task.description && <p className="task-full-description">{task.description}</p>}</>}{task.status === 'skipped' ? <p className="result-note skipped-task-reason">{task.reason || '本步骤按运行路由跳过，未执行任务。'}</p> : <Markdown>{task.output || task.result || task.raw || ''}</Markdown>}{(task.agent || task.model) && <p className="task-provenance">执行角色：{task.agent || '原生框架分配'}{task.model && ` · 模型：${task.model}`}</p>}{task.condition && <pre className="task-condition-record">{JSON.stringify(task.condition, null, 2)}</pre>}</details>)}</div>}{conversationView && artifact ? <div className="conversation-artifact-note"><Icon name="file" size={17} /><span>{artifact.status === "complete" ? "交付内容已保存，可在成果面板查看。" : "已收到的内容保留在成果面板，任务尚未完整完成。"}</span><CopyTextButton text={artifact.content} label="复制内容" /></div> : result.answer ? <section className="final-answer"><header><span className="final-label">{result.partial ? "部分协作结果" : result.process === "workforce" ? "工作队结果" : result.process === "flow" ? "流程结果" : "协作结果"}</span><span>{result.partial ? "完整任务流程尚未完成" : "任务流程已完成"}</span></header><Markdown>{result.answer}</Markdown><ConversationActions text={result.answer} label="复制交付内容" /></section> : <WorkerPending job={job} text="原生团队正在执行任务步骤" />}{result.workforce_events?.length > 0 && <details className="review-item"><summary>原生 Workforce 事件<span>{result.workforce_events.length} 条</span><Icon name="chevron" size={16} /></summary><pre className="execution-log">{JSON.stringify(result.workforce_events, null, 2)}</pre></details>}<ToolCalls job={job} /><WorkflowFormatRecords job={job} />{conversationView && artifact && <ExecutionRecord job={job} />}</div>;
}

function ExecutionRecord({ job }) {
  const logs = (job.events || []).filter((event) => event.type === 'log' && event.text);
  if (!logs.length) return null;
  return <details className="review-item execution-record"><summary>运行记录<span>{logs.length} 条</span><CopyTextButton text={logs.map((event) => event.text).join('\n')} label="复制记录" /><Icon name="chevron" size={16} /></summary><pre className="execution-log">{logs.map((event) => event.text).join('\n')}</pre></details>;
}

function WorkerPending({ job, text }) {
  if (active(job)) return <StagePending text={text} running />;
  const terminal = { cancelled: '任务已停止', interrupted: '任务已中断', failed: '任务失败', error: '任务失败' };
  return <div className="worker-incomplete"><p className="result-note">{terminal[job.status] || '任务已结束'}，运行记录已保存；完整结果未生成。</p><ExecutionRecord job={job} /></div>;
}

function CouncilResult({ result, running, conversationView }) {
  const [tab, setTab] = useState(conversationView ? 'responses' : 'synthesis');
  const responses = result?.stage1 || [];
  const rankings = result?.stage2 || [];
  const synthesis = result?.stage3;
  const currentStage = synthesis ? 3 : rankings.length ? 2 : responses.length ? 1 : 0;
  return <div className="council-output">
    <div className="stage-navigation" aria-label="评审阶段">{[['responses', '独立回答', 1], ['rankings', '匿名互评', 2], ['synthesis', '主席结论', 3]].map(([id, label, number]) => <button key={id} className={tab === id ? 'selected' : ''} onClick={() => setTab(id)}><span className={currentStage >= number ? 'stage-number done' : 'stage-number'}>{currentStage >= number ? <Icon name="check" size={13} /> : number}</span>{label}</button>)}</div>
    {tab === 'responses' && <div className={conversationView ? "model-conversation-output" : "comparison-grid"}>{responses.length ? responses.map((r, i) => <ResponsePane key={r.model} model={r.model} text={r.response} error={r.error} index={i} conversationView={conversationView} />) : <StagePending text="等待各模型独立回答" running={running} />}</div>}
    {tab === 'rankings' && <div className="review-output"><p className="result-note">评审时使用匿名标签。展开每个评审模型，查看原始评价与排序。</p>{rankings.length ? rankings.map((r) => <details className="review-item" key={r.model} open={rankings.length === 1}><summary>{modelLabel(r.model)}<span>查看评审</span><Icon name="chevron" size={16} /></summary><Markdown>{r.ranking}</Markdown><ConversationActions text={r.ranking} label="复制评审" />{r.parsed_ranking?.length > 0 && <div className="ranking-order">{r.parsed_ranking.map((label, i) => <span key={`${label}-${i}`}>{i + 1}. {result?.metadata?.label_to_model?.[label] || label}</span>)}</div>}</details>) : <StagePending text="独立回答完成后，模型将匿名互评" running={running} />}{result?.metadata?.aggregate_rankings?.length > 0 && <div className="aggregate-ranking"><strong>综合排序</strong>{result.metadata.aggregate_rankings.map((r, i) => <div key={r.model}><span>{i + 1}. {modelLabel(r.model)}</span><span>平均名次 {Number(r.average_rank).toFixed(2)}</span></div>)}</div>}</div>}
    {tab === 'synthesis' && (synthesis && conversationView ? <div className="conversation-artifact-note"><Icon name="file" size={17} /><span>主席结论已保存在成果面板。</span><CopyTextButton text={synthesis.response} label="复制结论" /></div> : synthesis ? <section className="final-answer"><header><span className="final-label">综合结论</span><span>主席：{modelLabel(synthesis.model)}</span></header><Markdown>{synthesis.response}</Markdown><ConversationActions text={synthesis.response} label="复制结论" /><p className="result-note">模型间的一致意见仍需外部证据核查。</p></section> : <StagePending text={currentStage === 2 ? '主席正在综合回答与评审结果' : currentStage === 1 ? '已收到独立回答，正在匿名互评' : '完整评审结束后，主席结论将在这里显示'} running={running} />)}
  </div>;
}

export function JobOutput({ job, conversationView = false }) {
  const result = job.result;
  if (job.mode === 'chat') {
    const replies = result?.responses || [];
    return <><div className={conversationView ? "model-conversation-output" : "comparison-grid"}>{job.models.map((model, i) => { const reply = replies.find((r) => r.model === model); return <ResponsePane key={model} model={model} text={reply?.text || reply?.response} error={reply?.error} waiting={active(job) && !reply?.done} index={i} conversationView={conversationView} />; })}</div><ToolCalls job={job} /></>;
  }
  if (job.mode === 'council') return <CouncilResult result={result} running={active(job)} conversationView={conversationView} />;
  if (job.mode === 'group') return <GroupResult job={job} conversationView={conversationView} />;
  if (job.mode === 'camel') return <div className="role-transcript">{result?.partial && <p className="result-note">已保留收到的部分角色发言，完整对话尚未完成。</p>}{result?.messages?.length ? result.messages.map((message, index) => { const role = { researcher: '研究员', reviewer: '审稿人' }[message.role] || message.role || '参与角色'; const reviewer = /review|审/.test(message.role || ''); return <section key={index} className={`role-message role-${reviewer ? 1 : 0}`}><div className="role-line"><span className="role-avatar">{role.slice(0, 1)}</span><strong>{role}</strong>{message.round && <span className="message-round">第 {message.round} 轮</span>}{message.model && <span>{modelLabel(message.model)}</span>}</div><ConversationActions text={message.content} label="复制发言" />{conversationView ? <details className="conversation-message-content"><summary>{message.content.replace(/[#*`>]/g, "").slice(0, 170)}{message.content.length > 170 && "…"}<span>展开完整发言</span></summary><Markdown>{message.content}</Markdown></details> : <Markdown>{message.content}</Markdown>}</section>; }) : <WorkerPending job={job} text="参与角色正在建立讨论" />}<ToolCalls job={job} /></div>;
  return <WorkflowResult job={job} conversationView={conversationView} />;
}

