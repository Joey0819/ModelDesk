import { API_BASE, apiFetch, apiResponseError } from './api-base';

const TODO_PATH = '/api/todo';

async function request(path, method = 'GET', body, signal) {
  let response;
  try {
    response = await apiFetch(`${API_BASE}${path}`, {
      method,
      signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('任务看板服务暂时无法访问，请检查 ModelDesk 后端是否正在运行。');
  }
  if (!response.ok) throw await apiResponseError(response, `任务看板请求失败（${response.status}）`);
  return response.json();
}

export const todoApi = {
  list: (archived = false, signal) => request(archived ? `${TODO_PATH}?archived=true` : TODO_PATH, 'GET', undefined, signal),
  create: (fields) => request(TODO_PATH, 'POST', fields),
  update: (id, fields) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'PUT', fields),
  remove: (id) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'DELETE'),
  move: (placement) => request(`${TODO_PATH}/move`, 'POST', placement),
  followSource: (id) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'PUT', { status_mode: 'source' }),
  reassignFleetTask: (id) => request(`/api/fleet/jobs/${encodeURIComponent(id)}/reassign`, 'POST'),
};
