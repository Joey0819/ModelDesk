import { API_BASE, apiFetch, apiResponseError } from './api-base';
import { newGroupRequestId } from './group-state';

const TODO_PATH = '/api/todo';
const pendingCards = new Map();

async function createCard(fields) {
  if (!fields.room_id || !fields.tags?.length) return request(TODO_PATH, 'POST', fields);
  // A lost response to a tagged creation may already have started a group routine.
  const body = JSON.stringify(fields);
  const digest = globalThis.crypto.subtle && await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
  const key = digest && 'modeldesk.todo.create:' + Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
  let id = pendingCards.get(body);
  try { if (key) id ||= sessionStorage.getItem(key); } catch { /* In-memory retries still share an ID. */ }
  id ||= newGroupRequestId();
  pendingCards.set(body, id);
  try { if (key) sessionStorage.setItem(key, id); } catch { /* Storage may be unavailable. */ }
  const card = await request(TODO_PATH, 'POST', { ...fields, client_request_id: id });
  pendingCards.delete(body);
  try { if (key) sessionStorage.removeItem(key); } catch { /* The server receipt remains valid. */ }
  return card;
}

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
  create: createCard,
  update: (id, fields) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'PUT', fields),
  remove: (id) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'DELETE'),
  move: (placement) => request(`${TODO_PATH}/move`, 'POST', placement),
  followSource: (id) => request(`${TODO_PATH}/${encodeURIComponent(id)}`, 'PUT', { status_mode: 'source' }),
  reassignFleetTask: (id) => request(`/api/fleet/jobs/${encodeURIComponent(id)}/reassign`, 'POST'),
};
