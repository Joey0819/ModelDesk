import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeDiagnostic, workflowFailure, workflowFormatDiagnostics, workflowToolSummary } from '../src/workflow-failure.js';
import { hydrateJob } from '../src/workbench-state.js';

const native = (seq, entry) => ({ job_id: 'job', type: 'framework_task', seq, status: entry.event_type, native_event: entry });
const historicalJob = () => ({
  id: 'job', mode: 'camel_workforce', status: 'error', result: null,
  error: 'RuntimeError: CAMEL Workforce 的原生任务状态未完成；已保留部分输出',
  workflow: { agents: [{ id: 'researcher', role: '研究员', tools: [] }, { id: 'reviewer', role: '审稿人', tools: [] }] },
  events: [
    native(1, { event_type: 'worker_created', worker_id: 'worker', role: 'researcher: 研究员；核查证据' }),
    native(2, { event_type: 'task_created', task_id: 'task.1', description: '查找原帖，核对来源并交付证据卡。' }),
    native(3, { event_type: 'task_assigned', task_id: 'task.1', worker_id: 'worker' }),
    native(4, { event_type: 'task_failed', task_id: 'task.1', worker_id: 'worker', error_message: 'Reddit 案例证据卡｜待核实\n本次会话没有可用的联网检索工具，无法搜索 Reddit、打开原帖或核对存档。\n以下为待核查线索。' }),
  ],
});

test('historical null-result Workforce failure surfaces the exact native cause and task without altering tool grants', () => {
  const job = historicalJob();
  const before = JSON.stringify(job);
  const failure = workflowFailure(job);
  assert.equal(failure.category, 'capability_missing');
  assert.equal(failure.role, '研究员');
  assert.equal(failure.taskId, 'task.1');
  assert.equal(failure.description, '查找原帖，核对来源并交付证据卡。');
  assert.match(failure.evidence, /没有可用的联网检索工具/);
  assert.equal(failure.tools[0].labels.length, 0);
  assert.equal(JSON.stringify(job), before);
});

test('new structured capability failure is used instead of the generic runtime error', () => {
  const job = historicalJob();
  job.result = { failure: { task_id: 'next', worker_role: '研究员', kind: 'capability_missing', reason: '该角色未配置 web_search/web_fetch，无法核实原帖或网页来源。', description: '核对实际网页来源。' } };
  const failure = workflowFailure(job);
  assert.equal(failure.category, 'capability_missing');
  assert.equal(failure.taskId, 'next');
  assert.equal(failure.description, '核对实际网页来源。');
  assert.match(failure.reason, /未配置 web_search/);
});

test('empty tools alone do not diagnose a missing capability or prevent text-only work', () => {
  const job = historicalJob();
  job.events = [];
  job.error = '原生子任务没有完成。';
  assert.equal(workflowFailure(job).category, 'task_failed');
  assert.deepEqual(workflowToolSummary(job.workflow).map((agent) => agent.tools), [[], []]);
  job.status = 'running';
  assert.equal(workflowFailure(job), null);
});

test('upstream timeout remains distinct from tool permissions, including selected tools', () => {
  const job = historicalJob();
  job.workflow.agents[0].tools = ['web_search', 'web_fetch'];
  job.result = { failure: { worker_role: '研究员', kind: 'upstream_timeout', reason: '模型请求超时或上游响应中断，有限传输重试后仍未完成。' } };
  assert.equal(workflowFailure(job).category, 'upstream_timeout');
  assert.match(workflowFailure(job).title, /超时/);
  assert.deepEqual(workflowFailure(job).tools[0].labels, ['网页搜索', '网页读取']);
});

test('native callbacks are retained as lifecycle events instead of fake completed task outputs', () => {
  const job = hydrateJob(historicalJob());
  assert.equal(job.result.workforce_events.length, 4);
  assert.equal(job.result.tasks, undefined);
  assert.equal(job.result.process, 'workforce');
  assert.equal(workflowFailure(job).category, 'capability_missing');
});

test('upstream diagnostic credentials are removed before rendering', () => {
  const rendered = safeDiagnostic('Authorization: Bearer private-session-token api_key=sk-example123 access_token="secret-access" refresh_token: secret-refresh AIzaExampleKey GOCSPX-example');
  for (const value of ['private-session-token', 'sk-example123', 'secret-access', 'secret-refresh', 'AIzaExampleKey', 'GOCSPX-example']) assert.equal(rendered.includes(value), false);
  assert.match(rendered, /已隐藏/);
});

test('invalid structured TaskResult is distinct from empty tool permissions and retains the configuration', () => {
  const job = historicalJob();
  job.result = { failure: { task_id: 'task.1', worker_role: '研究员', kind: 'structured_output_invalid', reason: '原生任务结果格式无效或输出被截断；原始研究输出已保留，任务未标为完成。' } };
  const original = JSON.stringify(job);
  const failure = workflowFailure(job);
  assert.equal(failure.category, 'structured_output_invalid');
  assert.match(failure.title, /格式无效或被截断/);
  assert.match(failure.suggestion, /缩短预期输出或拆分任务/);
  assert.doesNotMatch(failure.title, /工具|联网/);
  assert.equal(JSON.stringify(job), original);
});

