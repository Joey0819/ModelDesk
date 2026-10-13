/**
 * API client for the LLM Council backend.
 */

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8001';

async function workbenchRequest(path, options = {}) {
  let response;
  try { response = await fetch(`${API_BASE}/api/workbench${path}`, options); }
  catch { throw new Error('工作台服务无法连接。请启动本地服务后重试。'); }
  if (!response.ok) {
    let detail;
    try { const body = await response.json(); detail = body.detail || body.error; } catch { /* Non-JSON response. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `请求失败（${response.status}）`);
  }
  return response.json();
}

async function platformRequest(path, options = {}) {
  let response;
  try { response = await fetch(`${API_BASE}/api/platform${path}`, options); }
  catch { throw new Error('平台服务无法连接。请检查本地服务状态。'); }
  if (!response.ok) {
    let detail; try { detail = (await response.json()).detail; } catch { /* Non-JSON response. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('；');
    throw new Error(typeof detail === 'string' ? detail : `平台请求失败（${response.status}）`);
  }
  return response.json();
}

const platformJson = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const platformApi = {
  overview: () => platformRequest('/overview'),
  list: (kind, archived = false, scope = {}) => platformRequest(`/${encodeURIComponent(kind)}?${new URLSearchParams({ archived: String(archived), ...(scope.scope ? { scope: scope.scope } : {}), ...(scope.project_id ? { project_id: scope.project_id } : {}) })}`),
  create: (kind, body) => platformRequest(`/${encodeURIComponent(kind)}`, platformJson('POST', body)),
  update: (kind, id, body) => platformRequest(`/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, platformJson('PUT', body)),
  archive: (kind, id) => platformRequest(`/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  restore: (kind, id) => platformRequest(`/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/restore`, { method: 'POST' }),
  runFlow: (id, body) => platformRequest(`/flows/${encodeURIComponent(id)}/run`, platformJson('POST', body)),
  uploadKnowledge: (file, projectId) => { const body = new FormData(); body.append('file', file); if (projectId) body.append('project_id', projectId); return platformRequest('/knowledge/upload', { method: 'POST', body }); },
  search: (kind, body) => platformRequest(`/${encodeURIComponent(kind)}/search`, platformJson('POST', body)),
  tools: () => platformRequest('/tools'),
  invokeTool: (name, body) => platformRequest(`/tools/${encodeURIComponent(name)}/invoke`, platformJson('POST', body)),
  discoverMcp: (id) => platformRequest(`/mcp/${encodeURIComponent(id)}/discover`, { method: 'POST' }),
  callMcp: (id, body) => platformRequest(`/mcp/${encodeURIComponent(id)}/call`, platformJson('POST', body)),
  importData: (body) => platformRequest('/data/import', platformJson('POST', body)),
  exportUrl: () => `${API_BASE}/api/platform/data/export?include_jobs=true`,
  originals: () => platformRequest('/originals'),
  startOriginal: (id) => platformRequest(`/originals/${encodeURIComponent(id)}/start`, { method: 'POST' }),
  sdkTemplates: () => platformRequest('/sdk/templates'),
};

export const workbenchApi = {
  status: () => workbenchRequest('/status'),
  listJobs: (projectId) => workbenchRequest(`/jobs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  getJob: (id) => workbenchRequest(`/jobs/${encodeURIComponent(id)}`),
  createJob: (body) => workbenchRequest('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  cancelJob: (id) => workbenchRequest(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  events: (id, after = 0) => new EventSource(`${API_BASE}/api/workbench/jobs/${encodeURIComponent(id)}/events?after=${after}`),
  exportUrl: (id, format) => `${API_BASE}/api/workbench/jobs/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`,
  listProjects: () => workbenchRequest('/projects'),
  addProject: (body) => workbenchRequest('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  getProject: (id) => workbenchRequest(`/projects/${encodeURIComponent(id)}`),
  updateProject: (id, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  removeProject: (id) => workbenchRequest(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  listAssets: (id) => workbenchRequest(`/projects/${encodeURIComponent(id)}/assets`),
  uploadAssets: (id, files, shared = false) => { const body = new FormData(); files.forEach((item) => body.append('files', item.file || item)); body.append('relative_paths', JSON.stringify(files.map((item) => item.relative_path || item.file?.webkitRelativePath || item.webkitRelativePath || item.file?.name || item.name))); body.append('shared', String(shared)); return workbenchRequest(`/projects/${encodeURIComponent(id)}/assets/batch`, { method: 'POST', body }); },
  updateAsset: (projectId, id, patch) => workbenchRequest(`/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(id)}`, platformJson('PUT', patch)),
  removeAsset: (projectId, id) => workbenchRequest(`/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  assetUrl: (asset) => { const value = asset.content_url || asset.preview_url; if (value) { try { const url = new URL(value, API_BASE); if (url.origin === new URL(API_BASE).origin && /^\/api\/workbench\/(?:projects|jobs)\/[^/]+\/(?:assets|attachments)\/[^/]+\/content$/.test(url.pathname) && !url.search && !url.hash) return url.href; } catch { /* Untrusted imported reference. */ } } return asset.project_id ? `${API_BASE}/api/workbench/projects/${encodeURIComponent(asset.project_id)}/assets/${encodeURIComponent(asset.id)}/content` : ''; },
  addFolder: (id, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}/folders`, platformJson('POST', body)),
  folderFiles: (id, folder) => workbenchRequest(`/projects/${encodeURIComponent(id)}/folders/${encodeURIComponent(folder)}/files`),
  importFolder: (id, folder, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}/folders/${encodeURIComponent(folder)}/import`, platformJson('POST', body)),
  removeFolder: (id, folder) => workbenchRequest(`/projects/${encodeURIComponent(id)}/folders/${encodeURIComponent(folder)}`, { method: 'DELETE' }),
  createSnapshot: (id, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}/snapshots`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  getSnapshot: (id, snapshot) => workbenchRequest(`/projects/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshot)}`),
  listSnapshots: (id) => workbenchRequest(`/projects/${encodeURIComponent(id)}/snapshots`),
  createReview: (id, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}/reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
};

export const api = {
  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}`
    );
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  /**
   * Send a message in a conversation.
   */
  async sendMessage(conversationId, content) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to send message');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          try {
            const event = JSON.parse(data);
            onEvent(event.type, event);
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    }
  },
};
