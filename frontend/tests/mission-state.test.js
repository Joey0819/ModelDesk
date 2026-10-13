import test from 'node:test';
import { setImmediate } from 'node:timers';
import assert from 'node:assert/strict';
import { missionRequest, copyMissionDraft, missionProgress, missionErrors, readMissionWorkspace, missionScopeKey, scopedMissions, selectedMissionStep, stepJobId } from '../src/mission-state.js';
import { temporaryMembers, teamIssues, teamRequest, runTeamSelection, copyRunTeam } from '../src/mission-teams.js';
import { pollMission } from '../src/mission-polling.js';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const models = ['model-a', 'model-b'];
const steps = [{ id: 'research', title: '研究', mode: 'camel', instructions: '阅读实际材料', require_review: true, rounds: 1 }, { id: 'review', title: '评审', mode: 'council', instructions: '核查' }];
const context = { project_id: 'project-a', attachment_ids: ['image'], knowledge_ids: ['knowledge'], remember: true, memory_scope: 'project-a', include_project_assets: false, tools: ['math'] };

test('mission request includes accepted context fields and bounds copied execution settings', () => {
  const request = missionRequest({ goal: ' goal ', templateId: 'research-review', steps, models, context: { ...context, _private: 'secret', UI_selection: true }, requestOptions: { timeout_seconds: 500, max_attempts: 2, status: 'complete', _auth: 'secret' } });
  assert.equal(request.goal, 'goal'); assert.equal(request.remember, true);
  assert.deepEqual(request.attachment_ids, ['image']); assert.equal(request.include_project_assets, false);
  assert.equal(request.timeout_seconds, 500); assert.equal(request.max_attempts, 2);
  assert.equal('_private' in request, false); assert.equal('status' in request, false); assert.equal('_auth' in request, false);
  assert.deepEqual(request.steps, steps); request.steps[0].title = 'changed'; assert.equal(steps[0].title, '研究');
});

test('copying a run preserves original workflow, model assignments, source selection and timeouts', () => {
  const originalSteps = [{ ...steps[0], models, workflow: { agents: [{ model: models[0], tools: ['math'] }] }, engine: 'camel' }];
  const run = { goal: 'goal', request: { goal: 'goal', template_id: 'custom', models, steps: originalSteps, ...context, timeout_seconds: 700, system_prompt: 'Use evidence' } };
  const copied = copyMissionDraft(run);
  const request = missionRequest({ goal: copied.draft, templateId: copied.templateId, steps: copied.steps, models: copied.models, context: copied.context, requestOptions: copied.requestOptions });
  assert.deepEqual(request.steps, originalSteps); assert.equal(request.system_prompt, 'Use evidence'); assert.equal(request.timeout_seconds, 700);
  assert.deepEqual(request.attachment_ids, ['image']); assert.equal(request.remember, true);
  copied.steps[0].workflow.agents[0].model = 'other'; assert.equal(run.request.steps[0].workflow.agents[0].model, models[0]);
});

test('saved mission drafts, lists and outputs cannot leak between projects', () => {
  const storage = { getItem: (key) => key === missionScopeKey('project-a') ? JSON.stringify({ draft: 'private', runId: 'a' }) : null };
  assert.equal(readMissionWorkspace(storage, 'project-a').draft, 'private'); assert.equal(readMissionWorkspace(storage, 'project-b').draft, '');
  const all = [{ id: 'a', project_id: 'project-a' }, { id: 'b', project_id: 'project-b' }, { id: 'u' }];
  assert.deepEqual(scopedMissions(all, 'project-a').map((run) => run.id), ['a']); assert.deepEqual(scopedMissions(all, null).map((run) => run.id), ['u']);
});

test('progress retains completed steps and selects latest attempt instead of stale output', () => {
  const run = { id: 'run', active_step_id: 'review', steps: [{ id: 'research', status: 'complete', output: { job_id: 'old' } }, { id: 'review', status: 'error', attempts: [{ job_id: 'failed' }, { job_id: 'retry' }], output: { job_id: 'failed' } }] };
  assert.equal(missionProgress(run).complete, 1); assert.equal(missionProgress(run).percent, 50);
  assert.equal(stepJobId(selectedMissionStep(run)), 'retry'); assert.equal(selectedMissionStep(run, { runId: 'different', stepId: 'research' }).id, 'review');
});

test('unavailable models, missing default team members and unfinished sources prevent launch', () => {
  assert.equal(missionErrors({ goal: 'goal', steps, models }).length, 0);
  assert(missionErrors({ goal: 'goal', steps, models: ['model-a'] }).some((error) => error.includes('两个模型')));
  assert(missionErrors({ goal: 'goal', steps: [{ ...steps[0], mode: 'crewai' }], models: ['model-a'] }).some((error) => error.includes('默认团队')));
  assert(missionErrors({ goal: 'goal', steps, models, catalog: [{ id: 'model-a' }] }).some((error) => error.includes('不可用')));
  assert(missionErrors({ goal: 'goal', steps, models, attachments: ['附件已删除'] }).includes('附件已删除'));
});

const tick = () => new Promise((resolve) => setImmediate(resolve));
test('terminal polling stops and a resumed run starts a new polling lifecycle', async () => {
  let generation = 1, scheduled = [], delivered = [], status = 'error';
  const options = { runId: 'r', projectId: 'p', getRun: async () => ({ id: 'r', project_id: 'p', status }), onRun: (run) => delivered.push(run.status), onError: assert.fail, currentGeneration: () => generation, setTimer: (callback) => { scheduled.push(callback); return callback; }, clearTimer: (callback) => { scheduled = scheduled.filter((item) => item !== callback); } };
  const stop = pollMission({ ...options, generation }); await tick(); assert.deepEqual(delivered, ['error']); assert.equal(scheduled.length, 0); stop();
  generation += 1; status = 'running'; const stopResumed = pollMission({ ...options, generation }); await tick(); assert.equal(scheduled.length, 1);
  status = 'complete'; await scheduled.shift()(); assert.deepEqual(delivered, ['error', 'running', 'complete']); assert.equal(scheduled.length, 0); stopResumed();
});

