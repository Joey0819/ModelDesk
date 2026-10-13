import { API_BASE, apiFetch } from './api-base';
import { taskScope } from './group-task-state';
async function request(path, method = 'GET', body) {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('Agent 服务无法连接，请检查工作台连接。'); }
  if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch { /* Preserve HTTP status. */ } if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；'); throw new Error(typeof detail === 'string' ? detail : `请求失败（${response.status}）`); }
  return response.json();
}
export const groupTaskApi = {
  engines: () => request('group-tasks/engines'),
  exportUrl: (id, projectId, roomId, format = 'md') => `${API_BASE}/api/group-tasks/${encodeURIComponent(id)}/export?${taskScope(projectId, roomId)}&format=${encodeURIComponent(format)}`,
  list: (projectId, roomId) => request(`group-tasks?${taskScope(projectId, roomId)}`),
  get: (id, projectId, roomId) => request(`group-tasks/${encodeURIComponent(id)}?${taskScope(projectId, roomId)}`),
  create: (body) => request('group-tasks', 'POST', body),
  cancel: (id, projectId, roomId) => request(`group-tasks/${encodeURIComponent(id)}/cancel?${taskScope(projectId, roomId)}`, 'POST'),
  approvals: (projectId, roomId) => request(`group-approvals?${taskScope(projectId, roomId)}`),
  requestApproval: (body) => request('group-approvals', 'POST', body),
  decide: (id, body) => request(`group-approvals/${encodeURIComponent(id)}/decision`, 'POST', body),
};
