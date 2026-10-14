const complete = (job) => ['complete', 'completed'].includes(job?.status);
const active = (job) => ['queued', 'running'].includes(job?.status);
const text = (value) => typeof value === 'string' ? value : '';
const output = (task) => text(task?.output) || text(task?.result) || text(task?.raw);
const taskComplete = (task) => ['complete', 'completed'].includes(task?.status);
const usefulMessage = (message) => text(message?.content).trim() && !/任务规划|任务细化|模型传输重试|原生任务结果|原生结果整理/.test(message.role || '');

function makeArtifact(job, id, title, content, extra = {}) {
  if (!text(content).trim()) return null;
  const partial = !complete(job) || Boolean(job.result?.partial || job.result?.partial_failure || extra.partial);
  const html = /^\s*(?:<!doctype\s+html|<html\b|<script\b|<style\b|<iframe\b|<svg\b)/i.test(content);
  const { partial: _partial, ...metadata } = extra;
  return {
    id, title, content,
    status: active(job) ? 'live' : partial ? 'partial' : 'complete',
    kind: 'deliverable', format: html ? 'code' : 'markdown',
    ...(html ? { language: 'html', note: 'HTML 以源码预览，不在页面中执行。' } : {}),
    source: '模型输出', ...metadata,
  };
}

function lastMessages(job, mode) {
  const messages = job.result?.messages || [];
  const latest = new Map();
  messages.forEach((message, index) => {
    if (!usefulMessage(message)) return;
    const key = mode === 'group' ? message.member_id || message.model : message.role || message.model;
    latest.set(key, { message, index });
  });
  return [...latest.values()].sort((a, b) => b.index - a.index).map(({ message, index }) => {
    const role = { researcher: '研究员', reviewer: '审稿人' }[message.role] || message.role;
    const laterFailed = messages.slice(index + 1).some((next) => (mode === 'group' ? (next.member_id || next.model) === (message.member_id || message.model) : next.model === message.model) && (next.error || next.incomplete));
    return makeArtifact(job, `${mode}:${index}`, mode === 'group' ? `最后发言 · ${message.member_name || message.model || '参与模型'}` : `最后发言 · ${role || '参与角色'}`, message.content, {
      kind: 'discussion', source: mode === 'group' ? `群聊第 ${message.round || 1} 轮` : `角色对话${message.round ? `第 ${message.round} 轮` : ''}`,
      model: message.model, role, partial: Boolean(message.error || message.incomplete || laterFailed),
      note: '这是角色的实际发言，未另行生成综合结论。',
    });
  }).filter(Boolean);
}

function nativeEvidence(job) {
  const events = job.events || [];
  const failures = new Map();
  events.forEach((event, index) => {
    const native = event.native_event || (event.type === 'native_event' ? event : null);
    if (native?.event_type !== 'task_failed' || !text(native.error_message).trim()) return;
    failures.set(native.task_id || event.seq || index, { native, event, index });
  });
  return [...failures.entries()].map(([id, { native }]) => makeArtifact(job, `failure:${id}`, '失败子任务的证据记录', native.error_message, {
    partial: true, kind: 'evidence', task_id: native.task_id, source: '原生 task_failed 事件',
    note: '任务未完成；此内容保留查证结果、限制与证据缺口。',
  })).filter(Boolean);
}

function rawEvidence(job) {
  const stored = job.result?.format_diagnostics;
  const diagnostics = Array.isArray(stored) && stored.length ? stored : (job.events || []).filter((event) => ['research', 'finalization', 'finalization_error'].includes(event.phase)).map((event) => ({ ...event, raw: event.raw ?? event.content }));
  return diagnostics.map((entry, index) => makeArtifact(job, `format:${index}`, `原始输出 · ${entry.phase === 'research' ? '研究阶段' : '结果整理'}`, text(entry.raw), {
    partial: true, kind: 'evidence', format: 'text', model: entry.model, role: entry.agent,
    source: '原生结果整理记录', note: '未形成有效交付结果；原始输出仅作为运行依据。',
  })).filter(Boolean);
}

