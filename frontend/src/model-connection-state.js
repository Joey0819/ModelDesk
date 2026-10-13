export const CONNECTION_PRESETS = {
  xai: { name: 'Grok / xAI', base_url: 'https://api.x.ai/v1', help: '填写 xAI API Key，保存后读取你账户可用的模型。', link: 'https://console.x.ai/' },
  openai_compatible: { name: '', base_url: '', help: '适用于提供 OpenAI 兼容 Chat Completions 接口的服务。', link: null },
};
export function connectionDraft(connection, provider = 'xai') {
  const preset = CONNECTION_PRESETS[connection?.provider || provider] || CONNECTION_PRESETS.openai_compatible;
  return { id: connection?.id || null, provider: connection?.provider || provider, name: connection?.name ?? preset.name, base_url: connection?.base_url ?? preset.base_url, enabled: connection?.enabled ?? true, allow_local: Boolean(connection?.allow_local), models: (connection?.models || []).map(({ id, label }) => ({ id, label: label || id })), api_key: '', has_api_key: Boolean(connection?.has_api_key), original_base_url: connection?.base_url || '' };
}
export function connectionBody(draft) {
  const body = { provider: draft.provider, name: draft.name.trim(), base_url: draft.base_url.trim().replace(/\/$/, ''), enabled: Boolean(draft.enabled), allow_local: Boolean(draft.allow_local), models: draft.models.map(({ id, label }) => ({ id: id.trim(), label: label.trim() || id.trim() })) };
  if (draft.api_key.trim()) body.api_key = draft.api_key.trim();
  return body;
}
export function connectionIssue(draft) {
  if (!draft.name.trim()) return '请给连接起一个名称。';
  let url;
  try { url = new URL(draft.base_url.trim()); } catch { return '请输入完整的 API 地址，例如 https://api.x.ai/v1。'; }
  if (url.username || url.password || url.search || url.hash) return 'API 地址不能包含凭据、查询参数或片段。';
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) return '远程 API 使用 HTTPS；本机服务可使用 HTTP。';
  if (!draft.api_key.trim() && !draft.has_api_key && !draft.allow_local) return '请在此处填写此服务的 API Key。';
  if (['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) && !draft.allow_local) return '使用本机地址时，请启用“连接本机模型服务”。';
  if (draft.has_api_key && draft.original_base_url.replace(/\/$/, '') !== draft.base_url.trim().replace(/\/$/, '') && !draft.api_key.trim()) return '更换 API 地址时请重新填写密钥，避免将旧密钥发往其他服务。';
  if (draft.models.some((model) => !model.id.trim())) return '请填写模型 ID，或移除空模型行。';
  if (new Set(draft.models.map((model) => model.id.trim())).size !== draft.models.length) return '同一连接中不能重复添加模型 ID。';
  return '';
}
export function connectionStatus(connection) {
  if (!connection.enabled) return '已停用';
  if (connection.last_test?.ok) return '调用已验证';
  if (connection.last_test?.ok === false) return '上次调用失败';
  if (!connection.models?.length) return '待选择模型';
  return '尚未测试调用';
}
