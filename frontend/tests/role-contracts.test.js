import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { teamRequest, teamIssues, roleContractIssues, removeTeamMember, copyRunTeam } from '../src/mission-teams.js';

const members = [
  { id: 'writer', name: '策划', kind: 'model', model: 'gpt', role: '整理需求', required: true },
  { id: 'builder', name: '工程师', kind: 'codex', role: '实现', required: true, after_member_ids: ['writer'], expected_output: '实际补丁与测试记录' },
];
test('role contracts survive serialization and copies retain the explicit coordinator', () => {
  const team = { name: '工作组', coordinator_model: 'gpt', members };
  const saved = teamRequest(team, null);
  assert.deepEqual(saved.members[1].after_member_ids, ['writer']);
  assert.equal(saved.members[1].expected_output, '实际补丁与测试记录');
  assert.equal(saved.members[1].required, true);
  assert.equal(copyRunTeam({ team_snapshot: saved }).coordinator_model, 'gpt');
  assert.deepEqual(teamIssues(team, [{id:'gpt'}], {allowRepeatedModels:true,allowExecutableMembers:true}), []);
  assert.ok(teamIssues(team, [{id:'gpt'}]).some((item) => item.includes('交接约定')));
});
test('cyclic role edges are invalid and removing a role clears only its edges', () => {
  assert.deepEqual(roleContractIssues(members), []);
  assert.match(roleContractIssues([{...members[0], after_member_ids:['builder']}, members[1]])[0], /循环/);
  assert.match(roleContractIssues([members[0], {...members[1], after_member_ids:['missing']}])[0], /已移除/);
  const updated = removeTeamMember({members}, 'writer');
  assert.deepEqual(updated.members[0].after_member_ids, []);
  assert.deepEqual(members[1].after_member_ids, ['writer']);
  assert.equal(updated.members[0].expected_output, members[1].expected_output);
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default, TeamRoleMap} from "./src/components/RoleContractFields.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/role-contract-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
test('mixed roles show their actual runtime and accessible scheduling controls', () => {
  const html = renderToStaticMarkup(React.createElement(module.exports.default, {member:members[1], members, index:1, onChange() {}}));
  assert.match(html, /aria-label="成员 2 必须参与"/);
  assert.match(html, /aria-label="成员 2 等待 策划"/);
  assert.match(html, /aria-label="成员 2 交付约定"/);
  assert.match(html, /隔离项目副本/);
  assert.doesNotMatch(html, /<details[^>]*open/);
  const map = renderToStaticMarkup(React.createElement(module.exports.TeamRoleMap, {members}));
  assert.match(map, /Codex Agent/);
  assert.match(map, /接收：策划/);
  assert.match(map, /实际补丁与测试记录/);
});
