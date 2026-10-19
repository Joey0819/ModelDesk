import { API_BASE, apiFetch } from './api-base';
async function call(path = '', body) {
  const response = await apiFetch(`${API_BASE}/api/skills${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Skill 操作失败，请重试。');
  return result;
}
export const skillsApi = { list: () => call(), installLemo: () => call('/lemo-opuscar/install', {}), import: (body) => call('/import', body) };
