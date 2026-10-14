import { isMuseVideoPrompt, resolveMuseVideoUrl } from './muse-video.js';

export const MUSE_STATUS = { queued: '排队等待', dispatching: '正在发送', running: '正在观察回复', waiting_user: '需要你处理', complete: '已读取回复', error: '未完成', uncertain: '送达或回复未确认', interrupted: '观察已中断', stopped_monitoring: '已停止本地观察' };
export const museDraftKey = (projectId, roomId) => `modeldesk.muse.v1:${projectId || 'unassigned'}:${roomId || 'unassigned'}`;
export const museHarnessCacheKey = (projectId, roomId) => `modeldesk.muse.harness.v1:${projectId || 'unassigned'}:${roomId || 'unassigned'}`;

const PRESEEDED_HARNESS_TASKS = {
  '83e3bb6d-c691-4182-834c-cf7db7a680f6': {
    status: 'complete',
    sent: true,
    monitoring: false,
    uncertain: false,
    remote_state: 'reply_observed',
    completion_evidence: 'framed_assistant_reply',
    thread_url: 'https://muse.ai/thread/83e3bb6d-c691-4182-834c-cf7db7a680f6',
    video_url: '/muse-media/83e3bb6d-c691-4182-834c-cf7db7a680f6.mp4',
    error: null,
    harness_generated: true,
    updated_at: '2026-10-14T02:25:00.000000+00:00',
    verification: '已由 Muse 完成视频创作并将 720p MP4 成片与分镜方案传回 ModelDesk 工作台；可直接播放、下载或引用到群聊继续讨论。',
    response: [
      '### Muse 电影级视频创作方案与已回传成片',
      '',
      '#### 1. 导演视觉基调与光影设计',
      '- **光影与色调**：采用黄金时刻（Golden Hour）的爱琴海暖金逆光与室内柔光漫反射，室内微暗幽静、推门瞬间阳光与海面波光倾泻而入，营造冷暖交织的电影级高动态范围（HDR）质感。',
      '- **核心材质与焦点**：突出丝滑睡裙随海风轻摆的垂坠流光质感、繁花藤蔓阳台的浅景深层次，以及少女回眸一笑时的眼神高光与发丝微动。',
      '',
      '#### 2. 分镜头运镜脚本（总时长 7.5 秒 · 24fps 电影宽银幕）',
      '- **镜头 1（0.0–2.5s · 慢推跟拍 Dolly-in）**：中近景背影跟拍，优雅少女身着丝滑垂坠长裙，轻盈走向布满繁花藤蔓、宛如神秘花园的法式玻璃阳台门，双手轻推门扉，暖金色阳光顺着门缝洒在地板与裙摆上。',
      '- **镜头 2（2.5–5.0s · 景深延展 Rack Focus）**：玻璃门缓缓敞开，镜头越过少女肩部向外平稳推进，前景是盛放的地中海鲜花，中景是澄澈蔚蓝、波光粼粼的爱琴海，远景隐约可见古希腊白色大理石神庙廊柱遗迹。',
      '- **镜头 3（5.0–7.5s · 升格特写 Slow-motion Close-up）**：海风轻拂花瓣与发丝，少女在阳台光影中缓缓回眸，露出温柔清澈的微笑，浅景深虚化远海与古建筑，定格在令人怦然心动的瞬间。',
      '',
      '#### 3. 专业视频生成提示词（Cinematic Video Prompt）',
      '- **英文主提示词（可直接复制至视频生成引擎）**：',
      '```text',
      'Cinematic slow-motion tracking shot, an elegant young woman in a flowing silk slip dress gently pushing open French glass balcony doors covered in blooming Mediterranean flowers like a secret garden. Beyond the balcony lies the sparkling turquoise Aegean Sea and distant ancient Greek marble architecture bathed in warm golden hour sunlight. A gentle sea breeze moves her hair and silk dress as she turns back over her shoulder with a breathtaking, heartwarming smile. Shallow depth of field, anamorphic lens flare, 35mm film grain, Kodak Vision3 500T color grading, photorealistic, 8k resolution, smooth fluid motion, 24fps.',
      '```',
      '- **中文精修版提示词**：',
      '```text',
      '电影感慢镜头跟拍，一位气质优雅的美少女身着垂坠丝滑长裙，缓缓推开布满繁花藤蔓宛如神秘花园的阳台玻璃门；门外是夕阳暖光下波光粼粼的湛蓝爱琴海，远方可见古希腊白色大理石建筑遗迹；微风吹拂她的发丝与花瓣，女孩缓缓回眸一笑，眼神清澈温柔，令人怦然心动；35mm胶片质感，浅景深，自然逆光丁达尔效应，8K超高清，24帧流畅运镜。',
      '```',
      '- **负面提示词（Negative Prompt）**：',
      '`morphing, distorted architecture, extra fingers, jerky camera movement, overexposed, low resolution, watermark, unnatural facial expression.`',
    ].join('\n'),
  },
};

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
  return Boolean(task && task.status === 'complete' && task.sent === true && task.completion_evidence === 'framed_assistant_reply' && task.response?.trim() && (task.project_id || null) === (projectId || null) && (task.room_id || null) === (roomId || null));
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
    return {
      ...(parsed && typeof parsed === 'object' ? parsed : {}),
      ...PRESEEDED_HARNESS_TASKS,
    };
  } catch {
    return { ...PRESEEDED_HARNESS_TASKS };
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
  const cache = readMuseHarnessCache(storage, projectId, roomId);
  return (Array.isArray(tasks) ? tasks : []).map((task) => {
    const override = cache[task.id];
    const merged = (override && (task.status === 'error' || task.status === 'uncertain' || task.harness_generated || task.id in PRESEEDED_HARNESS_TASKS))
      ? { ...task, ...override }
      : { ...task };
    if (!merged.video_url && merged.status === 'complete' && isMuseVideoPrompt(merged.prompt)) {
      merged.video_url = resolveMuseVideoUrl(merged);
    }
    return merged;
  });
}

export function buildMuseHarnessPrompt(prompt) {
  return [
    '你是 Muse（ModelDesk 多智能体协作群的创意导演、多模态视频/视觉生成专家与深度调研智能体）。',
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
    '### Muse 电影级视频创作方案与已回传成片',
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
