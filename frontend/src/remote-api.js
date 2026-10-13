import { API_BASE, apiFetch } from './api-base';
async function request(path, method = 'GET', body, gateway = false) {
  let response;
  try { response = await (gateway ? fetch : apiFetch)(`${gateway ? '' : API_BASE}${path}`, { method, cache: 'no-store', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('远程连接暂时无法访问，请确认 Mac 在线且手机已连接同一 Tailscale 网络。'); }
  if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch { /* Preserve HTTP status. */ } throw new Error(typeof detail === 'string' ? detail : `连接请求失败（${response.status}）`); }
  return response.json();
}
export const remoteApi = {
  status: () => request('/api/remote/status'),
  start: () => request('/api/remote/start', 'POST', {}),
  stop: () => request('/api/remote/stop', 'POST', {}),
  createPairing: (label) => request('/api/remote/pairings', 'POST', { label }),
  revoke: (id) => request(`/api/remote/sessions/${encodeURIComponent(id)}/revoke`, 'POST'),
  revokeAll: () => request('/api/remote/revoke-all', 'POST'),
  session: () => request('/remote/session', 'GET', undefined, true),
  pair: (code, label) => request('/remote/pair', 'POST', { code, label }, true),
  logout: () => request('/remote/logout', 'POST', {}, true),
};
