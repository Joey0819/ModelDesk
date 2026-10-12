const frameworkModes = new Set(['camel', 'camel_workforce', 'crewai', 'crewai_flow']);

export const WORKFLOW_TOOLS = [
  ['math', '数学计算'], ['text_stats', '文本统计'], ['web_fetch', '网页读取'],
  ['web_search', '网页搜索'], ['knowledge_search', '知识检索'],
  ['memory_read', '读取记忆'], ['memory_save', '保存记忆'],
  ['project_read', '代码上下文'], ['mcp_call', 'MCP 工具'],
  ['statistics', '统计汇总'], ['markdown_to_text', 'Markdown 转文本'],
];

export function workflowToolSummary(workflow) {
  const labels = Object.fromEntries(WORKFLOW_TOOLS);
  return (workflow?.agents || []).map((agent) => ({
    id: agent.id,
    role: agent.role || agent.id || '未命名角色',
    tools: [...new Set(agent.tools || [])],
    labels: [...new Set(agent.tools || [])].map((tool) => labels[tool] || tool),
  }));
}

/** Keep diagnostic output readable without exposing credentials from an upstream error. */
export function safeDiagnostic(value) {
  return String(value || '')
    .replace(/\bBearer\s+[^\s"'<>]+/gi, 'Bearer [已隐藏]')
    .replace(/\b(?:sk-|AIza|GOCSPX-)[A-Za-z0-9_-]+/g, '[已隐藏凭据]')
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[已隐藏凭据]')
    .replace(/((?:access_token|refresh_token|api_key|authorization)["']?\s*[:=]\s*["']?)[^\s,"'<>]+/gi, '$1[已隐藏]')
    .slice(0, 20000);
}

const missingWebCapability = /(?:没有|无|未提供|未启用|缺少|不可用|无法使用).{0,18}(?:联网|网络|网页|浏览|搜索|检索).{0,12}工具|(?:联网|网页|网络|浏览|检索|搜索)工具.{0,18}(?:未启用|不可用|未提供|缺失)|(?:no|without|unavailable|lack(?:s|ing)?).{0,35}(?:web|internet|brows(?:e|ing)|search).{0,20}tools?/i;
const timeoutFailure = /\b(?:HTTP\s*408|408\s+Request\s+Timeout|ReadTimeout|ConnectTimeout|TimeoutError)\b|(?:上游|网关|模型).{0,12}(?:请求)?超时|upstream.{0,15}tim(?:ed?\s*out|eout)/i;
const unverifiedSource = /(?:指定|目标).{0,30}(?:Reddit|帖子|原帖|来源).{0,15}(?:核查失败|尚未核实|未能核实|无法核实)|(?:原帖|来源).{0,12}(?:核查失败|尚未核实|未能核实|无法核实)/i;

/** Stored diagnostics remain readable after reload, even if the final result is absent. */
export function workflowFormatDiagnostics(job) {
  const stored = job?.result?.format_diagnostics;
  const entries = Array.isArray(stored) && stored.length ? stored : (job?.events || [])
    .filter((event) => event.type === 'framework_message' && ['research', 'finalization', 'finalization_error'].includes(event.phase))
    .map((event) => ({ ...event, raw: event.raw ?? event.content }));
  return entries.map((entry) => ({
    ...entry,
    raw: safeDiagnostic(entry.raw),
    error: safeDiagnostic(entry.error),
    finish_reasons: (Array.isArray(entry.finish_reasons) ? entry.finish_reasons : []).map(safeDiagnostic),
  }));
}

/** Prefer the recorded native failure, including older jobs with a null final result. */
export function workflowFailure(job) {
  if (!job || !frameworkModes.has(job.mode) || !['error', 'failed'].includes(job.status)) return null;
  const nativeEvents = (job.events || []).flatMap((event) => event.native_event ? [event.native_event] : event.type === 'native_event' ? [event] : []);
  const failed = [...nativeEvents].reverse().find((event) => event.event_type === 'task_failed');
  const failure = job.result?.failure || job.result?.tasks?.find((task) => task.failure)?.failure || {};
  const taskId = failure.task_id || failed?.task_id;
  const assigned = [...nativeEvents].reverse().find((event) => event.event_type === 'task_assigned' && event.task_id === taskId);
  const workerId = failure.worker_id || failed?.worker_id || assigned?.worker_id;
  const worker = nativeEvents.find((event) => event.event_type === 'worker_created' && event.worker_id === workerId);
  const task = nativeEvents.find((event) => event.event_type === 'task_created' && event.task_id === taskId);
  const workerRole = failure.worker_role || worker?.role || '';
  const agent = (job.workflow?.agents || []).find((item) => item.id === workerId || item.id === workerRole || item.role === workerRole || workerRole.startsWith(`${item.id}:`));
  const role = agent?.role || workerRole.split('；')[0] || '原生框架分配的角色';
  const reason = safeDiagnostic(failure.reason || failed?.error_message || job.error);
  const description = safeDiagnostic(failure.description || task?.description || failed?.metadata?.task_content || '');
  const kind = failure.kind;
  const mayInfer = !kind || ['task_failed', 'incomplete'].includes(kind);
  const tools = agent ? [...new Set(agent.tools || [])] : null;
  // Empty tools alone are valid for text-only work; only a recorded failure can diagnose a gap.
  const networkEvidence = (mayInfer && missingWebCapability.test(reason)) || (kind === 'capability_missing' && /联网|网页|web_search|web_fetch|internet|brows/i.test(reason));
  const missingNetwork = networkEvidence
    && (!tools || !tools.some((tool) => ['web_search', 'web_fetch'].includes(tool)));
  const timedOut = kind === 'upstream_timeout' || (mayInfer && timeoutFailure.test(reason));
  const connectionFailed = kind === 'upstream_connection';
  const invalidOutput = kind === 'structured_output_invalid';
  const sourceUnverified = kind === 'source_unverified' || (mayInfer && !missingNetwork && !timedOut && unverifiedSource.test(reason));
  const webCalls = (job.events || []).filter((event) => event.type === 'tool_result' && ['web_search', 'web_fetch'].includes(event.tool)).length;
  const sentence = reason.split(/[\n。！？]/).find((line) => missingWebCapability.test(line))?.trim();
  let title = '原生协作任务未完成';
  let summary = '框架已保存失败原因与运行记录，请检查任务要求和角色配置后重新发起。';
  let suggestion = '检查下方实际失败任务与原始说明。调整任务、角色或输入材料后，可重新发起一次任务。';
  if (invalidOutput) {
    title = '原生结果格式无效或被截断';
    summary = '原生框架未获得可用的 TaskResult；原始研究输出已保留，本次任务未标为完成。';
    suggestion = '展开“原生结果整理记录”检查原始输出、终止标记与整理错误。可缩短预期输出或拆分任务后重新提交；模型、提示词和工具不会自动更改。';
  } else if (missingNetwork) {
    title = '查证任务缺少联网工具';
    summary = `「${role}」的失败记录明确说明无法使用联网检索工具，外部来源尚未核查。`;
    suggestion = '检查负责查证角色的“网页搜索”和“网页读取”工具选择；也可在任务中提供原帖链接与正文材料。工具需要你手动选择后再提交。';
  } else if (timedOut) {
    title = '模型请求超时';
    summary = '本次失败记录包含上游模型超时；完整任务结果尚未生成。';
    suggestion = '检查本地网关连接，稍后重新提交；可缩小任务或调整参与模型。已有输出仍保存在记录中。';
  } else if (connectionFailed) {
    title = '模型连接未完成';
    summary = '原生框架记录了上游模型连接失败，完整任务结果尚未生成。';
    suggestion = '检查本地网关与账户连接状态，再重新提交；已保存的任务配置和输出可继续查看。';
  } else if (kind === 'capability_missing') {
    title = '任务所需能力尚未具备';
    summary = '原生角色明确报告任务所需能力缺失，相关要求尚未完成。';
    suggestion = '核对实际失败说明，为负责该任务的角色手动选择所需工具，或补充可用的输入材料后重新提交。';
  } else if (sourceUnverified) {
    title = '来源尚未核实';
    summary = `${webCalls ? `本次已执行 ${webCalls} 次网页搜索或读取，指定来源仍未得到可靠验证。` : '指定原帖或来源尚未得到可靠验证，来源核查未满足任务要求。'}查证输出与证据缺口已保留，线索未标为已证实。`;
    suggestion = '请补充原帖 URL、可读正文或其他可靠原文材料；如需不依赖该原帖的写作，可明确要求“原创教学示例”，并保留示例标注。';
  }
  return {
    title, summary, suggestion, taskId, role, description, reason,
    evidence: safeDiagnostic(sentence || (reason.split('\n').find((line) => line.trim()) || '')).slice(0, 320),
    technical: safeDiagnostic(job.error),
    source: failure.reason ? '框架保存的失败结果' : failed ? '原生 task_failed 事件' : '任务错误记录',
    tools: workflowToolSummary(job.workflow),
    category: invalidOutput ? 'structured_output_invalid' : missingNetwork ? 'capability_missing' : timedOut ? 'upstream_timeout' : connectionFailed ? 'upstream_connection' : sourceUnverified ? 'source_unverified' : kind || 'task_failed',
  };
}