function reviewMarkdown(result) {
  const findings = result.findings || [];
  const sections = findings.map((finding) => `### ${finding.severity || ''} ${finding.title || '代码发现'}\n\n${finding.file}:${finding.line_start}${finding.line_end && finding.line_end !== finding.line_start ? `–${finding.line_end}` : ''}\n\n${finding.body || ''}${finding.evidence ? `\n\n证据：\n\n\`\`\`\n${finding.evidence}\n\`\`\`` : ''}${finding.suggestion ? `\n\n建议：${finding.suggestion}` : ''}`);
  if (!findings.length) sections.push('本次未产生已验证的代码发现。');
  if (result.unconfirmed?.length) sections.push(`### 尚未确认的主张\n\n${result.unconfirmed.map((item) => `- ${item.model || '模型'}：${item.reason || ''}${item.claim ? ` · ${item.claim}` : ''}`).join('\n')}`);
  if (result.coverage?.disclaimer) sections.push(`覆盖范围：${result.coverage.disclaimer}`);
  return sections.join('\n\n');
}

/** Derive previews only from actual model/task output; never fabricate a completed deliverable. */
export function deriveArtifacts(job) {
  if (!job) return [];
  const result = job.result || {};
  if (job.mode === 'muse_task') return [
    makeArtifact(job, 'muse:deliverable', job.video_url ? 'Muse 视频成片与分镜方案' : 'Muse 任务成果', text(job.response) || text(job.prompt), {
      source: job.harness_generated ? 'Muse Harness 视频与创意引擎' : 'Muse 本机桥接',
      video_url: job.video_url || null,
      note: job.verification || '由 Muse 完成并回传至 ModelDesk 工作台。',
    }),
  ].filter(Boolean);
  if (job.mode === 'group_agent') return [
    makeArtifact(job, 'group-agent:output', job.kind === 'review' ? '独立 Agent 审阅意见' : '独立 Agent 执行结果', text(job.output), { source: `${job.member?.name || '成员'} / ${job.engine === 'member_model' ? job.member?.model || '成员模型' : job.engine === 'claude' ? 'Claude Code' : 'Codex'}`, note: '这是执行引擎的实际返回；结论与测试说明需结合运行记录核查。代码合入需另行审批。' }),
    makeArtifact(job, 'group-agent:patch', '待审阅的实际补丁', text(job.diff), { source: `基准 ${job.base_sha?.slice(0, 12) || '未记录'} / 补丁 ${job.diff_sha256?.slice(0, 12) || '未记录'}`, format: 'code', language: 'diff', kind: 'evidence', note: '补丁保存在独立任务工作区。是否已经合入以审批卡片记录为准。' }),
  ].filter(Boolean);
  if (job.mode === 'chat') return (result.responses || []).map((response) => makeArtifact(job, `chat:${response.model}`, response.model || '模型回答', text(response.text) || text(response.response), {
    model: response.model, source: '独立模型回答', partial: Boolean(response.error || response.incomplete),
    ...(response.error ? { kind: 'evidence', note: `调用未完整返回：${response.error}` } : {}),
  })).filter(Boolean);
  if (job.mode === 'council') {
    const synthesis = makeArtifact(job, 'council:synthesis', '主席综合结论', text(result.stage3?.response), { model: result.stage3?.model, source: 'LLM Council 第三阶段' });
    if (synthesis) return [synthesis];
    return (result.stage1 || []).map((response) => makeArtifact(job, `council:response:${response.model}`, `独立回答 · ${response.model}`, text(response.response), { model: response.model, source: 'LLM Council 第一阶段', kind: 'evidence', partial: true, note: '评审尚未形成主席综合结论。' })).filter(Boolean);
  }
  if (['camel', 'group'].includes(job.mode)) return lastMessages(job, job.mode);
  if (job.mode === 'sdk_script') {
    const answer = makeArtifact(job, 'sdk:answer', 'SDK 返回内容', text(result.answer), { source: `${job.sdk || result.sdk || ''} 原生 SDK 返回` });
    if (answer) return [answer];
    if (!job.result) return [];
    return [makeArtifact(job, 'sdk:result', 'SDK 返回数据', JSON.stringify(result, null, 2), { format: 'code', language: 'json', source: '原生 SDK 实际返回' })];
  }
  if (job.mode === 'review') return job.result ? [makeArtifact(job, 'review:findings', '代码评审报告', reviewMarkdown(result), { source: '已保存的评审发现与覆盖范围' })] : [];
  const tasks = (result.tasks || []).map((task, index) => ({ task, index })).filter(({ task }) => output(task).trim() && task.status !== 'skipped');
  const taskArtifact = ({ task, index }) => makeArtifact(job, `task:${task.id || index}`, task.title || task.description?.split('\n')[0]?.slice(0, 54) || task.name || `任务 ${index + 1} 输出`, output(task), {
    task_id: task.id, role: task.agent, model: task.model, source: taskComplete(task) ? '已完成的原生子任务' : '未完成的原生子任务',
    kind: taskComplete(task) ? 'deliverable' : 'evidence', partial: !taskComplete(task),
    ...(!taskComplete(task) ? { note: '整体任务尚未完成，保留该子任务收到的实际内容。' } : {}),
  });
  const answer = makeArtifact(job, 'workflow:answer', job.mode === 'camel_workforce' ? '原生工作队汇总' : job.mode === 'crewai_flow' ? '流程交付结果' : '协作交付结果', text(result.answer), { source: '原生框架实际返回' });
  if (job.mode === 'camel_workforce' && tasks.length) {
    const finished = tasks.filter(({ task }) => taskComplete(task));
    const deliverable = [...finished].reverse().find(({ task }) => /最终|交付|整合|定稿|审稿|完整文章/.test(`${task.description || ''} ${task.title || ''}`)) || finished.at(-1);
    const ordered = [...(deliverable ? [deliverable] : []), ...tasks.filter((entry) => entry !== deliverable).reverse()];
    const artifacts = ordered.map(taskArtifact).filter(Boolean);
    if (answer && !artifacts.some((artifact) => artifact.content === answer.content)) artifacts.push({ ...answer, note: '这是框架汇总记录，可能包含多个任务的输出。' });
    return artifacts;
  }
  if (answer) return [answer];
  if (tasks.length) return tasks.reverse().map(taskArtifact).filter(Boolean);
  const failures = nativeEvidence(job);
  return failures.length ? failures : rawEvidence(job);
}