test('late responses after project navigation or cancellation cannot replace the current run', async () => {
  let resolve, generation = 1; const delivered = [];
  const stop = pollMission({ runId: 'r', projectId: 'a', getRun: () => new Promise((done) => { resolve = done; }), onRun: (run) => delivered.push(run), onError: assert.fail, generation, currentGeneration: () => generation });
  generation += 1; resolve({ id: 'r', project_id: 'a', status: 'running' }); await tick(); assert.equal(delivered.length, 0); stop();
  const wrong = []; const close = pollMission({ runId: 'x', projectId: 'a', getRun: async () => ({ project_id: 'b' }), onRun: assert.fail, onError: (error) => wrong.push(error), generation, currentGeneration: () => generation }); await tick(); assert.match(wrong[0], /另一个项目/); close();
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {MissionPlan} from "./src/components/MissionWorkspace.jsx"; export {default as MissionTeamPicker} from "./src/components/MissionTeamPicker.jsx"; export {default as WorkspaceNavigation} from "./src/components/WorkspaceNavigation.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/mission-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { MissionPlan, WorkspaceNavigation, MissionTeamPicker } = module.exports;

test('actual plan component exposes editable engines, order and human review gates', () => {
  const html = renderToStaticMarkup(React.createElement(MissionPlan, { steps, editable: true, onChange() {} }));
  assert.match(html, /协作执行计划/); assert.match(html, /协作引擎/); assert.match(html, /CAMEL/); assert.match(html, /LLM Council/);
  assert.match(html, /完成后暂停，等我审阅再继续/); assert.match(html, /上移/); assert.match(html, /移除此步/);
});

test('actual navigation gives cross-engine tasks a primary entry and retains legacy tools', () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceNavigation, { missionView: false, navigationTab: 'tools', mode: 'chat', activeJobs: [], modeMenu: true }));
  assert.match(html, /协作任务/); assert.match(html, /单项协作/); assert.match(html, /项目资源/); assert.match(html, /全部协作方式/); assert.match(html, /Workforce 团队/);
});


test('temporary groups send exactly the roles shown by the UI and preserve solo chat', () => {
  const request = missionRequest({ goal: 'goal', templateId: 'write-refine', steps, models, context });
  assert.deepEqual(request.members, temporaryMembers(models));
  assert.deepEqual(runTeamSelection(null, ['solo']), {});
  const selected = { id: 'saved-team', members: temporaryMembers(models) };
  assert.deepEqual(runTeamSelection(selected, models), { team_id: 'saved-team' });
});

test('copying a historical team uses immutable roles and removes the live saved-team ID', () => {
  const run = { project_id: 'project-a', team_snapshot: { id: 'saved-team', name: 'Research', members: temporaryMembers(models) } };
  const copy = copyRunTeam(run);
  assert.equal(copy.id, undefined); assert.equal(copy.project_id, 'project-a');
  assert.deepEqual(copy.members, run.team_snapshot.members);
  copy.members[0].role = 'changed'; assert.notEqual(run.team_snapshot.members[0].role, 'changed');
  const body = teamRequest(copy, 'project-a');
  assert.equal(body.project_id, 'project-a'); assert.equal(body.members[0].role, 'changed');
});

test('mission model choices remain isolated per project alongside copied temporary teams', () => {
  const first = { draft: 'A', models: ['a1', 'a2', 'a3'], team: { name: 'Copy', members: temporaryMembers(['a1', 'a2', 'a3']) } };
  const second = { draft: 'B', models: ['b1', 'b2'] };
  const storage = { getItem: (key) => JSON.stringify(key === missionScopeKey('a') ? first : second) };
  assert.deepEqual(readMissionWorkspace(storage, 'a').models, first.models);
  assert.deepEqual(readMissionWorkspace(storage, 'b').models, second.models);
  assert.equal(readMissionWorkspace(storage, 'a').team.members[2].model, 'a3');
  assert.equal(readMissionWorkspace({ getItem: () => '{' }, 'broken').models, null);
});

test('teams reject duplicate model membership and make disconnected models explicit', () => {
  const team = { name: 'Research', members: temporaryMembers(['a', 'a']) };
  assert(teamIssues(team, [{ id: 'a' }]).some((issue) => issue.includes('不能重复')));
  team.members[1].model = 'unconnected';
  assert(teamIssues(team, [{ id: 'a' }]).some((issue) => issue.includes('当前未接入：unconnected')));
  const html = renderToStaticMarkup(React.createElement(MissionTeamPicker, { projectId: 'project-a', team, models: ['a', 'unconnected'], catalog: [{ id: 'a' }], onChange() {}, onPending() {} }));
  assert.match(html, /选择协作组/); assert.match(html, /成员与分工/); assert.match(html, /新建组/); assert.match(html, /当前未接入：unconnected/);
});

test('CAMEL plan states its actual two-member participation before launch', () => {
  const html = renderToStaticMarkup(React.createElement(MissionPlan, { steps, models: ['a', 'b', 'c'], editable: false, onChange() {} }));
  assert.match(html, /CAMEL 双角色对话使用前两位成员：a \+ b/);
});
