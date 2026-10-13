import { API_BASE, apiFetch } from './api-base';
const scope = (projectId, extra = {}) => new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), ...extra }).toString();
async function request(path, method = 'GET', body) {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/groups${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('项目群聊服务无法连接，请检查本地服务后重试。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve HTTP failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `群聊请求失败（${response.status}）`);
  }
  return response.json();
}
export const groupApi = {
  list: (projectId) => request(`?${scope(projectId)}`),
  create: (body) => request('', 'POST', body),
  get: (id, projectId) => request(`/${encodeURIComponent(id)}?${scope(projectId)}`),
  updateTeam: (id, projectId, body) => request(`/${encodeURIComponent(id)}/team?${scope(projectId)}`, 'PUT', body),
  messages: (id, projectId, before) => request(`/${encodeURIComponent(id)}/messages?${scope(projectId, { limit: '20', ...(before ? { before } : {}) })}`),
  send: (id, projectId, body) => request(`/${encodeURIComponent(id)}/messages?${scope(projectId)}`, 'POST', body),
};
