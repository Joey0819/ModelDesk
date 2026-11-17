import { API_BASE, apiFetch } from './api-base';

async function request(path = '', method = 'GET', body) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}/api/todo${path}`, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('任务看板服务暂时无法访问，请检查 ModelDesk 后端是否正在运行。');
  }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve the failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `任务看板请求失败（${response.status}）`);
  }
  return response.json();
}

export const todoApi = {
  list: (archived = false) => request(archived ? '?archived=true' : ''),
  create: (fields) => request('', 'POST', fields),
  update: (id, fields) => request(`/${encodeURIComponent(id)}`, 'PUT', fields),
  remove: (id) => request(`/${encodeURIComponent(id)}`, 'DELETE'),
  reorder: (ids, status, positionStart = 0) =>
    request('/reorder', 'POST', { ids, status, position_start: positionStart }),
};
