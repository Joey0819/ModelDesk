/** Rebuild streamed output from durable events without applying a replay twice. */
export function applyEvent(job, event) {
  if (!job || job.id !== event.job_id) return job;
  const events = job.events || [];
  if (event.seq && events.some((item) => item.seq === event.seq)) return job;
  const next = { ...job, events: [...events, event], updated_at: event.created_at || job.updated_at };
  if (event.type === 'started') next.status = 'running';
  if (event.type === 'delta') {
    const responses = [...(job.result?.responses || [])];
    const index = responses.findIndex((item) => item.model === event.model);
    if (index < 0) responses.push({ model: event.model, text: event.text });
    else responses[index] = { ...responses[index], text: (responses[index].text || '') + event.text };
    next.result = { ...job.result, responses };
  }
  if (event.type === 'stage') {
    const keys = { responses: 'stage1', rankings: 'stage2', synthesis: 'stage3' };
    next.result = { ...job.result, [keys[event.stage] || event.stage]: event.data, ...(event.metadata ? { metadata: event.metadata } : {}) };
  }
  if (event.type === 'message') {
    const message = { model: event.model, round: event.round, content: event.content, ...(event.error !== undefined ? { error: event.error } : {}), ...(event.incomplete !== undefined ? { incomplete: event.incomplete } : {}) };
    next.result = { ...job.result, partial_failure: Boolean(job.result?.partial_failure || event.error || event.incomplete), messages: [...(job.result?.messages || []), message] };
  }
  if (event.type === 'framework_message') {
    next.result = { ...job.result, partial: true, messages: [...(job.result?.messages || []), { role: event.role, model: event.model, round: event.round, content: event.content }] };
  }
  if (event.type === 'framework_task') {
    const task = { id: event.id, name: event.name, agent: event.agent, description: event.description, context: event.context, output: event.output };
    const tasks = [...(job.result?.tasks || [])];
    const index = tasks.findIndex((item) => item.id === task.id);
    if (index < 0) tasks.push(task); else tasks[index] = task;
    next.result = { ...job.result, partial: true, tasks };
  }
  if (event.type === 'complete') { next.status = 'complete'; next.result = event.result; }
  if (event.type === 'error') { next.status = 'error'; next.error = event.message; }
  if (event.type === 'cancelled') next.status = 'cancelled';
  return next;
}

export function hydrateJob(saved) {
  const replayed = (saved.events || []).reduce(applyEvent, { ...saved, events: [], result: null });
  return { ...replayed, status: saved.status, result: saved.result ?? replayed.result, error: saved.error || replayed.error };
}
