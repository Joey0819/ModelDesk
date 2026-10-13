export function temporaryMembers(models) {
  return models.map((model, index) => ({ id: `member_${index + 1}`, name: `助手 ${index + 1}`, model, role: index === 0 ? '研究任务，提出有依据的方案。' : index === 1 ? '独立核查依据、指出遗漏并完善交付。' : '补充不同视角，检查执行细节。' }));
}

export function teamIssues(team, catalog = []) {
  if (!team) return [];
  const errors = [];
  if (!team.name?.trim()) errors.push('请填写协作组名称。');
  const members = team.members || [];
  if (members.length < 2 || members.length > 4) errors.push('每个协作组需要 2–4 名模型成员。');
  if (members.some((member) => !member.name?.trim() || !member.model || !member.role?.trim())) errors.push('每名成员需要名称、模型与职责。');
  if (new Set(members.map((member) => member.model)).size !== members.length) errors.push('一个协作组中的模型不能重复。');
  const unavailable = members.filter((member) => !catalog.some((model) => model.id === member.model));
  if (unavailable.length) errors.push(`当前未接入：${unavailable.map((member) => member.model || '未选择模型').join('、')}。请更换模型或接入对应账户。`);
  return errors;
}

export function teamRequest(team, projectId) {
  return { name: team.name.trim(), description: team.description || '', project_id: projectId || null, members: team.members.map(({ id, name, model, role }) => ({ id, name: name.trim(), model, role: role.trim() })) };
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
