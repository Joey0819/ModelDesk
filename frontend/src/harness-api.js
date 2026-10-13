import { API_BASE, apiFetch } from './api-base';

async function request(path, method = 'GET', body) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}/api/harness${path}`, {
      method,
      ...(body !== undefined ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
  } catch { throw new Error('协作任务服务无法连接。请检查本地服务后重试。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve the HTTP status when no JSON was returned. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `协作任务请求失败（${response.status}）`);
  }
  return response.json();
}

export const harnessApi = {
  templates: () => request('/templates'),
  teams: (projectId) => request(`/teams${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  createTeam: (body) => request('/teams', 'POST', body),
  updateTeam: (id, body) => request(`/teams/${encodeURIComponent(id)}`, 'PUT', body),
  archiveTeam: (id, projectId) => request(`/teams/${encodeURIComponent(id)}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`, 'DELETE'),
  list: (projectId) => request(`/runs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  get: (id) => request(`/runs/${encodeURIComponent(id)}`),
  create: (body) => request('/runs', 'POST', body),
  cancel: (id) => request(`/runs/${encodeURIComponent(id)}/cancel`, 'POST'),
  resume: (id) => request(`/runs/${encodeURIComponent(id)}/resume`, 'POST', { acknowledge_replay: true }),
  approve: (id) => request(`/runs/${encodeURIComponent(id)}/approve`, 'POST'),
  exportUrl: (id, format = 'md') => `${API_BASE}/api/harness/runs/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`,
};
