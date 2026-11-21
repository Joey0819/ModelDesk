import { API_BASE, apiFetch } from './api-base';

async function request(path = '', method = 'GET', body, options = {}) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}/api${path}`, {
      method,
      signal: options.signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new Error('飞书连接服务暂时无法访问，请检查运行 ModelDesk 的这台电脑上的服务。');
  }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Preserve the failure. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    const error = new Error(typeof detail === 'string' ? detail : `飞书请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
export const feishuApi = {
  get: () => request('/feishu'),
  save: (body) => request('/feishu', 'PUT', body),
  test: () => request('/feishu/test', 'POST'),
  chats: (pageToken) => request(`/feishu/chats${pageToken ? `?page_token=${encodeURIComponent(pageToken)}` : ''}`),
  rooms: () => request('/feishu/rooms'),
  roomAccess: (roomId, projectId, signal) => request(`/groups/${encodeURIComponent(roomId)}/feishu${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`, 'GET', undefined, { signal }),
  enableAllGroups: (roomId, chatId, projectId) => request(`/feishu/rooms/${encodeURIComponent(roomId)}/binding/${encodeURIComponent(chatId)}/allow-all-groups`, 'PUT', { project_id: projectId || null }),
  saveRoomBinding: (roomId, body) => request(`/feishu/rooms/${encodeURIComponent(roomId)}/binding`, 'PUT', body),
  removeRoomBinding: (roomId, chatId, projectId) => request(`/feishu/rooms/${encodeURIComponent(roomId)}/binding/${encodeURIComponent(chatId)}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`, 'DELETE'),
};
