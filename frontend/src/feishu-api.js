import { API_BASE, apiFetch } from './api-base';

async function request(path = '', method = 'GET', body) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}/api/feishu${path}`, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch { throw new Error('飞书连接服务暂时无法访问，请检查 Mac 上的 ModelDesk 服务。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve the failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `飞书请求失败（${response.status}）`);
  }
  return response.json();
}
export const feishuApi = {
  get: () => request(),
  save: (body) => request('', 'PUT', body),
  test: () => request('/test', 'POST'),
  chats: (pageToken) => request(`/chats${pageToken ? `?page_token=${encodeURIComponent(pageToken)}` : ''}`),
};
