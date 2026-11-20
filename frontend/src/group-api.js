import { API_BASE, apiFetch } from './api-base';
const scope = (projectId, extra = {}) => new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), ...extra }).toString();
const routinePath = (roomId, routineId, action = '') => `/${encodeURIComponent(roomId)}/routines${routineId ? `/${encodeURIComponent(routineId)}${action}` : action}`;
async function request(path, method = 'GET', body) {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/groups${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('项目群聊服务无法连接，请检查本地服务后重试。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve HTTP failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw Object.assign(new Error(typeof detail === 'string' ? detail : `群聊请求失败（${response.status}）`), { status: response.status });
  }
  return response.json();
}
export const groupApi = {
  catalog: () => request('/catalog'),
  list: (projectId) => request(`?${scope(projectId)}`),
  create: (body) => request('', 'POST', body),
  get: (id, projectId) => request(`/${encodeURIComponent(id)}?${scope(projectId)}`),
  updateTeam: (id, projectId, body) => request(`/${encodeURIComponent(id)}/team?${scope(projectId)}`, 'PUT', body),
  messages: (id, projectId, before) => request(`/${encodeURIComponent(id)}/messages?${scope(projectId, { limit: '20', ...(before ? { before } : {}) })}`),
  send: (id, projectId, body) => request(`/${encodeURIComponent(id)}/messages?${scope(projectId)}`, 'POST', body),
  routines: (roomId, projectId) => request(`${routinePath(roomId)}?${scope(projectId)}`),
  createRoutine: (roomId, projectId, body) => request(`${routinePath(roomId)}?${scope(projectId)}`, 'POST', body),
  updateRoutine: (roomId, projectId, id, body) => request(`${routinePath(roomId, id)}?${scope(projectId)}`, 'PUT', body),
  setRoutineEnabled: (roomId, projectId, id, body) => request(`${routinePath(roomId, id, '/enabled')}?${scope(projectId)}`, 'POST', body),
  runRoutine: (roomId, projectId, id, body) => request(`${routinePath(roomId, id, '/run')}?${scope(projectId)}`, 'POST', body),
  removeRoutine: (roomId, projectId, id, revision) => request(`${routinePath(roomId, id)}?${scope(projectId, { expected_revision: revision })}`, 'DELETE'),
  designRoutine: (roomId, projectId, body) => request(`${routinePath(roomId, null, '/design')}?${scope(projectId)}`, 'POST', body),
  walkthroughRoutine: (roomId, projectId, body) => request(`${routinePath(roomId, null, '/walkthrough')}?${scope(projectId)}`, 'POST', body),
  routineRevisions: (roomId, projectId, id) => request(`${routinePath(roomId, id, '/revisions')}?${scope(projectId)}`),
  recordRoutineFeedback: (roomId, projectId, id, runId, body) => request(`${routinePath(roomId, id, `/runs/${encodeURIComponent(runId)}/feedback`)}?${scope(projectId)}`, 'POST', body),
  setRoutineCaseState: (roomId, projectId, id, runId, body) => request(`${routinePath(roomId, id, `/runs/${encodeURIComponent(runId)}/case`)}?${scope(projectId)}`, 'POST', body),
  reflectRoutine: (roomId, projectId, id) => request(`${routinePath(roomId, id, '/reflect')}?${scope(projectId)}`, 'POST', {}),
  applyRoutineProposal: (roomId, projectId, id, proposalId, body) => request(`${routinePath(roomId, id, `/proposals/${encodeURIComponent(proposalId)}/apply`)}?${scope(projectId)}`, 'POST', body),
  dismissRoutineProposal: (roomId, projectId, id, proposalId) => request(`${routinePath(roomId, id, `/proposals/${encodeURIComponent(proposalId)}/dismiss`)}?${scope(projectId)}`, 'POST', {}),
};
