import { test } from 'node:test';
import assert from 'node:assert/strict';
import { artifactMarkdown, copyText, deriveArtifacts } from '../src/artifacts.js';
import { managedWorkerDocuments } from '../src/delivery-media.js';

const job = (mode, result, extra = {}) => ({ id: 'job', mode, status: 'complete', models: ['gpt', 'gemini'], result, events: [], ...extra });

test('automatic group delivery presents the actual complete body instead of the last participant utterance', () => {
  const result = {
    messages: [{ member_id: 'reviewer', model: 'gemini', content: 'I approve the writer draft.' }],
    delivery: { status: 'complete', title: '可发布的文章', content: '# 实际完整正文\n\n第一节与第二节的完整内容。', model: 'gpt', steps: [] },
  };
  const artifacts = deriveArtifacts(job('group', result, { group_delivery_mode: 'auto' }));
  assert.equal(artifacts.length, 1);
  assert.equal(artifacts[0].id, 'group:delivery');
  assert.equal(artifacts[0].kind, 'deliverable');
  assert.equal(artifacts[0].status, 'complete');
  assert.equal(artifacts[0].content, result.delivery.content);
  assert.doesNotMatch(artifactMarkdown(artifacts[0]), /I approve|最后发言/);
  assert.equal(deriveArtifacts(job('group', result, { status: 'running' }))[0].status, 'live');
});

test('automatic partial and blocked deliveries preserve limitations and never promote discussion as a result', () => {
  const messages = [{ model: 'gpt', content: 'Use Muse to generate the missing images.' }];
  for (const delivery of [undefined, { status: 'working', content: '' }, { status: 'blocked', limitations: ['Muse 无法读取实际回复'] }]) {
    assert.deepEqual(deriveArtifacts(job('group', { messages, delivery }, { group_delivery_mode: 'auto' })), []);
  }
  const partial = deriveArtifacts(job('group', { messages, delivery: { status: 'partial', title: '文章初稿', content: '实际收到的正文', limitations: ['配图未生成'] } }, { group_delivery_mode: 'auto' }));
  assert.equal(partial[0].status, 'partial');
  assert.match(partial[0].note, /配图未生成/);
  assert.match(artifactMarkdown(partial[0]), /整体任务未完成/);
  assert.equal(deriveArtifacts(job('group', { messages }))[0].kind, 'discussion');
});

test('independent model responses retain attribution, partial errors and live content', () => {
  const result = { responses: [{ model: 'gpt', text: '完整回答' }, { model: 'gemini', text: '已收到的文字', error: '请求中断' }] };
  const artifacts = deriveArtifacts(job('chat', result));
  assert.equal(artifacts[0].id, 'chat:gpt');
  assert.equal(artifacts[0].status, 'complete');
  assert.equal(artifacts[1].content, '已收到的文字');
  assert.equal(artifacts[1].status, 'partial');
  assert.match(artifacts[1].note, /请求中断/);
  assert.equal(deriveArtifacts(job('chat', result, { status: 'running' }))[0].status, 'live');
});

test('Council exposes only the real chairman synthesis, without promoting independent answers to a final result', () => {
  const stage1 = [{ model: 'gpt', response: '初步回答' }];
  assert.equal(deriveArtifacts(job('council', { stage1 }))[0].status, 'partial');
  assert.equal(deriveArtifacts(job('council', { stage1 }))[0].kind, 'evidence');
  const artifacts = deriveArtifacts(job('council', { stage1, stage3: { model: 'gemini', response: '实际主席结论' } }));
  assert.deepEqual(artifacts.map((item) => item.id), ['council:synthesis']);
  assert.equal(artifacts[0].content, '实际主席结论');
});

