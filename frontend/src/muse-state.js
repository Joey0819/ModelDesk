export const MUSE_STATUS = { queued: '排队等待', dispatching: '正在发送', running: '正在观察回复', waiting_user: '需要你处理', complete: '已读取回复', error: '未完成', uncertain: '送达或回复未确认', interrupted: '观察已中断', stopped_monitoring: '已停止本地观察' };
export const museDraftKey = (projectId, roomId) => `modeldesk.muse.v1:${projectId || 'unassigned'}:${roomId || 'unassigned'}`;
export const museHarnessCacheKey = (projectId, roomId) => `modeldesk.muse.harness.v1:${projectId || 'unassigned'}:${roomId || 'unassigned'}`;

export function isUnverifiedMuseTask(task) {
  return Boolean(task?.harness_generated || task?.source_unverified
    || task?.source_kind === 'unverified_local_generation'
    || String(task?.verification || '').includes('Harness')
    || task?.events?.some((event) => ['harness_fallback_started', 'harness_retry_requested'].includes(event.type)));
}

export function normalizeMuseSource(task) {
  if (!isUnverifiedMuseTask(task)) return { ...task };
  return { ...task, status: task.status === 'complete' ? 'uncertain' : task.status,
    source_unverified: true, source_kind: 'unverified_local_generation', harness_generated: true,
    completion_evidence: 'source_unverified', thread_url: null, video_url: null,
    verification: '历史 ModelDesk 本地生成内容；没有 Muse 原生送达或回复证据，不能作为 Muse 已完成交付。' };
}

export function readMuseDraft(storage, projectId, roomId) {
  try {
    const value = JSON.parse(storage.getItem(museDraftKey(projectId, roomId)) || '{}');
    return { prompt: typeof value.prompt === 'string' ? value.prompt : '', taskId: typeof value.taskId === 'string' ? value.taskId : null };
  } catch { return { prompt: '', taskId: null }; }
}
export function scopedMuseTasks(items, projectId, roomId) {
  return (Array.isArray(items) ? items : []).filter((task) => (task.project_id || null) === (projectId || null) && (task.room_id || null) === (roomId || null))
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
}
export function museReadiness(probe) {
  if (!probe) return { ready: false, label: '正在检查连接', detail: '读取 Muse 原生窗口状态。' };
  if (probe.installed == null) return { ready: false, label: '连接尚未确认', detail: probe.error || '请检查本地桥接服务后重新连接。' };
  if (!probe.installed) return { ready: false, label: '未找到 Muse', detail: probe.error || '请先安装并打开 Muse Mac。' };
  if (probe.permission !== true) return { ready: false, label: '需要辅助功能授权', detail: probe.error || '在 macOS 系统设置 → 隐私与安全性 → 辅助功能中允许 ModelDesk 桥接，再检查连接。' };
  if (!probe.ok) return { ready: false, label: '暂不可用', detail: probe.error || '暂时无法读取 Muse 原生窗口，请打开 Muse 后重试。' };
  if (!probe.composer_ready) return { ready: false, label: '等待 Muse 输入窗口', detail: '辅助功能已授权。请打开 Muse 的可用输入窗口，再检查连接。' };
  return { ready: true, label: '原生输入窗口已就绪', detail: '通过已登录的 Muse Mac 窗口发送并读取回复。' };
}
export function safeMuseThreadUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'muse.ai' && (!url.port || url.port === '443') && !url.username && !url.password && !url.search && !url.hash && /^\/thread\/[A-Za-z0-9_-]{1,200}\/?$/.test(url.pathname) ? url.href : null;
  } catch { return null; }
}
export function canUseMuseReply(task, projectId, roomId) {
  return Boolean(task && !isUnverifiedMuseTask(task) && task.status === 'complete' && task.sent === true && task.completion_evidence === 'framed_assistant_reply' && task.response?.trim() && (task.project_id || null) === (projectId || null) && (task.room_id || null) === (roomId || null));
}

export function mergeMuseTasks(previous, incoming, projectId, roomId) {
  const tasks = new Map(scopedMuseTasks(previous, projectId, roomId).map((task) => [task.id, task]));
  for (const task of scopedMuseTasks(incoming, projectId, roomId)) {
    const prior = tasks.get(task.id);
    if (!prior || String(task.updated_at || '') >= String(prior.updated_at || '')) tasks.set(task.id, task);
  }
  return scopedMuseTasks([...tasks.values()], projectId, roomId);
}

