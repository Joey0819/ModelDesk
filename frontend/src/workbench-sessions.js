import { EMPTY_CONTEXT } from './platform-settings.js';
import { projectDraftKey, projectSessionKey } from './project-context.js';

export const SESSION_KEY = 'modeldesk.sessions.v1';
export const DRAFT_KEY = 'modeldesk.conversation-drafts.v1';
export const WORKSPACE_KEY = 'modeldesk.conversation-workspaces.v1';
export function sessionFor(sessions, mode, projectId) {
  const scoped = sessions[projectSessionKey(projectId, mode)];
  const legacy = sessions[mode];
  return scoped || ((legacy?.context?.project_id || null) === (projectId || null) ? legacy : null) || {};
}
export function readConversationWorkspaces(storage) {
  try {
    const saved = JSON.parse(storage.getItem(WORKSPACE_KEY) || '{}');
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {};
    return Object.fromEntries(Object.entries(saved).filter(([, item]) => item && typeof item === 'object' && !Array.isArray(item)).map(([key, item]) => [key, { draft: typeof item.draft === 'string' ? item.draft : '', context: sourceSelection(item.context), preferences: conversationPreferences(item.preferences), job_id: typeof item.job_id === 'string' ? item.job_id : null }]));
  } catch { return {}; }
}
export function readConversationDrafts(storage, key = DRAFT_KEY) {
  try {
    const value = JSON.parse(storage.getItem(key) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).filter(([, draft]) => typeof draft === 'string')) : {};
  } catch { return {}; }
}
export function conversationDraft(drafts, job) {
  return drafts[projectDraftKey(job.project_id, job.conversation_id || job.id)] || drafts[job.conversation_id || job.id] || '';
}
export function reviewContinuation(jobs, projectId) {
  return [...jobs].filter((job) => job.mode === 'review' && job.project_id === projectId && ['complete', 'completed'].includes(job.status) && job.conversation_id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] || null;
}
export function readSessions(storage) {
  try {
    const value = JSON.parse(storage.getItem(SESSION_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function sourceSelection(value = {}) {
  value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const context = { ...EMPTY_CONTEXT };
  for (const key of ['knowledge_ids', 'memory_ids', 'mcp_server_ids', 'tools', 'attachment_ids']) {
    context[key] = Array.isArray(value[key]) ? [...new Set(value[key].filter((id) => typeof id === 'string'))] : [];
  }
  for (const key of ['project_id', 'snapshot_id']) context[key] = typeof value[key] === 'string' ? value[key] : null;
  context.memory_scope = typeof value.memory_scope === 'string' && value.memory_scope.trim() ? value.memory_scope : 'default';
  context.remember = value.remember === true;
  context.include_project_assets = value.include_project_assets !== false;
  return context;
}

export function conversationPreferences(value = {}) {
  value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  return {
    models: Array.isArray(value.models) ? [...new Set(value.models.filter((id) => typeof id === 'string'))].slice(0, 4) : [],
    chairman: typeof value.chairman === 'string' ? value.chairman : 'gpt-6.1-sol',
    rounds: Math.max(1, Math.min(6, Number(value.rounds) || 2)),
    context_turns: Math.max(1, Math.min(40, Number(value.context_turns) || 12)),
    system_prompt: typeof value.system_prompt === 'string' ? value.system_prompt : '',
    relay_prompt: typeof value.relay_prompt === 'string' ? value.relay_prompt : '请阅读其他参与者的发言，补充新的观点或指出具体问题，避免重复。',
    roles: value.roles && typeof value.roles === 'object' && !Array.isArray(value.roles) ? Object.fromEntries(Object.entries(value.roles).filter(([, role]) => typeof role === 'string')) : {},
  };
}

export function restoredSession(sessions, selectedMode, job) {
  const saved = sessionFor(sessions, selectedMode, job.project_id);
  const matching = saved?.job_id === job.id;
  return { context: sourceSelection(matching && saved.context ? saved.context : job), preferences: conversationPreferences(matching && saved.preferences ? saved.preferences : job) };
}

export function taskTitle(prompt = '') {
  const first = prompt.replace(/^任务要求\s*(失败\s*)?(\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}\s*)?/, '').split(/\n+/).find((line) => line.trim())?.replace(/^#{1,6}\s*/, '').replace(/\s+/g, ' ').trim() || '未命名会话';
  return first.length > 110 ? `${first.slice(0, 110)}…` : first;
}

export function conversationGroups(jobs, query = '') {
  const groups = new Map();
  for (const job of [...jobs].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))) {
    if (job.mode === 'review' || job.mode === 'sdk_script') continue;
    const id = job.conversation_id || job.id;
    const group = groups.get(id) || { id, title: taskTitle(job.prompt), latest: job, jobs: [], modes: [] };
    group.jobs.push(job);
    group.latest = job;
    if (!group.modes.includes(job.mode)) group.modes.push(job.mode);
    groups.set(id, group);
  }
  const needle = query.trim().toLowerCase();
  return [...groups.values()].filter((group) => !needle || [group.title, ...group.jobs.map((job) => job.prompt), ...group.modes].join(' ').toLowerCase().includes(needle)).sort((a, b) => Date.parse(b.latest.created_at) - Date.parse(a.latest.created_at));
}

export function previousTurns(jobs, job) {
  if (!job) return [];
  return jobs.filter((item) => item.id !== job.id && item.conversation_id === job.conversation_id && (item.project_id || null) === (job.project_id || null) && Date.parse(item.created_at) < Date.parse(job.created_at)).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

export function buildCommandItems({
  modes = {},
  mode = 'chat',
  theme = 'light',
  sidebarCollapsed = false,
  artifactsOpen = true,
  history = [],
  projects = [],
  activeProjectId = null,
  onOpenGroups,
  onOpenMissions,
  onOpenProjects,
  onOpenAgentSettings,
  onSelectMode,
  onNewConversation,
  onToggleSidebar,
  onToggleArtifacts,
  onToggleTheme,
  onOpenRemote,
  onSelectJob,
  onSelectProject,
}) {
  const items = [
    {
      id: 'nav:groups',
      category: '核心工作区',
      title: '打开项目群聊（多智能体协同群）',
      subtitle: '固定成员、@点名、自动交付与 Muse / Codex 协同',
      keywords: 'group chat room 项目群聊 群组 muse codex claude',
      icon: 'group',
      shortcut: 'G',
      action: onOpenGroups,
    },
    {
      id: 'nav:missions',
      category: '核心工作区',
      title: '打开协作任务（跨引擎流水线）',
      subtitle: '多引擎接力执行与人工审阅关卡',
      keywords: 'mission tasks 协作任务 流水线',
      icon: 'tasks',
      shortcut: 'M',
      action: onOpenMissions,
    },
    {
      id: 'nav:projects',
      category: '核心工作区',
      title: '打开项目与资料管理',
      subtitle: '管理共享文件、图片、Git 快照与代码评审',
      keywords: 'project files assets 项目 资料 文件夹',
      icon: 'folder',
      shortcut: 'P',
      action: onOpenProjects,
    },
    {
      id: 'nav:agents',
      category: '核心工作区',
      title: '打开 Agent 与模型 API 连接',
      subtitle: '配置协作组、外部 API Key、Grok 与 Muse 桥接',
      keywords: 'agents settings api key connection 模型 连接 设置',
      icon: 'settings',
      shortcut: 'A',
      action: () => onOpenAgentSettings?.('agents'),
    },
    {
      id: 'nav:connections',
      category: '核心工作区',
      title: '配置外部模型与 API Key 连接',
      subtitle: '添加 OpenAI / Anthropic / Gemini / Grok / Ollama 端点',
      keywords: 'api key grok openai gemini claude ollama 连接',
      icon: 'settings',
      action: () => onOpenAgentSettings?.('connections'),
    },
    {
      id: 'action:new-chat',
      category: '快捷操作',
      title: '新建单项协作会话',
      subtitle: `在当前模式（${modes[mode]?.label || mode}）下开始新任务`,
      keywords: 'new chat task 新建会话 清空',
      icon: 'plus',
      shortcut: 'N',
      action: onNewConversation,
    },
    {
      id: 'action:toggle-sidebar',
      category: '界面与视图',
      title: sidebarCollapsed ? '展开左侧导航栏' : '收起左侧导航栏（专注主工作区）',
      subtitle: '释放水平视野，最大化聊天与代码区域',
      keywords: 'sidebar collapse expand 侧边栏 导航 折叠 收起 展开',
      icon: 'panel',
      shortcut: '⌘B',
      action: onToggleSidebar,
    },
    {
      id: 'action:toggle-artifacts',
      category: '界面与视图',
      title: artifactsOpen ? '收起右侧成果预览栏' : '展开右侧成果预览栏',
      subtitle: '切换右侧代码、文档与 Muse 视频预览抽屉',
      keywords: 'artifact panel preview 成果 预览 右侧栏',
      icon: 'layers',
      shortcut: '⌘.',
      action: onToggleArtifacts,
    },
    {
      id: 'action:toggle-theme',
      category: '界面与视图',
      title: theme === 'light' ? '切换为深色模式 (Dark)' : '切换为浅色模式 (Light)',
      subtitle: '切换工作台整体配色主题',
      keywords: 'theme dark light 深色 浅色 主题 夜间',
      icon: theme === 'light' ? 'moon' : 'sun',
      action: onToggleTheme,
    },
    {
      id: 'action:remote',
      category: '界面与视图',
      title: '手机连接与远程访问配对',
      subtitle: '查看配对二维码、PIN 码与受信任设备',
      keywords: 'phone remote mobile 手机 远程 二维码',
      icon: 'phone',
      action: onOpenRemote,
    },
  ];

  Object.entries(modes).forEach(([id, item]) => {
    items.push({
      id: `mode:${id}`,
      category: '单项协作方式',
      title: `切换到：${item.label}`,
      subtitle: item.description,
      keywords: `mode ${id} ${item.label} ${item.description}`,
      icon: item.icon || 'chat',
      action: () => onSelectMode?.(id),
    });
  });

  projects.slice(0, 8).forEach((project) => {
    const isCurrent = project.id === activeProjectId;
    items.push({
      id: `project:${project.id}`,
      category: '切换项目空间',
      title: `${isCurrent ? '当前项目：' : '切换到项目：'}${project.name}`,
      subtitle: project.description || project.repo_path || '共享资料与独立群聊作用域',
      keywords: `project ${project.name} ${project.description || ''}`,
      icon: 'folder',
      action: () => onSelectProject?.(project.id),
    });
  });

  history.slice(0, 12).forEach((group) => {
    const latest = group.latest;
    if (!latest) return;
    const isGroupRoom = Boolean(latest.group_room_id);
    items.push({
      id: `history:${group.id}`,
      category: '最近会话与群聊记录',
      title: group.title || taskTitle(latest.prompt),
      subtitle: `${isGroupRoom ? '项目群聊' : group.modes.map((m) => modes[m]?.label || m).join(' / ')} · ${group.jobs.length} 轮记录`,
      keywords: `history ${group.title} ${latest.prompt || ''}`,
      icon: isGroupRoom ? 'group' : 'chat',
      action: () => onSelectJob?.(latest),
    });
  });

  return items;
}

export function filterCommandItems(items, query = '') {
  const needle = query.trim().toLowerCase();
  if (!needle) return items;
  const terms = needle.split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    const haystack = `${item.category} ${item.title} ${item.subtitle || ''} ${item.keywords || ''}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}
