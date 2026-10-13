import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { groupScopeKey, groupDraftKey, readGroupSelection, readGroupDraft, scopedRooms, mergeRoomJobs, groupMessageRequest, groupMessages } from '../src/group-state.js';
import { applyEvent } from '../src/workbench-state.js';

const members = [{ id: 'writer', name: 'Writer', model: 'model-a', role: 'Draft the proposal' }, { id: 'reviewer', name: 'Reviewer', model: 'model-b', role: 'Check the evidence' }];
const room = { id: 'room-a', project_id: 'project-a', team_snapshot: { name: 'Team', members } };
const job = { id: 'job-a', project_id: 'project-a', group_room_id: 'room-a', group_team_snapshot: room.team_snapshot, prompt: 'Discuss our project', models: members.map((member) => member.model), status: 'complete', created_at: '2026-10-13T01:00:00Z', updated_at: '2026-10-13T01:01:00Z', result: { messages: [{ model: 'model-a', round: 1, content: 'Actual answer' }] } };

test('group rooms and drafts remain isolated by project and room', () => {
  const values = { [groupScopeKey('project-a')]: JSON.stringify({ roomId: 'room-a' }), [groupDraftKey('project-a', 'room-a')]: JSON.stringify({ prompt: 'private draft', recipientIds: ['reviewer'], contextTurns: 8, context: { attachment_ids: ['image-a'], project_id: 'wrong' } }) };
  const storage = { getItem: (key) => values[key] };
  assert.equal(readGroupSelection(storage, 'project-a'), 'room-a'); assert.equal(readGroupSelection(storage, 'project-b'), null);
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').prompt, 'private draft');
  assert.equal(readGroupDraft(storage, 'project-a', 'room-b').prompt, '');
  assert.equal(readGroupDraft(storage, 'project-a', 'room-a').context.project_id, 'project-a');
  assert.deepEqual(readGroupDraft(storage, 'project-a', 'room-a').recipientIds, ['reviewer']);
  assert.equal(readGroupDraft({ getItem: () => '{invalid' }, 'p', 'r').contextTurns, 6);
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

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {GroupTurn} from "./src/components/GroupWorkspace.jsx"; export {default as WorkspaceNavigation} from "./src/components/WorkspaceNavigation.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/group-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { GroupTurn, WorkspaceNavigation } = module.exports;

test('actual continuous timeline exposes attributed full messages and useful actions', () => {
  const html = renderToStaticMarkup(React.createElement(GroupTurn, { job, room, onOpenArtifacts() {} }));
  assert.match(html, /Discuss our project/); assert.match(html, /Actual answer/); assert.match(html, /Writer/); assert.match(html, /model-a/);
  assert.match(html, /复制这条提问/); assert.match(html, /复制 Writer 的发言/); assert.match(html, /查看本轮成果/);
  assert.equal(html.includes('展开完整发言'), false);
});

test('group navigation is a primary workspace while single tools and missions remain discoverable', () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceNavigation, { groupsView: true, missionView: false, mode: 'chat', navigationTab: 'tools', activeJobs: [] }));
  assert.match(html, /项目群聊/); assert.match(html, /协作任务/); assert.match(html, /单项协作/); assert.match(html, /项目资源/);
});