export function readMuseHarnessCache(storage, projectId, roomId) {
  try {
    const raw = storage?.getItem?.(museHarnessCacheKey(projectId, roomId));
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function writeMuseHarnessEntry(storage, projectId, roomId, taskId, patch) {
  if (!storage?.setItem || !taskId) return;
  try {
    const current = readMuseHarnessCache(storage, projectId, roomId);
    const updated = { ...current, [taskId]: { ...(current[taskId] || {}), ...patch, updated_at: new Date().toISOString() } };
    storage.setItem(museHarnessCacheKey(projectId, roomId), JSON.stringify(updated));
  } catch { /* Ignore quota errors. */ }
}

export function applyMuseHarnessOverrides(tasks, storage, projectId, roomId) {
  // Browser drafts are not native receipts. Keep older local drafts available
  // as explicitly unverified metadata without changing server task state.
  const cache = readMuseHarnessCache(storage, projectId, roomId);
  return (Array.isArray(tasks) ? tasks : []).map((task) => {
    const normalized = normalizeMuseSource(task);
    const draft = cache[task.id];
    if (draft?.response) normalized.unverified_local_draft = { response: draft.response, source_kind: 'unverified_local_generation' };
    return normalized;
  });
}

export function buildMuseHarnessPrompt(prompt) {
  return [
    '你是 ModelDesk 创意助手，负责提供视频分镜和生成提示词草稿。你不是 Muse 应用；不要声称已在 Muse 发送任务或生成视频。',
    '请直接针对用户的任务给出高水准、结构化、可直接交付执行的专业成果：',
    '1. 【导演创意与视觉基调】：光影氛围、色彩美学、人物神态与镜头情绪；',
    '2. 【电影级分镜头脚本】：按时间轴列出景别、运镜方式（推轨、慢门跟拍、景深切换）、画面细节与转场；',
    '3. 【可直接复制的专业生成提示词（Prompt）】：提供中文精修版与英文高画质电影感提示词（含镜头参数、光效、材质细节与负面提示词 Negative Prompt）；',
    '4. 【群组协作衔接建议】：供项目群其他成员下一步核对的要点。',
    '',
    '用户任务：',
    (prompt || '').trim(),
  ].join('\n');
}

export function fallbackMuseCreativeResponse(prompt) {
  const clean = (prompt || '').trim();
  return [
    '### ModelDesk 本地创意草稿（尚未在 Muse 执行）',
    '',
    `**任务主题**：${clean}`,
    '',
    '#### 1. 导演视觉基调与光影设计',
    '- **光影与色调**：采用黄金时刻（Golden Hour）自然逆光与柔光漫反射，营造高动态范围（HDR）电影胶片质感，突出前景人物质感与远景空间纵深。',
    '- **画面焦点与情绪**：通过浅景深与慢动作运镜捕捉细腻神态变化与环境互动，保证视觉连贯性与叙事张力。',
    '',
    '#### 2. 分镜头运镜脚本（7.5 秒 · 24fps）',
    '- **镜头 1（0.0–2.5s · 慢推跟拍 Dolly-in）**：中近景平稳推进，展现主体动作起始与前景环境细节，光线随动作自然流动。',
    '- **镜头 2（2.5–5.0s · 景深延展 Rack Focus）**：镜头展开全景纵深，中景与远景建筑/自然风光依次呈现，色彩通透饱满。',
    '- **镜头 3（5.0–7.5s · 升格特写 Slow-motion Close-up）**：聚焦主体神态与微风动态细节，浅景深虚化背景，完成情绪定格。',
    '',
    '#### 3. 专业视频生成提示词（Cinematic Prompt）',
    '- **中文精修版提示词**：',
    '```text',
    `电影感慢镜头跟拍，${clean}；黄金时刻自然逆光，35mm胶片颗粒与浅景深虚化，丝滑流畅的运镜过渡，8K超高清画质，24帧电影宽银幕质感。`,
    '```',
    '- **英文主提示词（Cinematic English Prompt）**：',
    '```text',
    'Cinematic slow-motion tracking shot, golden hour natural backlighting, shallow depth of field, 35mm Kodak Vision3 film aesthetic, anamorphic lens flare, fluid camera choreography, photorealistic textures, 8k resolution, 24fps.',
    '```',
    '- **负面提示词（Negative Prompt）**：',
    '`morphing, distorted anatomy or architecture, jerky camera motion, overexposed, low resolution, watermark.`',
  ].join('\n');
}
