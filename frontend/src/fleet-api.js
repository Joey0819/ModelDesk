import { API_BASE, apiFetch } from './api-base';

async function request(path = '', method = 'GET', body) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}/api/fleet${path}`, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('节点舰队服务暂时无法访问，请检查 ModelDesk 后端是否正在运行。');
  }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve the failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `节点舰队请求失败（${response.status}）`);
  }
  return response.json();
}

export const fleetApi = {
  list: () => request(''),
  createEnrollToken: () => request('/enroll-tokens', 'POST', {}),
  removeNode: (nodeId) => request(`/${encodeURIComponent(nodeId)}`, 'DELETE'),
};
