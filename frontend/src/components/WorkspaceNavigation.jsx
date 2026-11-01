import { Icon } from './WorkbenchResults';
import { MODES } from '../workbench-modes';
import { PLATFORM_SECTIONS } from '../platform-settings';

export default function WorkspaceNavigation({
  agentsView,
  onAgents,
  groupsView,
  onGroups,
  missionView,
  navigationTab,
  platformView,
  projectView,
  platformSection,
  mode,
  modeMenu,
  activeJobs,
  onMissions,
  onMode,
  onPlatform,
  onModeMenu,
  onNewConversation,
  onProjects,
}) {
  const singleActive = !agentsView && !missionView && !groupsView;
  return <>
    <div className="sidebar-workspace-grid" role="navigation" aria-label="核心工作台切换">
      <button className={`mission-entry sidebar-grid-tile ${groupsView ? "selected" : ""}`} onClick={onGroups} title="固定成员，持续讨论与协作">
        <Icon name="group" size={16} />
        <span><strong>项目群聊</strong><small>多智能体协同</small></span>
      </button>
      <button className={`mission-entry sidebar-grid-tile ${missionView ? 'selected' : ''}`} onClick={onMissions} title="多引擎接力，直到交付">
        <Icon name="tasks" size={16} />
        <span><strong>协作任务</strong><small>跨引擎流水线</small></span>
      </button>
      <button className={`projects-entry sidebar-grid-tile ${projectView ? 'active selected' : ''}`} onClick={onProjects} title="图片、文件、文件夹与历史">
        <Icon name="folder" size={16} />
        <span><strong>项目与资料</strong><small>共享文件与快照</small></span>
      </button>
      <button type="button" className={`agent-settings-entry sidebar-grid-tile ${agentsView ? "selected" : ""}`} onClick={onAgents} title="添加成员、Grok / API、Muse">
        <Icon name="settings" size={16} />
        <span><strong>Agent 与连接</strong><small>模型 / API / Muse</small></span>
      </button>
    </div>
    <div className="sidebar-navigation-tabs">
      <button className={singleActive && navigationTab === 'tools' ? 'selected' : ''} onClick={() => onMode(mode)}>单项协作</button>
      <button className={singleActive && navigationTab === 'platform' ? 'selected' : ''} onClick={() => onPlatform('overview')}>项目资源</button>
      <button className="new-task new-task-inline" onClick={onNewConversation} title="新建独立会话">
        <Icon name="plus" size={14} />新建会话
      </button>
    </div>
    <div className="sidebar-primary-navigation">
      {navigationTab === 'platform' ? (
        <nav className="platform-navigation" aria-label="项目资源">
          {['overview', 'prompts', 'agents'].map((id) => (
            <button className={platformView && platformSection === id ? 'active' : ''} key={id} onClick={() => onPlatform(id)}>
              <Icon name={PLATFORM_SECTIONS[id][1]} size={16} />
              <span>{PLATFORM_SECTIONS[id][0]}</span>
            </button>
          ))}
          <details className="advanced-resource-navigation">
            <summary><Icon name="settings" size={15} />高级资源<Icon name="chevron" size={13} /></summary>
            {Object.entries(PLATFORM_SECTIONS).filter(([id]) => !['overview', 'prompts', 'agents'].includes(id)).map(([id, item]) => (
              <button className={platformView && platformSection === id ? 'active' : ''} key={id} onClick={() => onPlatform(id)}>
                <Icon name={item[1]} size={15} />
                <span>{item[0]}</span>
              </button>
            ))}
          </details>
        </nav>
      ) : (
        <nav className="mode-navigation" aria-label="工作模式">
          <div className="quick-modes">
            {['chat', 'council', 'crewai'].map((id) => (
              <button key={id} className={singleActive && !platformView && !projectView && mode === id ? 'active' : ''} onClick={() => onMode(id)} title={MODES[id].description}>
                <Icon name={MODES[id].icon} size={15} />
                <span><strong>{{ chat: '聊天', council: '评审', crewai: '团队' }[id]}</strong></span>
                {activeJobs.some((job) => job.mode === id) && <span className="nav-running" title="有任务运行中" />}
              </button>
            ))}
          </div>
          <div className="more-modes">
            <button type="button" className={singleActive && !['chat', 'council', 'crewai'].includes(mode) ? 'active' : ''} aria-expanded={modeMenu} onClick={onModeMenu}>
              <Icon name={MODES[mode].icon} size={16} />
              <span>{!['chat', 'council', 'crewai'].includes(mode) ? MODES[mode].label : '全部协作方式'}</span>
              <Icon name="chevron" size={13} />
            </button>
            {modeMenu && (
              <div className="mode-switcher" role="menu" aria-label="全部协作方式">
                {Object.entries(MODES).map(([id, item]) => (
                  <button role="menuitem" key={id} onClick={() => onMode(id)}>
                    <Icon name={item.icon} size={16} />
                    <span><strong>{item.label}</strong><small>{item.description}</small></span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </nav>
      )}
    </div>
  </>;
}