test('format research and bounded finalization records survive event hydration and replay without duplication', () => {
  const research = { job_id: 'job', seq: 1, type: 'framework_message', agent: 'researcher', model: 'gpt-6.1-sol', role: '原生任务结果', phase: 'research', content: '实际研究来源：网页读取结果', finish_reasons: ['max_iteration'], tool_calls: 8, parsed: false, raw_truncated: false };
  const finalization = { job_id: 'job', seq: 2, type: 'framework_message', agent: 'researcher', model: 'gpt-6.1-sol', role: '原生结果整理', phase: 'finalization', content: '{"content":"未完成的输出', finish_reasons: ['length'], tool_calls: 0, parsed: false, evidence_truncated: true, preview_truncated: true };
  const hydrated = hydrateJob({ id: 'job', mode: 'camel_workforce', status: 'error', result: null, events: [research, finalization, research] });
  const diagnostics = workflowFormatDiagnostics(hydrated);
  assert.equal(diagnostics.length, 2);
  assert.equal(diagnostics[0].raw, research.content);
  assert.equal(diagnostics[0].tool_calls, 8);
  assert.deepEqual(diagnostics[1].finish_reasons, ['length']);
  assert.equal(diagnostics[1].evidence_truncated, true);
  assert.equal(diagnostics[1].preview_truncated, true);
  assert.equal(diagnostics[1].model, research.model);
});

test('authoritative saved formatter errors are visible and redacted after reload', () => {
  const raw = '实际研究输出 Bearer private-gateway-token';
  const result = { format_diagnostics: [{ agent: 'researcher', model: 'gpt-6.1-sol', phase: 'finalization_error', raw, error: 'api_key=sk-secret-example HTTP 408', status_code: 408, finish_reasons: ['length'], raw_truncated: true }] };
  const hydrated = hydrateJob({ id: 'job', status: 'error', result, events: [] });
  const diagnostics = workflowFormatDiagnostics(hydrated);
  assert.equal(diagnostics.length, 1);
  assert.equal(diagnostics[0].phase, 'finalization_error');
  assert.equal(diagnostics[0].status_code, 408);
  assert.equal(diagnostics[0].raw_truncated, true);
  assert.equal(diagnostics[0].raw.includes('private-gateway-token'), false);
  assert.equal(diagnostics[0].error.includes('sk-secret-example'), false);
  assert.equal(hydrated.result.format_diagnostics[0].raw, raw);
});

test('actual source-verification shape with 15 web calls and a valid TaskResult does not suggest missing tools', () => {
  const job = historicalJob();
  job.workflow.agents.forEach((agent) => { agent.tools = ['web_search', 'web_fetch']; });
  job.events.push(...Array.from({ length: 15 }, (_, i) => ({ job_id: 'job', seq: i + 5, type: 'tool_result', tool: i % 3 ? 'web_search' : 'web_fetch' })));
  job.result = {
    failure: { task_id: 'task.1', worker_role: '研究员', kind: 'task_failed', reason: '案例证据包｜指定 Reddit 帖子核查失败', description: '查找原帖，并区分候选结果与已核实事实。' },
    format_diagnostics: [{ agent: 'researcher', phase: 'research', model: 'gpt-6.1-sol', parsed: true, tool_calls: 15, finish_reasons: ['stop'], raw: '{"content":"指定原帖未确认；候选链接返回验证页面。","failed":true}' }],
  };
  const original = JSON.stringify(job);
  const failure = workflowFailure(hydrateJob(job));
  assert.equal(failure.category, 'source_unverified');
  assert.equal(failure.title, '来源尚未核实');
  assert.match(failure.summary, /15 次网页搜索或读取/);
  assert.match(failure.suggestion, /原帖 URL/);
  assert.match(failure.suggestion, /原创教学示例/);
  assert.doesNotMatch(failure.suggestion, /角色配置|权限|选择工具/);
  assert.equal(workflowFormatDiagnostics(job)[0].parsed, true);
  assert.equal(JSON.stringify(job), original);
});

test('explicit source-unverified kind is recognized while capability and timeout kinds stay authoritative', () => {
  const job = historicalJob();
  const reason = '指定 Reddit 帖子核查失败，来源尚未核实。';
  job.result = { failure: { kind: 'source_unverified', reason, worker_role: '研究员' } };
  assert.equal(workflowFailure(job).category, 'source_unverified');
  for (const kind of ['capability_missing', 'upstream_timeout', 'upstream_connection', 'structured_output_invalid']) {
    job.result.failure.kind = kind;
    assert.equal(workflowFailure(job).category, kind);
    assert.notEqual(workflowFailure(job).title, '来源尚未核实');
  }
});
