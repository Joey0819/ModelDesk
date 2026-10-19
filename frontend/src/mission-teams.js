export function temporaryMembers(models) {
  return models.map((model, index) => ({ id: `member_${index + 1}`, name: `助手 ${index + 1}`, model, role: index === 0 ? '研究任务，提出有依据的方案。' : index === 1 ? '独立核查依据、指出遗漏并完善交付。' : '补充不同视角，检查执行细节。' }));
}

export const MEMBER_KINDS = { model: 'LLM 模型', image: '绘图模型', codex: 'Codex Agent', claude: 'Claude Code Agent', muse: 'Muse 应用 Agent' };
export const isImageModel = (id = '') => /^(gpt-image(?:-|$)|grok.*image|gemini.*image)/i.test(id.split(':').at(-1).split('/').at(-1));
export const memberKind = (member) => (!member?.kind || member.kind === 'model') && isImageModel(member?.model) ? 'image' : member?.kind || 'model';
export const memberKindLabel = (member) => MEMBER_KINDS[memberKind(member)] || '未知类型';

export const TEAM_RECIPES = [
  { id: 'article', name: '文章与配图', summary: '作者完成正文，编辑核查，平台按需求配图' },
  { id: 'video', name: '视频创作', summary: '编剧交付分镜，Muse 制作，审片员检查' },
  { id: 'code', name: '代码交付', summary: '分析需求，Codex 实现测试，审查实际改动' },
];

export function recipeTeam(id, catalog = []) {
  const textCatalog = catalog.filter((item) => !isImageModel(item.id));
  const models = [...textCatalog.filter((item) => item.verified), ...textCatalog.filter((item) => !item.verified)];
  const first = models[0]?.id || '';
  const second = models.find((item) => item.id !== first)?.id || first;
  const recipe = TEAM_RECIPES.find((item) => item.id === id);
  if (!recipe) return null;
  const role = (key, name, model, duty, kind = 'model') => ({ id: key, name, model, role: duty, kind });
  const members = {
    article: [role('writer', '作者', first, '根据用户目标和项目资料写出完整成品正文。明确未核实信息；需要配图时给出与正文一致的画面说明。'),
      role('editor', '审稿编辑', second, '检查作者的实际初稿，指出事实、结构和用户要求的遗漏，提供可以直接采用的修订内容。')],
    video: [role('writer', '编剧', first, '根据用户要求完成可制作的脚本、镜头说明与字幕；先把完整分镜交给 Muse 创作。'),
      role('creator', 'Muse 创作', null, '接收编剧的实际分镜，在 Muse 制作所需视频和图片，返回实际媒体文件。不要仅提供提示词或制作建议。', 'muse'),
      role('editor', '审片员', second, '接收 Muse 的实际交付，核对脚本与要求。根据提供的画面证据检查内容；区分抽帧检查与全片音视频验收，不虚构已看过的内容。')],
    code: [role('planner', '需求分析', first, '将用户要求整理为实现与测试条件，交给工程师执行。'),
      role('builder', 'Codex 工程师', null, '在隔离项目副本中完成代码、执行必要测试，并返回实际改动与命令证据。', 'codex'),
      role('reviewer', '代码审查', second, '检查工程师实际输出、改动和测试记录，指出未解决问题。合并前保留用户审批。')],
  }[id];
  return { name: recipe.name, description: recipe.summary, coordinator_model: first || null, members };
}

export function appendTeamMember(team, catalog = [], kind = 'model') {
  const draft = structuredClone(team);
  if (draft.members.length >= 4) return draft;
  if (!Object.hasOwn(MEMBER_KINDS, kind)) return draft;
  catalog = catalog.filter((item) => !isImageModel(item.id) || kind === 'image');
  const model = catalog.find((item) => item.verified && !draft.members.some((member) => member.model === item.id)) || catalog.find((item) => item.verified) || catalog[0];
  let index = 1;
  while (draft.members.some((member) => member.id === `member_${index}`)) index += 1;
  const defaults = {
    model: { name: `助手 ${index}`, model: model?.id || draft.members.find((member) => member.model)?.model || '', role: '' },
    image: { name: '绘图者', model: catalog.find((item) => isImageModel(item.id))?.id || '', role: '根据上游审核后的分镜生成真实图片，交给后续制片角色。' },
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
  if (members.some((member) => !member.name?.trim() || !member.role?.trim() || !MEMBER_KINDS[memberKind(member)] || (['model', 'image'].includes(memberKind(member)) && !member.model))) errors.push('每名成员需要名称、类型与职责；LLM 成员还需要选择模型。');
  if (!allowExecutableMembers && members.some((member) => memberKind(member) !== 'model')) errors.push('可执行 Agent 请在项目群聊中使用；此协作流程需要模型成员。');
  if (!llms.length && !team.coordinator_model) errors.push('请添加一名 LLM 成员，或选择一个统筹模型。');
  if (!allowRepeatedModels && new Set(llms.map((member) => member.model)).size !== llms.length) errors.push('协作任务中的模型不能重复；同一模型的多个角色请使用项目群聊。');
  if (new Set([...llms.map((member) => member.model), ...(team.coordinator_model ? [team.coordinator_model] : [])]).size > 4) errors.push('LLM 成员与统筹合计最多使用四个不同模型。');
  if (isImageModel(team.coordinator_model || "")) errors.push("统筹模型需要文字推理能力，请将生图模型配置为绘图成员。");
  if (!allowExecutableMembers && members.some((member) => member.skill_ids?.length)) errors.push("Skills 请在项目群中使用；流程模板暂不支持。");
  const unavailable = members.filter((member) => ["model", "image"].includes(memberKind(member))).filter((member) => !catalog.some((model) => model.id === member.model));
  if (unavailable.length) errors.push(`当前未接入：${unavailable.map((member) => member.model || '未选择模型').join('、')}。请更换模型或接入对应账户。`);
  if (team.coordinator_model && !catalog.some((model) => model.id === team.coordinator_model)) errors.push(`当前未接入：统筹模型 ${team.coordinator_model}。请连接后再开始群聊。`);
  return errors;
}

export function teamRequest(team, projectId) {
  return { name: team.name.trim(), description: team.description || '', coordinator_model: team.coordinator_model || null, project_id: projectId || null, members: team.members.map((member) => ({ id: member.id, name: member.name.trim(), kind: memberKind(member), model: memberKind(member) === 'muse' ? null : member.model?.trim() || null, role: member.role.trim(), ...(member.skill_ids?.length ? { skill_ids: member.skill_ids } : {}) })) };
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
