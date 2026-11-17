const strings = (value) => Array.isArray(value) ? [...new Set(value.filter((item) => typeof item === 'string').map((item) => item.trim()).filter(Boolean))] : [];
export const FEISHU_SETUP_URL = 'https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-';
export const feishuSenderIds = (value) => strings(String(value || '').split(/[\s,，;；]+/));
export const feishuOwnerIds = (value) => strings(value);
export const FEISHU_OWNER_PATTERN = /^ou_[A-Za-z0-9_-]{1,180}$/;
export const validFeishuOwner = (value) => FEISHU_OWNER_PATTERN.test(String(value || ''));
export function feishuBinding(value = {}) {
  return {
    chat_id: typeof value.chat_id === 'string' ? value.chat_id : '', name: typeof value.name === 'string' ? value.name : '',
    project_id: typeof value.project_id === 'string' && value.project_id ? value.project_id : null,
    room_id: typeof value.room_id === 'string' ? value.room_id : '', allowed_sender_ids: strings(value.allowed_sender_ids), allow_native: value.allow_native === true,
    natural_language_tasks: value.natural_language_tasks === true,
  };
}
/** Only whitelisted public fields enter the draft; saved credentials never return to inputs. */
export function feishuDraft(config = {}) {
  return {
    enabled: config.enabled === true, app_id: typeof config.app_id === 'string' ? config.app_id : '', app_secret: '',
    original_app_id: typeof config.app_id === 'string' ? config.app_id : '', has_app_secret: config.has_app_secret === true,
    owner_sender_ids: feishuOwnerIds(config.owner_sender_ids),
    bindings: Array.isArray(config.bindings) ? config.bindings.map(feishuBinding) : [],
  };
}
export function feishuBody(draft) {
  return {
    enabled: draft.enabled === true, app_id: String(draft.app_id || '').trim(),
    owner_sender_ids: feishuOwnerIds(draft.owner_sender_ids).slice(0, 10),
    ...(String(draft.app_secret || '').trim() ? { app_secret: draft.app_secret.trim() } : {}),
    bindings: (draft.bindings || []).map((item) => { const binding = feishuBinding(item); return { ...binding, chat_id: binding.chat_id.trim(), name: binding.name.trim(), room_id: binding.room_id.trim() }; }),
  };
}
export function feishuIssue(draft) {
  if (!draft.app_id.trim()) return '请填写飞书自建应用的 App ID。';
  if (!draft.app_secret.trim() && !draft.has_app_secret) return '请填写此自建应用的 App Secret。';
  if (draft.has_app_secret && draft.original_app_id !== draft.app_id.trim() && !draft.app_secret.trim()) return '更换 App ID 时请同时填写新 App Secret，避免沿用其他应用的凭据。';
  const owners = feishuOwnerIds(draft.owner_sender_ids);
  if (owners.length > 10) return '管理员 Open ID 最多 10 个，请精简后重试。';
  if (owners.some((item) => !validFeishuOwner(item))) return '管理员 Open ID 需以 ou_ 开头，请核对后重试。';
  if (draft.bindings.some((item) => !item.chat_id.trim() || !item.room_id.trim())) return '请为每个允许的飞书会话填写 Chat ID，并选择对应的 ModelDesk 项目群。';
  if (new Set(draft.bindings.map((item) => item.chat_id.trim())).size !== draft.bindings.length) return '同一个飞书 Chat ID 只能绑定一个项目群。';
  return '';
}
export function feishuConnectionStatus(status, config) {
  if (!config?.app_id || !config.has_app_secret) return '未配置';
  if (!config.enabled) return '已停用';
  return ({ not_configured: '未配置', disabled: '已停用', connecting: '正在连接', reconnecting: '等待自动重连', connected: '长连接在线', error: '连接失败' })[status?.state] || '连接状态未确认';
}
export function safeFeishuError(error, secret = '') {
  const message = typeof error === 'string' ? error : error?.message || '飞书请求未完成，请重新检查配置。';
  return secret ? message.split(secret).join('[密钥已隐藏]') : message;
}
