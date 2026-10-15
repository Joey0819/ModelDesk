import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  GROUP_HARNESS_MODES,
  groupMemberKind,
  groupMemberKindLabel,
  groupSelection,
  groupRuntimeRequest,
  buildGroupRuntimePrompt,
  buildMuseHandoffPrompt,
  extractGroupDeliverable,
  groupScopeKey,
  groupDraftKey,
  readGroupSelection,
  readGroupDraft,
  scopedRooms,
  mergeRoomJobs,
  groupMessageRequest,
  groupMessages,
  groupRecipients,
  groupTimeline,
  groupDeliveryMode,
} from '../src/group-state.js';
import { applyEvent } from '../src/workbench-state.js';

const members = [{ id: 'writer', name: 'Writer', model: 'model-a', role: 'Draft the proposal' }, { id: 'reviewer', name: 'Reviewer', model: 'model-b', role: 'Check the evidence' }];
const room = { id: 'room-a', project_id: 'project-a', team_snapshot: { name: 'Team', members } };
const job = { id: 'job-a', project_id: 'project-a', group_room_id: 'room-a', group_team_snapshot: room.team_snapshot, prompt: 'Discuss our project', models: members.map((member) => member.model), status: 'complete', created_at: '2026-10-13T01:00:00Z', updated_at: '2026-10-13T01:01:00Z', result: { messages: [{ model: 'model-a', round: 1, content: 'Actual answer' }] } };

test('group rooms and drafts remain isolated by project and room', () => {
  const values = { [groupScopeKey('project-a')]: JSON.stringify({ roomId: 'room-a' }), [groupDraftKey('project-a', 'room-a')]: JSON.stringify({ prompt: 'private draft', recipientIds: ['reviewer'], includeMuse: true, harnessMode: 'discuss_muse', contextTurns: 8, context: { attachment_ids: ['image-a'], project_id: 'wrong' } }) };
  const storage = { getItem: (key) => values[key] };
  assert.equal(readGroupSelection(storage, 'project-a'), 'room-a'); assert.equal(readGroupSelection(storage, 'project-b'), null);
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').prompt, 'private draft');
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').includeMuse, true);
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').harnessMode, 'discuss_muse');
  assert.equal(readGroupDraft(storage, 'project-a', 'room-b').prompt, '');
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').context.project_id, 'project-a');
  assert.deepEqual(readGroupDraft(storage, 'project-a', 'room-a').recipientIds, ['reviewer']);
  assert.equal(readGroupDraft({ getItem: () => '{invalid' }, 'p', 'r').contextTurns, 6);
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').deliveryMode, 'auto');
  assert.equal(readGroupDraft({ getItem: () => JSON.stringify({ deliveryMode: 'discussion' }) }, 'p', 'r').deliveryMode, 'discussion');
  assert.deepEqual(scopedRooms([room, { id: 'other', project_id: 'project-b' }, { id: 'standalone' }], 'project-a'), [room]);
});

test('paginated group messages deduplicate, preserve older turns, and reject foreign room responses', () => {
  const newer = { ...job, id: 'job-b', created_at: '2026-10-13T02:00:00Z', updated_at: '2026-10-13T02:00:00Z' };
  const loaded = mergeRoomJobs([newer], [job, newer, { ...job, id: 'foreign-room', group_room_id: 'room-b' }, { ...job, id: 'foreign-project', project_id: 'project-b' }], 'project-a', 'room-a');
  assert.deepEqual(loaded.map((item) => item.id), ['job-a', 'job-b']);
  const withUpdate = mergeRoomJobs(loaded, [{ ...newer, status: 'running', updated_at: '2026-10-12T00:00:00Z' }], 'project-a', 'room-a');
  assert.equal(withUpdate.at(-1).status, 'complete');
});

test('group routing uses explicit member IDs and never text or model-generated mentions', () => {
  const body = groupMessageRequest({ prompt: ' @Writer please answer ', recipientIds: ['reviewer'], context: { project_id: 'different-project', attachment_ids: ['image'], tools: ['math'], model: 'fake', roles: 'override', secret: 'not copied' }, contextTurns: 4 });
  assert.equal(body.prompt, '@Writer please answer'); assert.deepEqual(body.recipient_member_ids, ['reviewer']);
  assert.deepEqual(body.attachment_ids, ['image']); assert.deepEqual(body.tools, ['math']); assert.equal(body.context_turns, 4);
  assert.equal(body.rounds, 1);
  assert.equal(body.delivery_mode, 'auto');
  assert.equal(groupMessageRequest({ prompt: 'Discuss', recipientIds: [], context: {}, deliveryMode: 'discussion' }).delivery_mode, 'discussion');
  assert.equal(groupMessageRequest({ prompt: 'Work', recipientIds: [], context: {}, deliveryMode: 'unknown' }).delivery_mode, 'auto');
  assert.equal(groupMessageRequest({ prompt: 'Two rounds', recipientIds: [], context: {}, rounds: 2 }).rounds, 2);
  for (const key of ['project_id', 'model', 'roles', 'secret']) assert.equal(key in body, false);
});

