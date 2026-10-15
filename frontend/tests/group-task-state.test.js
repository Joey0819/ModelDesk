import test from 'node:test';
import assert from 'node:assert/strict';
import { approvalDecision, eligibleReviews, memberEngineSupports, readTaskDraft, taskDraftKey, taskRequest, taskScope } from '../src/group-task-state.js';
import { isRemoteLocation, resolveApiBase } from '../src/api-base.js';

const task = { id: 'code-1', kind: 'code', status: 'complete', diff_sha256: 'patch-exact', member_id: 'writer', project_id: 'p1', room_id: 'r1' };
const review = { id: 'review-1', kind: 'review', status: 'complete', source_task_id: task.id, review_of_patch_sha256: task.diff_sha256, member_id: 'reviewer', project_id: 'p1', room_id: 'r1' };
test('approval selection requires completed independent review of the exact scoped patch', () => {
  const rejected = [{ ...review, id: 'self', member_id: 'writer' }, { ...review, id: 'stale', review_of_patch_sha256: 'old' }, { ...review, id: 'running', status: 'running' }, { ...review, id: 'foreign', room_id: 'r2' }, { ...review, id: 'other', project_id: 'p2' }];
  assert.deepEqual(eligibleReviews(task, [...rejected, review]), [review]);
  assert.deepEqual(eligibleReviews({ ...task, status: 'error' }, [review]), []);
});
test('approval click submits pinned base and patch hashes, never guessed latest state', () => {
  const card = { project_id: 'p1', room_id: 'r1', status: 'pending', base_sha: 'base-exact', patch_sha256: 'patch-exact' };
  assert.deepEqual(approvalDecision(card, 'approve'), { project_id: 'p1', room_id: 'r1', decision: 'approve', expected_base_sha: 'base-exact', expected_patch_sha256: 'patch-exact' });
  assert.throws(() => approvalDecision({ ...card, status: 'merged' }, 'approve'));
  assert.throws(() => approvalDecision({ ...card, patch_sha256: '' }, 'approve'));
  assert.throws(() => approvalDecision(card, 'force'));
});
test('worker requests carry explicit member and engine without accepting hidden data from drafts', () => {
  assert.deepEqual(taskRequest({ projectId: 'p', roomId: 'r', memberId: 'm', engine: 'codex', kind: 'code', prompt: '  fix export  ', sourceTaskId: 'not-for-code', token: 'never-forward' }), { project_id: 'p', room_id: 'r', member_id: 'm', engine: 'codex', kind: 'code', prompt: 'fix export' });
  assert.equal(taskRequest({ projectId: 'p', roomId: 'r', memberId: 'm', engine: 'claude', kind: 'review', prompt: 'review', sourceTaskId: 'source' }).source_task_id, 'source');
  assert.equal(taskScope('p&x', 'r/y'), 'project_id=p%26x&room_id=r%2Fy');
});
test('task drafts remember per-room settings while rejecting invalid executor values', () => {
  const values = { [taskDraftKey('p', 'r')]: JSON.stringify({ prompt: 'remember', engine: 'shell', memberId: 'm' }) };
  const storage = { getItem: (key) => values[key] };
  assert.equal(readTaskDraft(storage, 'p', 'r').prompt, 'remember');
  assert.equal(readTaskDraft(storage, 'p', 'r').engine, '');
  assert.equal(readTaskDraft(storage, 'p', 'other').prompt, '');
  assert.equal(readTaskDraft({ getItem: () => 'bad json' }, 'p', 'r').prompt, '');
});
test('phone API always uses its secure gateway origin even with a localhost build-time override', () => {
  const phone = new URL('https://mac.example.ts.net:8443/');
  assert.equal(isRemoteLocation(phone), true);
  assert.equal(resolveApiBase(phone, 'http://localhost:8001'), phone.origin);
  assert.equal(resolveApiBase(new URL('http://localhost:5173/')), 'http://localhost:8001');
  assert.equal(resolveApiBase(new URL('http://127.0.0.1:8001/'), 'http://127.0.0.1:8001'), 'http://127.0.0.1:8001');
  assert.equal(isRemoteLocation(new URL('http://localhost:8765/')), true);
  assert.equal(isRemoteLocation(new URL('file:///Applications/ModelDesk.app')), false);
});

test('independent task artifacts preserve observed partial output and actual patch provenance', async () => {
  const { deriveArtifacts } = await import('../src/artifacts.js');
  const artifacts = deriveArtifacts({ ...task, mode: 'group_agent', status: 'error', output: 'Partial real output', diff: 'diff --git a/a b/a', engine: 'codex', member: { name: '工程师' }, base_sha: '1234567890abcdef' });
  assert.equal(artifacts.length, 2);
  assert.equal(artifacts[0].content, 'Partial real output');
  assert.equal(artifacts[0].status, 'partial');
  assert.equal(artifacts[1].format, 'code');
  assert.match(artifacts[1].source, /1234567890ab/);
  assert.deepEqual(deriveArtifacts({ ...task, mode: 'group_agent' }), []);
});

test('engine support prevents code execution through a read-only member model', async () => {
  const { engineSupports, taskEngineLabel } = await import('../src/group-task-state.js');
  const member = { id: 'member_model', capabilities: ['review'] };
  assert.equal(engineSupports(member, 'code'), false);
  assert.equal(engineSupports(member, 'review'), true);
  assert.equal(taskEngineLabel({ engine: 'member_model', member: { model: 'gemini-real' } }), '成员模型 · gemini-real');
});
test('UNIX-second pairing expiry shows the correct countdown and accepts ISO compatibility', async () => {
  const { epochMillis, pairingSeconds, remoteDate } = await import('../src/remote-state.js');
  const now = 1791850000000;
  assert.equal(pairingSeconds(now / 1000 + 300, now), 300);
  assert.equal(pairingSeconds(now / 1000 - 1, now), 0);
  assert.equal(pairingSeconds(new Date(now + 60000).toISOString(), now), 60);
  assert.equal(epochMillis(now / 1000), now);
  assert.equal(remoteDate(null), '—');
});


test('typed runtime members cannot silently switch execution engines while legacy model assignments stay explicit', () => {
  const codex = { id: 'codex', capabilities: ['code', 'review'] };
  const claude = { id: 'claude', capabilities: ['code'] };
  const model = { id: 'member_model', capabilities: ['review'] };
  assert.equal(memberEngineSupports(codex, 'code', { kind: 'codex' }), true);
  assert.equal(memberEngineSupports(claude, 'code', { kind: 'codex' }), false);
  assert.equal(memberEngineSupports(model, 'review', { kind: 'codex' }), false);
  assert.equal(memberEngineSupports(claude, 'code', { kind: 'muse' }), false);
  assert.equal(memberEngineSupports(model, 'review', { model: 'legacy-llm' }), true);
  assert.equal(memberEngineSupports(codex, 'code', { model: 'legacy-llm' }), true);
  assert.equal(memberEngineSupports(claude, 'review', { kind: 'claude' }), false);
});