test('role and group panels choose each participant latest actual utterance and ignore planning messages', () => {
  const messages = [{ role: '研究员', model: 'gpt', content: '旧方案', round: 1 }, { role: '任务规划', model: 'gpt', content: '内部计划' }, { role: '审稿人', model: 'gemini', content: '最终复核', round: 2 }, { role: '研究员', model: 'gpt', content: '修正方案', round: 2 }];
  assert.deepEqual(deriveArtifacts(job('camel', { messages })).map((item) => item.content), ['修正方案', '最终复核']);
  assert.equal(deriveArtifacts(job('camel', { messages }))[0].kind, 'discussion');
  const group = deriveArtifacts(job('group', { messages: [{ model: 'gpt', content: '第一轮', round: 1 }, { model: 'gpt', content: '最后发言', round: 2 }, { model: 'gemini', content: '收到的半句话', round: 2, incomplete: true }] }));
  assert.equal(group[0].status, 'partial');
  assert.equal(group[1].id, 'group:1');
});

test('group artifacts keep distinct members sharing a model and scope later failures to their member', () => {
  const artifacts = deriveArtifacts(job('group', { messages: [
    { model: 'grok', member_id: 'writer', member_name: '作者', content: 'draft', round: 1 },
    { model: 'grok', member_id: 'reviewer', member_name: '审稿人', content: 'review', round: 1 },
    { model: 'grok', member_id: 'reviewer', member_name: '审稿人', content: '', error: 'failed', round: 2 },
  ] }));
  assert.equal(artifacts.length, 2);
  assert.equal(artifacts.find((item) => item.content === 'draft').status, 'complete');
  assert.equal(artifacts.find((item) => item.content === 'review').status, 'partial');
  assert(artifacts.some((item) => item.title.includes('作者')));
  assert(artifacts.some((item) => item.title.includes('审稿人')));
});

test('successful Workforce prefers the final completed delivery task instead of concatenated framework output', () => {
  const tasks = [{ id: 'evidence', status: 'complete', description: '搜集依据', output: '原始依据' }, { id: 'write', status: 'complete', description: '整合为完整文章并交付', output: '# 最终文章\n\n实际正文', agent: 'reviewer' }];
  const artifacts = deriveArtifacts(job('camel_workforce', { tasks, answer: '原始依据\n\n实际正文', partial: false }));
  assert.equal(artifacts[0].id, 'task:write');
  assert.equal(artifacts[0].content, '# 最终文章\n\n实际正文');
  assert.equal(artifacts[0].status, 'complete');
  assert.equal(artifacts.at(-1).id, 'workflow:answer');
  assert.match(artifacts.at(-1).note, /汇总记录/);
});

test('source-verification failures preserve actual failed evidence without a completed artifact', () => {
  const result = { partial: true, answer: '', tasks: [{ id: 'verify', status: 'failed', description: '核实指定来源', output: '已经检索15次，但指定原帖尚未核实；不应据此发布真实案例。', agent: 'researcher' }] };
  const saved = job('camel_workforce', result, { status: 'error' });
  const original = JSON.stringify(saved);
  const artifacts = deriveArtifacts(saved);
  assert.equal(artifacts[0].id, 'task:verify');
  assert.equal(artifacts[0].kind, 'evidence');
  assert.equal(artifacts[0].status, 'partial');
  assert.match(artifacts[0].content, /尚未核实/);
  assert.equal(JSON.stringify(saved), original);
});

test('historical null-result failures reconstruct only real native task evidence', () => {
  const saved = job('camel_workforce', null, { status: 'error', events: [{ type: 'framework_task', seq: 3, native_event: { event_type: 'task_failed', task_id: 'task.1', error_message: '实际保留的证据缺口' } }] });
  const artifact = deriveArtifacts(saved)[0];
  assert.equal(artifact.id, 'failure:task.1');
  assert.equal(artifact.content, '实际保留的证据缺口');
  assert.equal(artifact.status, 'partial');
});

test('Crew and conditional Flow use actual answer and do not manufacture skipped task output', () => {
  assert.equal(deriveArtifacts(job('crewai', { answer: '真正交付结果' }))[0].id, 'workflow:answer');
  assert.equal(deriveArtifacts(job('crewai_flow', { tasks: [{ id: 'skip', status: 'skipped', output: '不得展示为已执行' }] })).length, 0);
  assert.equal(deriveArtifacts(job('crewai_flow', { answer: '已执行分支的结果' }))[0].content, '已执行分支的结果');
});