export function artifactMarkdown(artifact) {
  const body = artifact.format === 'code' ? `\`\`\`${artifact.language || ''}\n${artifact.content}\n\`\`\`` : artifact.content;
  const status = { partial: '部分输出；整体任务未完成', live: '生成中的输出', complete: artifact.kind === 'discussion' ? '已保存的讨论发言' : '已生成的内容' }[artifact.status];
  return `# ${artifact.title}\n\n> ${status || '实际输出'}\n> 来源：${artifact.source || '已保存的模型输出'}${artifact.model ? ` · ${artifact.model}` : ''}\n\n${artifact.note ? `${artifact.note}\n\n` : ''}${body}`;
}

/** Plain-text clipboard fallback only; copied HTML/code is never evaluated. */
export async function copyText(value, { clipboard = globalThis.navigator?.clipboard, document = globalThis.document } = {}) {
  const content = String(value ?? '');
  if (!content) throw new Error('没有可复制的内容。');
  try { if (clipboard?.writeText) { await clipboard.writeText(content); return; } } catch { /* Native clipboard may be unavailable in an embedded view. */ }
  if (!document?.createElement || !document?.execCommand) throw new Error('复制不可用，请选择文本后手动复制。');
  const previous = document.activeElement;
  const field = document.createElement('textarea');
  field.value = content; field.readOnly = true; field.setAttribute('aria-hidden', 'true');
  Object.assign(field.style, { position: 'fixed', left: '-10000px', top: '0', opacity: '0' });
  document.body.appendChild(field);
  try { field.select(); if (!document.execCommand('copy')) throw new Error('复制不可用，请选择文本后手动复制。'); }
  finally { field.remove(); previous?.focus?.({ preventScroll: true }); }
}