test('streamed group member identity survives replay with error and partial flags', () => {
  const event = { job_id: job.id, seq: 1, type: 'message', member_id: 'reviewer', member_name: 'Frozen Reviewer', role: 'Frozen Reviewer', model: 'model-b', round: 1, content: 'partial evidence', incomplete: true };
  const streamed = applyEvent({ ...job, result: null, events: [] }, event);
  assert.equal(streamed.result.messages[0].member_id, 'reviewer'); assert.equal(streamed.result.messages[0].member_name, 'Frozen Reviewer');
  assert.equal(streamed.result.messages[0].incomplete, true); assert.equal(applyEvent(streamed, event).result.messages.length, 1);
  const messages = groupMessages(job, { ...room, team_snapshot: { members: [{ ...members[0], name: 'Later edited' }] } });
  assert.equal(messages[0].member_name, 'Writer');
});

test('roster edits preserve recipients and role identities in saved turns', () => {
  const changed = { ...room, team_snapshot: { members: [{ ...members[0], name: 'Changed', model: 'grok' }] } };
  assert.deepEqual(groupRecipients(job, changed), members);
  assert.deepEqual(groupRecipients({ ...job, group_recipient_member_ids: ['reviewer'] }, changed), [members[1]]);
  const sameModel = { ...job, group_team_snapshot: { members: members.map((member) => ({ ...member, model: 'grok' })) }, result: { messages: [{ member_id: 'writer', model: 'grok', content: 'draft' }, { member_id: 'reviewer', model: 'grok', content: 'review' }] } };
  assert.deepEqual(groupMessages(sameModel, changed).map((message) => message.member_name), ['Writer', 'Reviewer']);
});

test('Muse actual replies and CLI agent tasks join the room transcript in chronological order without crossing projects', () => {
  const task = { id: 'muse-a', project_id: 'project-a', room_id: 'room-a', created_at: '2026-10-13T02:00:00Z', response: 'Observed reply' };
  const agentTask = { id: 'agent-a', project_id: 'project-a', room_id: 'room-a', created_at: '2026-10-13T03:00:00Z', output: 'Code updated' };
  const entries = groupTimeline(
    [job, { ...job, id: 'otherjob', project_id: 'project-b' }],
    [task, { ...task, id: 'othermuse', room_id: 'room-b' }],
    'project-a',
    'room-a',
    [agentTask, { ...agentTask, id: 'otheragent', room_id: 'room-b' }],
  );
  assert.deepEqual(entries.map((item) => item.type), ['discussion', 'muse', 'agent_task']);
  assert.deepEqual(entries.map((item) => item.value.id), [job.id, task.id, agentTask.id]);
});