test('HTML previews remain source text and exported partial content keeps its evidence boundary', () => {
  const artifact = deriveArtifacts(job('chat', { responses: [{ model: 'gpt', text: '<html><script>alert(1)</script></html>' }] }, { status: 'cancelled' }))[0];
  assert.equal(artifact.format, 'code');
  assert.equal(artifact.language, 'html');
  const markdown = artifactMarkdown(artifact);
  assert.match(markdown, /整体任务未完成/);
  assert.match(markdown, /```html\n<html>/);
});

test('legacy report renders its safe preview, while the saved delivery stays original', () => {
  const delivery = { title: '交付报告', status: 'complete', content: '<html><body><h1>报告</h1></body></html>', preview_content: '# 报告' };
  const artifact = deriveArtifacts(job('group', { delivery }))[0];
  assert.equal(artifact.format, 'markdown');
  assert.equal(artifact.content, '# 报告');
  assert.equal(artifact.report_preview, true);
  assert.match(delivery.content, /^<html>/);
});

test('no result means no invented deliverable, including running and failed jobs', () => {
  assert.deepEqual(deriveArtifacts(null), []);
  assert.deepEqual(deriveArtifacts(job('chat', null, { status: 'running' })), []);
  assert.deepEqual(deriveArtifacts(job('camel_workforce', null, { status: 'error', error: '没有输出' })), []);
});

test('clipboard primary path writes plain text exactly and never invokes fallback', async () => {
  let copied;
  await copyText('<script>not executed</script>', { clipboard: { writeText: async (content) => { copied = content; } }, document: null });
  assert.equal(copied, '<script>not executed</script>');
});

test('clipboard fallback uses a temporary text field and restores focus even when copying is blocked', async () => {
  let value; let removed = false; let focused = false;
  const field = { style: {}, setAttribute() {}, select() { value = this.value; }, remove() { removed = true; } };
  const document = { activeElement: { focus() { focused = true; } }, createElement() { return field; }, body: { appendChild() {} }, execCommand() { return true; } };
  await copyText('<img src=x onerror=evil()>', { clipboard: { writeText: async () => { throw new Error('denied'); } }, document });
  assert.equal(value, '<img src=x onerror=evil()>');
  assert.equal(removed, true);
  assert.equal(focused, true);
  document.execCommand = () => false;
  await assert.rejects(copyText('plain text', { clipboard: null, document }), /手动复制/);
});

test('a saved MCP document can be downloaded from a job even when synthesis failed', () => {
  const id = 'f37b36b3-3187-4cb8-9022-f64d4b171f27';
  const group_room_id = '29004dd4-ee1f-4f9e-a2db-4692b32dc2a1';
  const project_id = 'a685afbe-cc84-4a22-9653-dcb976f80776';
  const task_id = '70adaef8-f03d-4766-817a-2a693ac5425a';
  const sha256 = 'b'.repeat(64);
  const path = `/api/groups/${group_room_id}/messages/${id}/mcp-documents/${task_id}?project_id=${project_id}`;
  const document = { id: task_id, filename: 'worker-report.txt', download_url: path, size: 24, sha256 };
  const step = { kind: 'mcp', status: 'complete', task_id, output_sha256: sha256, worker_document: document };
  const build = (...steps) => job('group', { delivery: { status: 'partial', steps } }, { id, group_room_id, project_id, status: 'error' });
  const apiBase = 'https://paired.example';
  assert.deepEqual(managedWorkerDocuments(build(step, step), apiBase), [{ ...document, download_url: apiBase + path }]);
  const [artifact] = deriveArtifacts(build(step), apiBase);
  assert.equal(artifact.status, 'partial');
  assert.equal(artifact.title, '已保存的交付文件');
  assert.equal(artifact.media_only, true);
  for (const unverified of [
    { ...step, status: 'blocked' },
    { ...step, output_sha256: 'a'.repeat(64) },
    { ...step, worker_document: { ...document, filename: '../report.txt' } },
    { ...step, worker_document: { ...document, download_url: `https://other.example${path}` } },
    { ...step, worker_document: { ...document, download_url: path.replace(project_id, group_room_id) } },
  ]) {
    assert.deepEqual(managedWorkerDocuments(build(unverified), apiBase), []);
    assert.deepEqual(deriveArtifacts(build(unverified), apiBase), []);
  }
});
