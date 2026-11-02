import { API_BASE, apiFetch } from './api-base';

const scope = (projectId) => projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
async function request(path, method = 'GET') {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/autonomy${path}`, { method }); }
  catch { throw new Error('后台任务服务暂时无法连接。执行结果仍以服务端保存的任务记录为准。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Keep the HTTP failure. */ }
    throw new Error(typeof detail === 'string' ? detail : `后台任务请求失败（${response.status}）`);
  }
  return response.json();
}
export const autonomyApi = {
  list: (projectId) => request(`/tasks${scope(projectId)}`),
  read: (id, projectId) => request(`/tasks/${encodeURIComponent(id)}/read${scope(projectId)}`, 'POST'),
  pause: (id, projectId) => request(`/tasks/${encodeURIComponent(id)}/pause${scope(projectId)}`, 'POST'),
};
