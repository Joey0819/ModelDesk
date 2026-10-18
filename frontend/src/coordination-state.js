const terminal = new Set(['complete', 'error', 'cancelled', 'interrupted']);
export function coordinationState(job) {
  const delivery = job?.result?.delivery;
  const workflow = delivery?.workflow;
  if (workflow?.schema_version !== 1 || workflow.run_id !== job.id) return null;
  const requirements = Array.isArray(delivery.task_spec?.requirements) ? delivery.task_spec.requirements : [];
  const checks = delivery.validation?.review?.checks || [];
  const actual = delivery.validation?.mechanical_checks || [];
  return {
    workflow,
    requirements: requirements.map((item) => ({ ...item,
      state: actual.find((check) => check.requirement_id === item.id)?.met === false || checks.find((check) => check.requirement_id === item.id)?.met === false ? 'missing'
        : actual.find((check) => check.requirement_id === item.id)?.met === true && checks.find((check) => check.requirement_id === item.id)?.met === true ? 'met' : 'pending',
      reason: checks.find((check) => check.requirement_id === item.id)?.reason || '',
    })),
    steps: Array.isArray(delivery.steps) ? delivery.steps : [],
    versions: Array.isArray(delivery.versions) ? delivery.versions.filter((item) => Number.isInteger(item.number) && typeof item.content === 'string') : [],
    canContinue: terminal.has(job.status) && delivery.status !== 'complete' && workflow.resume_count < 2 && workflow.elapsed_seconds < 1200,
    reviewLabel: delivery.validation?.status === 'passed' ? '交付检查通过' : delivery.validation?.status === 'needs_revision' ? '仍有要求需要处理' : terminal.has(job.status) ? '交付检查尚未完成' : '正在执行与检查',
  };
}