test('group harness synthesizes completed member deliverables for Muse and Agent handoff', () => {
  assert.equal(GROUP_HARNESS_MODES.length, 4);
  assert.match(extractGroupDeliverable(job, room), /【Writer】：Actual answer/);
  const handoff = buildMuseHandoffPrompt('Create a scene prompt', job, room);
  assert.match(handoff, /Create a scene prompt/);
  assert.match(handoff, /【Writer】：Actual answer/);
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {GroupTurn, GroupMuseTurn, GroupAgentTaskTurn, GroupDeliveryProgress, GroupDeliveryModeSelector} from "./src/components/GroupWorkspace.jsx"; export {default as WorkspaceNavigation} from "./src/components/WorkspaceNavigation.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/group-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { GroupTurn, GroupMuseTurn, GroupAgentTaskTurn, GroupDeliveryProgress, GroupDeliveryModeSelector, WorkspaceNavigation } = module.exports;

test('automatic delivery timeline distinguishes execution progress, full results, and blocked steps', () => {
  const delivery = { status: 'partial', title: '实际文章初稿', content: '完整收到的文章正文', steps: [
    { id: 'writing', member_id: 'writer', member_name: 'Writer', status: 'complete', instruction: '完成文章正文' },
    { id: 'images', member_id: 'muse', member_name: 'Muse', status: 'error', instruction: '生成配图', error: '未收到实际回复' },
  ], limitations: ['配图尚未生成'] };
  const html = renderToStaticMarkup(React.createElement(GroupTurn, { job: { ...job, group_delivery_mode: 'auto', result: { ...job.result, delivery } }, room, onOpenArtifacts() {}, onSendToMuse() {}, onHandOffToAgent() {} }));
  assert.match(html, /已保留部分成果/);
  assert.match(html, /Writer/); assert.match(html, /已完成/); assert.match(html, /Muse/); assert.match(html, /执行失败/);
  assert.match(html, /未收到实际回复/); assert.match(html, /配图尚未生成/); assert.match(html, /查看部分成果/);
  assert.match(html, /<details class="group-delivery-discussion"><summary>查看讨论与规划记录/);
  assert.doesNotMatch(html, /准备 Muse 任务|交由代码 Agent/);
  const blocked = renderToStaticMarkup(React.createElement(GroupDeliveryProgress, { job: { ...job, result: { delivery: { status: 'blocked' } } }, onOpenArtifacts() {} }));
  assert.match(blocked, /尚未生成完整成果/); assert.doesNotMatch(blocked, /查看完整成果/);
  const mode = renderToStaticMarkup(React.createElement(GroupDeliveryModeSelector, { value: 'auto', onChange() {} }));
  assert.match(mode, /aria-pressed="true" class="selected">自动完成/); assert.match(mode, /仅讨论/);
});

test('automatic delivery replay and context retain real final content without treating unfinished plans as output', () => {
  assert.equal(groupDeliveryMode(job), 'discussion');
  const active = { ...job, status: 'running', group_delivery_mode: 'auto', result: { messages: [{ model: 'gpt', content: 'A plan that is not delivered' }] }, events: [] };
  const progress = { job_id: job.id, seq: 9, type: 'delivery', delivery: { status: 'working', title: 'Article', steps: [{ id: 'a', status: 'running' }] } };
  const streamed = applyEvent(active, progress);
  assert.equal(streamed.result.delivery.status, 'working');
  assert.equal(streamed.result.messages.length, 1);
  assert.equal(applyEvent(streamed, progress).events.length, 1);
  const completed = { ...streamed, status: 'complete', result: { ...streamed.result, delivery: { status: 'complete', content: 'Actual final article body' } } };
  assert.equal(extractGroupDeliverable(active, room), '');
  assert.equal(extractGroupDeliverable({ ...completed, result: { ...completed.result, delivery: { status: 'partial', content: 'Incomplete' } } }, room), '');
  assert.equal(extractGroupDeliverable(completed, room), 'Actual final article body');
  const runtimePrompt = buildGroupRuntimePrompt('Review the completed article', [completed], room);
  assert.match(runtimePrompt, /Actual final article body/); assert.doesNotMatch(runtimePrompt, /A plan that is not delivered/);
  assert.equal(buildGroupRuntimePrompt('Review', [{ ...active, status: 'complete' }], room), 'Review');
});

test('actual continuous timeline exposes attributed full messages and useful actions including Muse and Agent handoff', () => {
  const html = renderToStaticMarkup(React.createElement(GroupTurn, { job, room, onOpenArtifacts() {}, onSendToMuse() {}, onHandOffToAgent() {} }));
  assert.match(html, /Discuss our project/); assert.match(html, /Actual answer/); assert.match(html, /Writer/); assert.match(html, /model-a/);
  assert.match(html, /复制这条提问/); assert.match(html, /复制 Writer 的发言/); assert.match(html, /查看本轮成果/);
  assert.match(html, /准备 Muse 任务/); assert.match(html, /交由代码 Agent/);
  assert.equal(html.includes('展开完整发言'), false);
  const agentHtml = renderToStaticMarkup(React.createElement(GroupAgentTaskTurn, { task: { id: 't1', member: { name: 'Engineer' }, engine: 'codex', status: 'complete', prompt: 'Implement feature', output: 'All tests passed' }, onOpenTaskMode() {}, onUseAgentOutput() {} }));
  assert.match(agentHtml, /Engineer/); assert.match(agentHtml, /All tests passed/); assert.match(agentHtml, /引用结果继续讨论/);
});

test('automatically assigned child tasks keep generated prompts collapsed and actual outputs visible', () => {
  const prompt = 'Task instructions\n{"quoted_context":"<script>never execute</script>"}';
  const autoTask = { id: 'child', parent_job_id: 'parent', project_id: 'project-a', room_id: 'room-a', member: { name: 'Engineer' }, engine: 'codex', status: 'complete', prompt, output: 'Actual code result', response: 'Actual Muse reply', sent: true, completion_evidence: 'framed_assistant_reply' };
  const agent = renderToStaticMarkup(React.createElement(GroupAgentTaskTurn, { task: autoTask, onOpenTaskMode() {} }));
  const muse = renderToStaticMarkup(React.createElement(GroupMuseTurn, { task: autoTask, projectId: 'project-a', roomId: 'room-a', onUseReply() {}, onOpenMuse() {} }));
  for (const html of [agent, muse]) {
    assert.match(html, /<strong>自动分配<\/strong>/);
    assert.match(html, /<details class="group-task-prompt-details"><summary>查看任务说明与引用上下文<\/summary><pre>/);
    assert.match(html, /&quot;quoted_context&quot;/); assert.match(html, /&lt;script&gt;never execute&lt;\/script&gt;/);
    assert.doesNotMatch(html, /<details[^>]*open|<strong>你<\/strong>/);
  }
  assert.match(agent, /Actual code result/); assert.match(muse, /Actual Muse reply/);
  const manual = renderToStaticMarkup(React.createElement(GroupAgentTaskTurn, { task: { ...autoTask, parent_job_id: null }, onOpenTaskMode() {} }));
  assert.match(manual, /<strong>你<\/strong>/); assert.doesNotMatch(manual, /group-task-prompt-details/);
});

test('native Muse transcript distinguishes complete observed replies from partial output', () => {
  const task = { id: 'muse', project_id: 'project-a', room_id: 'room-a', prompt: 'Research this', status: 'complete', sent: true, completion_evidence: 'framed_assistant_reply', response: 'Observed Muse reply' };
  const render = (value) => renderToStaticMarkup(React.createElement(GroupMuseTurn, { task: value, projectId: 'project-a', roomId: 'room-a', onUseReply() {}, onOpenMuse() {} }));
  assert.match(render(task), /Observed Muse reply/);
  assert.match(render(task), /引用回复继续讨论/);
  const partial = render({ ...task, status: 'uncertain' });
  assert.match(partial, /回复尚未确认完整/);
  assert.equal(partial.includes('引用回复继续讨论'), false);
});

test('group navigation is a primary workspace while single tools and missions remain discoverable', () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceNavigation, { groupsView: true, missionView: false, mode: 'chat', navigationTab: 'tools', activeJobs: [] }));
  assert.match(html, /项目群聊/); assert.match(html, /协作任务/); assert.match(html, /单项协作/); assert.match(html, /项目资源/);
});


