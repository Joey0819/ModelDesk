export function defaultWorkflow(mode, models) {
  const agents = [
    { id: 'researcher', role: '研究员', goal: '围绕任务提出清晰、可执行、有证据依据的方案。', backstory: '善于分析问题，区分事实、假设与尚待验证的内容。', model: models[0] || '', tools: [] },
    { id: 'reviewer', role: '审稿人', goal: '检查方案中的遗漏、错误与证据缺口，提出具体修正。', backstory: '重视可靠依据、可复现方法与实际风险。', model: models[1] || models[0] || '', tools: [] },
  ];
  return { agents, tasks: mode === 'crewai' ? [
    { id: 'draft', description: '分析用户提出的任务，起草完整方案，并标明关键假设。', expected_output: '一份结构清晰、可执行的初稿。', agent: 'researcher', context: [] },
    { id: 'review', description: '复核前一步初稿，修正不可靠或遗漏的内容，交付最终结果。', expected_output: '经核查的最终方案，以及仍需验证的内容。', agent: 'reviewer', context: ['draft'] },
  ] : [], process: 'sequential', manager_model: null, max_iter: 3 };
}

export function workflowErrors(workflow, mode, models) {
  const errors = [];
  const agents = workflow?.agents || [];
  const tasks = workflow?.tasks || [];
  if (agents.length < 1 || agents.length > 4 || (mode === 'camel' && agents.length !== 2)) errors.push(mode === 'camel' ? 'CAMEL 需要两个角色。' : '团队需要 1–4 个角色。');
  const agentIds = new Set();
  for (const agent of agents) {
    if (agentIds.has(agent.id)) errors.push('角色标识不能重复。');
    agentIds.add(agent.id);
    if (!agent.role?.trim() || !agent.goal?.trim()) errors.push('请填写每个角色的名称与目标。');
    if (!models.includes(agent.model)) errors.push(`${agent.role || '角色'}的模型必须来自当前参与模型。`);
  }
  if (mode === 'crewai' && (tasks.length < 1 || tasks.length > 6)) errors.push('CrewAI 需要 1–6 个任务步骤。');
  const prior = new Set();
  for (const task of tasks) {
    if (!task.description?.trim() || !task.expected_output?.trim()) errors.push('请填写每个任务的内容与预期输出。');
    if (!agentIds.has(task.agent)) errors.push('请为每个任务分配已有角色。');
    if (prior.has(task.id) || (task.context || []).some((id) => !prior.has(id))) errors.push('任务上下文只能引用此前步骤。');
    prior.add(task.id);
  }
  if (workflow?.process === 'hierarchical' && !models.includes(workflow.manager_model)) errors.push('分层协作需要选择参与模型中的管理者。');
  return [...new Set(errors)];
}

export function orderTasks(tasks) {
  const prior = new Set();
  return tasks.map((task) => {
    const next = { ...task, context: [...new Set((task.context || []).filter((id) => prior.has(id)))] };
    prior.add(task.id);
    return next;
  });
}

export function moveTask(tasks, index, offset) {
  const destination = index + offset;
  if (destination < 0 || destination >= tasks.length) return tasks;
  const reordered = [...tasks];
  [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
  return orderTasks(reordered);
}
