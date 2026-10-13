import { API_BASE, apiFetch } from './api-base';
async function request(path = '', method = 'GET', body) {
  let response;
  try { response = await apiFetch(`${API_BASE}/api/model-connections${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); }
  catch { throw new Error('连接设置服务暂不可用，请检查 ModelDesk 服务后重试。'); }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* Do not include raw server output in credential forms. */ }
    throw new Error(typeof detail === 'string' ? detail : `连接设置失败（HTTP ${response.status}）`);
  }
  return response.json();
}
export const modelConnectionApi = {
  list: () => request(),
  create: (body) => request('', 'POST', body),
  update: (id, body) => request(`/${encodeURIComponent(id)}`, 'PUT', body),
  remove: (id) => request(`/${encodeURIComponent(id)}`, 'DELETE'),
  discover: (id) => request(`/${encodeURIComponent(id)}/discover`, 'POST'),
  test: (id, model) => request(`/${encodeURIComponent(id)}/test`, 'POST', { model }),
};