test('mixed member broadcast is LLM discussion while explicit runtime selection preserves actual identity', () => {
  const codex = { id: 'builder', name: 'Engineer', kind: 'codex', model: null };
  const muse = { id: 'creative', name: 'Creative', kind: 'muse', model: null };
  const roster = [...members, codex, muse];
  assert.equal(groupMemberKind(members[0]), 'model');
  assert.equal(groupMemberKindLabel(codex), 'Codex Agent');
  assert.deepEqual(groupSelection(roster, []).discussionMembers, members);
  assert.equal(groupSelection(roster, []).runtimeMember, undefined);
  assert.equal(groupSelection(roster, ['builder']).runtimeMember.id, 'builder');
  assert.deepEqual(groupRecipients({ group_team_snapshot: { members: roster } }, room), members);
  assert.deepEqual(groupRecipients({ group_team_snapshot: { members: roster }, group_recipient_member_ids: roster.map((member) => member.id) }, room), members);
  const request = groupRuntimeRequest({ member: codex, projectId: 'project-a', roomId: 'room-a', prompt: ' Implement feature ' });
  assert.deepEqual(request, { target: 'group_task', body: { project_id: 'project-a', room_id: 'room-a', member_id: 'builder', prompt: 'Implement feature', engine: 'codex', kind: 'code' } });
  const museRequest = groupRuntimeRequest({ member: muse, projectId: 'project-a', roomId: 'room-a', prompt: 'Research proposal' });
  assert.equal(museRequest.target, 'muse');
  assert.equal(museRequest.body.member_id, 'creative');
  assert.equal('model' in museRequest.body, false);
  assert.throws(() => groupRuntimeRequest({ member: members[0], projectId: 'p', roomId: 'r', prompt: '@Muse run this' }), /运行时 Agent/);
  assert.throws(() => groupRuntimeRequest({ member: codex, roomId: 'r', prompt: 'Code' }), /Git/);
});

