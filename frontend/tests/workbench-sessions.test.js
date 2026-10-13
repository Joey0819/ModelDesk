import test from 'node:test';
import assert from 'node:assert/strict';
import { readSessions, readConversationDrafts, conversationDraft, reviewContinuation, sourceSelection, restoredSession, conversationGroups, previousTurns } from '../src/workbench-sessions.js';

test('review continuity selects the latest completed conversation in exactly the same project', () => {
  const older = { id: 'old', mode: 'review', project_id: 'p', status: 'complete', conversation_id: 'old', created_at: '2026-10-10' };
  const latest = { ...older, id: 'latest', conversation_id: 'latest', created_at: '2026-10-11' };
  assert.equal(reviewContinuation([older, latest, { ...latest, id: 'failed', status: 'error', created_at: '2026-10-12' }, { ...latest, project_id: 'other', created_at: '2026-10-12' }], 'p').id, 'latest');
  assert.equal(reviewContinuation([latest], 'empty'), null);
});

test('drafts follow one conversation across modes without leaking into another task', () => {
  const drafts = readConversationDrafts({ getItem: () => JSON.stringify({ 'session-a': '继续完善这份方案', bad: null }) });
  assert.equal(conversationDraft(drafts, { id: 'chat', conversation_id: 'session-a' }), '继续完善这份方案');
  assert.equal(conversationDraft(drafts, { id: 'crew', conversation_id: 'session-a' }), '继续完善这份方案');
  assert.equal(conversationDraft(drafts, { id: 'chat', conversation_id: 'session-b' }), '');
  assert.deepEqual(readConversationDrafts({ getItem: () => '[' }), {});
});

test('saved sessions tolerate corrupt storage and sources never restore job internals or credentials', () => {
  assert.deepEqual(readSessions({ getItem: () => '[' }), {});
  assert.deepEqual(readSessions({ getItem: () => '[]' }), {});
  const selected = sourceSelection({ knowledge_ids: ['k', 'k', null], memory_scope: '论文', project_id: 'p', snapshot_id: 's', tools: ['web_search'], runtime: { token: 'private' }, prompt: 'private task' });
  assert.deepEqual(selected.knowledge_ids, ['k']);
  assert.equal(selected.project_id, 'p');
  assert.equal(selected.memory_scope, '论文');
  assert.equal(selected.remember, false);
  assert.equal('runtime' in selected, false);
  assert.equal('prompt' in selected, false);
  assert.deepEqual(sourceSelection(null), sourceSelection({}));
  assert.deepEqual(sourceSelection(readSessions({ getItem: () => '{"chat":{"context":null}}' }).chat.context), sourceSelection({}));
});

test('refresh after handing a chat to Council restores the next-turn choices instead of the older chat source', () => {
  const job = { id: 'same', mode: 'chat', project_id: 'old-project', snapshot_id: 'old-snapshot', models: ['old-model'], context_turns: 12 };
  const sessions = { chat: { job_id: 'same', context: job }, council: { job_id: 'same', context: { project_id: 'old-project', snapshot_id: 'chosen-snapshot' }, preferences: { models: ['chosen-model'], rounds: 4, context_turns: 7, system_prompt: '中文回答' } } };
  const restored = restoredSession(sessions, 'council', job);
  assert.equal(restored.context.project_id, 'old-project');
  assert.equal(restored.context.snapshot_id, 'chosen-snapshot');
  assert.deepEqual(restored.preferences.models, ['chosen-model']);
  assert.equal(restored.preferences.context_turns, 7);
  assert.equal(restored.preferences.rounds, 4);
  assert.equal(restored.preferences.system_prompt, '中文回答');
  assert.equal(restoredSession(sessions, 'crewai', job).context.project_id, 'old-project');
  sessions.council.context.project_id = 'foreign-project';
  assert.deepEqual(restoredSession(sessions, 'council', job).preferences.models, ['old-model']);
});

test('recent conversations group turns across modes and search prior prompts without losing latest status', () => {
  const jobs = [
    { id: 'a', conversation_id: 'c', mode: 'chat', prompt: '先讨论字幕', created_at: '2026-10-12T01:00:00Z' },
    { id: 'b', conversation_id: 'c', mode: 'council', prompt: '请审稿', status: 'complete', created_at: '2026-10-12T01:01:00Z' },
    { id: 'r', conversation_id: 'r', mode: 'review', prompt: '代码', created_at: '2026-10-12T02:00:00Z' },
  ];
  const groups = conversationGroups(jobs, '字幕');
  assert.equal(groups.length, 1);
  assert.equal(groups[0].latest.id, 'b');
  assert.equal(groups[0].title, '先讨论字幕');
  assert.deepEqual(groups[0].modes, ['chat', 'council']);
});

test('visible previous turns stay within conversation and project and exclude future turns', () => {
  const current = { id: 'now', conversation_id: 'c', project_id: 'p', created_at: '2026-10-12T02:00:00Z' };
  const jobs = [current, { ...current, id: 'old', created_at: '2026-10-12T01:00:00Z' }, { ...current, id: 'other', project_id: 'q', created_at: '2026-10-12T01:00:00Z' }, { ...current, id: 'new', created_at: '2026-10-12T03:00:00Z' }];
  assert.deepEqual(previousTurns(jobs, current).map((job) => job.id), ['old']);
});
