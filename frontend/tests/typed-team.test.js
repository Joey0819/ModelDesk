import test from 'node:test';
import assert from 'node:assert/strict';
import { appendTeamMember, memberKind, memberKindLabel, teamRequest, teamIssues, temporaryMembers, recipeTeam } from '../src/mission-teams.js';
import { deriveArtifacts } from '../src/artifacts.js';
const catalog = [{ id: 'gpt', verified: true }, { id: 'gemini', verified: true }];
test('existing model roles keep their type and typed runtimes save without fake model IDs', () => {
  const team = { name: 'Mixed', members: temporaryMembers(['gpt','gemini']), coordinator_model: 'gemini' };
  const mixed = appendTeamMember(appendTeamMember(team, catalog, 'codex'), catalog, 'muse');
  assert.equal(memberKind(team.members[0]), 'model');
  assert.equal(memberKindLabel(mixed.members[2]), 'Codex Agent');
  const body = teamRequest(mixed, 'project');
  assert.equal(body.coordinator_model, 'gemini');
  assert.equal(body.members[2].kind, 'codex'); assert.equal(body.members[2].model, null);
  assert.equal(body.members[3].kind, 'muse'); assert.equal(body.members[3].model, null);
  assert.deepEqual(teamIssues(mixed,catalog,{allowRepeatedModels:true,allowExecutableMembers:true}),[]);
  assert.ok(teamIssues(mixed,catalog).some((error)=>error.includes('项目群聊')));
  assert.equal(team.members.length,2);
});
test('runtime-only team needs a real coordinator and unknown models are not marked ready', () => {
  const team = { name: 'Agents', members: [{id:'a',name:'Code',kind:'codex',model:null,role:'Implement'}, {id:'m',name:'Muse',kind:'muse',model:null,role:'Create'}] };
  assert.ok(teamIssues(team,catalog,{allowExecutableMembers:true}).some((error)=>error.includes('统筹模型')));
  assert.deepEqual(teamIssues({...team,coordinator_model:'gpt'},catalog,{allowExecutableMembers:true}),[]);
  assert.ok(teamIssues({...team,coordinator_model:'offline'},catalog,{allowExecutableMembers:true}).some((error)=>error.includes('当前未接入')));
});
test('coordinator plans are not deliverables and failed synthesis does not promote its plan', () => {
  const job = {id:'r',mode:'group',status:'complete',result:{messages:[{member_id:'coordinator',model:'gpt',phase:'plan',content:'Only a plan'},{member_id:'reviewer',member_name:'Reviewer',model:'gemini',content:'Actual response'},{member_id:'coordinator',model:'gpt',phase:'synthesis',content:'',error:'failed'}]}};
  const artifacts=deriveArtifacts(job);
  assert.equal(artifacts.length,1); assert.equal(artifacts[0].content,'Actual response');
  job.result.messages[2]={member_id:'coordinator',model:'gpt',phase:'synthesis',content:'Actual synthesis'};
  assert.equal(deriveArtifacts(job)[0].title,'统筹模型综合结论');
});
test('historical generated Muse output retains text but cannot masquerade as verified native video', () => {
  const artifacts=deriveArtifacts({id:'old',mode:'muse_task',status:'complete',harness_generated:true,response:'Retained old draft',video_url:'/muse-media/other.mp4'});
  assert.equal(artifacts[0].status,'partial'); assert.equal(artifacts[0].video_url,null);
  assert.match(artifacts[0].source,/未验证/);
});

test('role recipes bind only connected models and create editable typed teams', () => {
  for (const id of ['article', 'video', 'code']) {
    const team = recipeTeam(id, catalog);
    assert.deepEqual(teamIssues(team, catalog, {allowRepeatedModels: true, allowExecutableMembers: true}), []);
    assert.equal(team.id, undefined);
  }
  assert.equal(recipeTeam('video', catalog).members[1].kind, 'muse');
  assert.equal(recipeTeam('code', catalog).members[1].kind, 'codex');
  assert.equal(recipeTeam('article', []).members[0].model, '');
  assert.ok(teamIssues(recipeTeam('article', []), []).length);
  assert.equal(recipeTeam('unknown', catalog), null);
  assert.equal(recipeTeam('article', [{id: 'one'}]).members[1].model, 'one');
});
