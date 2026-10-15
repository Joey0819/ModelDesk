export function temporaryMembers(models) {
  return models.map((model, index) => ({ id: `member_${index + 1}`, name: `助手 ${index + 1}`, model, role: index === 0 ? '研究任务，提出有依据的方案。' : index === 1 ? '独立核查依据、指出遗漏并完善交付。' : '补充不同视角，检查执行细节。' }));
}

export const MEMBER_KINDS = { model: 'LLM 模型', codex: 'Codex Agent', claude: 'Claude Code Agent', muse: 'Muse 应用 Agent' };
export const memberKind = (member) => member?.kind || 'model';
export const memberKindLabel = (member) => MEMBER_KINDS[memberKind(member)] || '未知类型';

export function appendTeamMember(team, catalog = [], kind = 'model') {
  const draft = structuredClone(team);
  if (draft.members.length >= 4) return draft;
  if (!Object.hasOwn(MEMBER_KINDS, kind)) return draft;
  const model = catalog.find((item) => item.verified && !draft.members.some((member) => member.model === item.id)) || catalog.find((item) => item.verified) || catalog[0];
  let index = 1;
  while (draft.members.some((member) => member.id === `member_${index}`)) index += 1;
  const defaults = {
    model: { name: `助手 ${index}`, model: model?.id || draft.members.find((member) => member.model)?.model || '', role: '' },
    codex: { name: 'Codex 工程师', model: null, role: '在隔离项目副本中实现任务、运行测试，并报告实际改动与验证结果。' },
    claude: { name: 'Claude Code 工程师', model: null, role: '在受限项目副本中检查和修改代码，保留实际执行结果与能力限制。' },
    muse: { name: 'Muse 创意助手', model: null, role: '使用本机 Muse 应用处理明确交付的任务，并返回原始回复与实际成果。' },
  };
  draft.members.push({ id: `member_${index}`, kind, ...defaults[kind] });
  return draft;
}

export function teamIssues(team, catalog = [], { allowRepeatedModels = false, allowExecutableMembers = false } = {}) {
  if (!team) return [];
  const errors = [];
  if (!team.name?.trim()) errors.push('请填写协作组名称。');
  const members = team.members || [];
  if (members.length < 2 || members.length > 4) errors.push('每个协作组需要 2–4 名成员。');
  const llms = members.filter((member) => memberKind(member) === 'model');
  if (members.some((member) => !member.name?.trim() || !member.role?.trim() || !MEMBER_KINDS[memberKind(member)] || (memberKind(member) === 'model' && !member.model))) errors.push('每名成员需要名称、类型与职责；LLM 成员还需要选择模型。');
  if (!allowExecutableMembers && members.some((member) => memberKind(member) !== 'model')) errors.push('可执行 Agent 请在项目群聊中使用；此协作流程需要模型成员。');
  if (!llms.length && !team.coordinator_model) errors.push('请添加一名 LLM 成员，或选择一个统筹模型。');
  if (!allowRepeatedModels && new Set(llms.map((member) => member.model)).size !== llms.length) errors.push('协作任务中的模型不能重复；同一模型的多个角色请使用项目群聊。');
  if (new Set([...llms.map((member) => member.model), ...(team.coordinator_model ? [team.coordinator_model] : [])]).size > 4) errors.push('LLM 成员与统筹合计最多使用四个不同模型。');
  const unavailable = llms.filter((member) => !catalog.some((model) => model.id === member.model));
  if (unavailable.length) errors.push(`当前未接入：${unavailable.map((member) => member.model || '未选择模型').join('、')}。请更换模型或接入对应账户。`);
  if (team.coordinator_model && !catalog.some((model) => model.id === team.coordinator_model)) errors.push(`当前未接入：统筹模型 ${team.coordinator_model}。请连接后再开始群聊。`);
  return errors;
}

export function teamRequest(team, projectId) {
  return { name: team.name.trim(), description: team.description || '', coordinator_model: team.coordinator_model || null, project_id: projectId || null, members: team.members.map((member) => ({ id: member.id, name: member.name.trim(), kind: memberKind(member), model: memberKind(member) === 'muse' ? null : member.model?.trim() || null, role: member.role.trim() })) };
}

export function runTeamSelection(team, models = []) {
  if (team?.id) return { team_id: team.id };
  return team?.members?.length ? { members: structuredClone(team.members) } : models.length >= 2 ? { members: temporaryMembers(models) } : {};
}

export function copyRunTeam(run) {
  const snapshot = run.team_snapshot;
  if (snapshot?.members?.length) return { name: `${snapshot.name || '历史协作组'}的副本`, description: snapshot.description || '', project_id: run.project_id || null, members: structuredClone(snapshot.members) };
  return run.request?.members?.length ? { name: '临时协作组', members: structuredClone(run.request.members) } : null;
}
