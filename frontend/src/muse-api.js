import { API_BASE, apiFetch } from './api-base';
const scope = (projectId, extra = {}) => new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), ...extra }).toString();
async function request(path, method = 'GET', body) {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/muse${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('Muse 本机桥接服务无法连接，请检查 ModelDesk 本地服务。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Keep HTTP failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `Muse 请求失败（${response.status}）`);
  }
  return response.json();
}
export const museApi = {
  status: () => request('/status'),
  list: (projectId, roomId) => request(`/tasks?${scope(projectId, roomId ? { room_id: roomId } : {})}`),
  create: (body) => request('/tasks', 'POST', body),
  cancel: (id, projectId) => request(`/tasks/${encodeURIComponent(id)}/cancel?${scope(projectId)}`, 'POST'),
  observe: (id, projectId) => request(`/tasks/${encodeURIComponent(id)}/observe?${scope(projectId)}`, 'POST'),
  exportUrl: (id, projectId, format = 'md') => `${API_BASE}/api/muse/tasks/${encodeURIComponent(id)}/export?${scope(projectId, { format })}`,
};