test('runtime task context is bounded, quoted, and scoped to actual completed group outputs', () => {
  const fixtureRoom = { ...room, team_snapshot: { ...room.team_snapshot, coordinator_model: 'lead-model' } };
  const outputTask = { id: 'code', project_id: room.project_id, room_id: room.id, status: 'complete', created_at: '2026-10-13T02:00:00Z', member: { name: 'Engineer' }, output: 'Actual execution result' };
  const unverifiedMuse = { ...outputTask, id: 'muse', created_at: '2026-10-13T03:00:00Z', output: undefined, response: 'Invented source', sent: true, harness_generated: true, completion_evidence: 'framed_assistant_reply' };
  const requestPrompt = buildGroupRuntimePrompt('Implement export', [job, { ...job, id: 'other', group_room_id: 'elsewhere', result: { messages: [{ content: 'Foreign content' }] } }], fixtureRoom, [outputTask, { ...outputTask, id: 'failed', status: 'error', output: 'Failed result' }], [unverifiedMuse]);
  assert.match(requestPrompt, /Implement export/);
  assert.match(requestPrompt, /Actual answer/);
  assert.match(requestPrompt, /Actual execution result/);
  assert.match(requestPrompt, /不代表新的执行授权/);
  assert.doesNotMatch(requestPrompt, /Foreign content|Failed result|Invented source/);
  assert.ok(buildGroupRuntimePrompt('x'.repeat(20000), [job], fixtureRoom).length <= 12000);
});

test('coordinator plan and synthesis keep phase attribution during streamed replay and rendering', () => {
  const event = { job_id: job.id, seq: 1, type: 'message', member_id: '__coordinator__', member_name: '群主控', model: 'lead-model', round: 1, phase: 'plan', content: 'Delegate real work explicitly.' };
  const streamed = applyEvent({ ...job, result: null, events: [] }, event);
  assert.equal(streamed.result.messages[0].phase, 'plan');
  const html = renderToStaticMarkup(React.createElement(GroupTurn, { job: streamed, room, onOpenArtifacts() {} }));
  assert.match(html, /统筹规划/);
  assert.match(html, /lead-model/);
  const synthesis = { ...streamed, result: { messages: [{ ...streamed.result.messages[0], phase: 'synthesis' }] } };
  assert.match(renderToStaticMarkup(React.createElement(GroupTurn, { job: synthesis, room, onOpenArtifacts() {} })), /统筹汇总/);
});


test('failed synthesis, planning, and incomplete content cannot become completed handoff conclusions', () => {
  const planning = { member_id: '__coordinator__', member_name: '统筹模型', model: 'lead', phase: 'plan', content: 'Plan-only text must not be promoted.' };
  const incomplete = { model: 'model-a', content: 'Truncated member answer', incomplete: true };
  const failedSynthesis = { model: 'lead', phase: 'synthesis', content: '', error: 'Gateway timeout' };
  const failed = { ...job, result: { partial_failure: true, messages: [planning, incomplete, failedSynthesis] } };
  assert.equal(extractGroupDeliverable(failed, room), '');
  assert.equal(buildMuseHandoffPrompt('Original task', failed, room), 'Original task');
  assert.equal(buildGroupRuntimePrompt('Explicit work', [failed], room), 'Explicit work');
  assert.equal(extractGroupDeliverable({ ...job, result: { messages: [planning, incomplete] } }, room), '');
  const complete = { ...job, result: { messages: [planning, { ...planning, phase: 'synthesis', content: 'Actual completed synthesis' }] } };
  assert.match(extractGroupDeliverable(complete, room), /Actual completed synthesis/);
  assert.doesNotMatch(extractGroupDeliverable(complete, room), /Plan-only/);
});

test('restored selections cannot silently choose an executor after a member type changes', () => {
  const changedRoster = [{ ...members[0], kind: 'codex' }, members[1], { id: 'muse', kind: 'muse' }];
  for (const ids of [['writer', 'reviewer'], ['writer', 'muse'], ['writer', 'removed-member']]) {
    const selection = groupSelection(changedRoster, ids);
    assert.equal(selection.runtimeMember, undefined);
    assert.match(selection.selectionError, /重新选择/);
  }
  assert.equal(groupSelection(changedRoster, ['writer']).runtimeMember.id, 'writer');
  assert.equal(groupSelection(changedRoster, ['writer']).selectionError, '');
  assert.deepEqual(groupSelection(changedRoster, []).discussionMembers, [members[1]]);
});
