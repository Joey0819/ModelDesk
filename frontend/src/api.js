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

export const workbenchApi = {
  status: () => workbenchRequest('/status'),
  listJobs: () => workbenchRequest('/jobs'),
  getJob: (id) => workbenchRequest(`/jobs/${encodeURIComponent(id)}`),
  createJob: (body) => workbenchRequest('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  cancelJob: (id) => workbenchRequest(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  events: (id, after = 0) => new EventSource(`${API_BASE}/api/workbench/jobs/${encodeURIComponent(id)}/events?after=${after}`),
  exportUrl: (id, format) => `${API_BASE}/api/workbench/jobs/${encodeURIComponent(id)}/export?format=${encodeURIComponent(format)}`,
  listProjects: () => workbenchRequest('/projects'),
  addProject: (body) => workbenchRequest('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  getProject: (id) => workbenchRequest(`/projects/${encodeURIComponent(id)}`),
  createSnapshot: (id, body) => workbenchRequest(`/projects/${encodeURIComponent(id)}/snapshots`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  getSnapshot: (id, snapshot) => workbenchRequest(`/projects/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshot)}`),
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
