import { useId, useRef, useState } from 'react';
import { Icon } from './WorkbenchResults';
import { MODES } from '../workbench-modes';
import { PLATFORM_SECTIONS } from '../platform-settings';
import './WorkspaceNavigation.css';

const QUICK_MODES = { chat: '聊天', council: '评审', crewai: '团队' };
const QUICK_RESOURCES = ['overview', 'prompts', 'agents'];

function NavigationDisclosure({ label, currentLabel, icon, children }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const trigger = useRef(null);
  const close = () => {
    trigger.current?.focus();
    setOpen(false);
  };
  const handleKeyDown = (event) => {
    if (event.key !== 'Escape' || !open) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  };
  const foldedCurrent = Boolean(currentLabel && !open);
  return <div className="more-modes navigation-disclosure" onKeyDown={handleKeyDown}>
    <button type="button" ref={trigger} className={foldedCurrent ? 'active' : ''} aria-current={foldedCurrent ? 'page' : undefined} aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>
      <Icon name={icon} size={16} />
      <span>{foldedCurrent ? currentLabel : label}</span>
      <Icon name="chevron" size={13} />
    </button>
    {open && <div id={id} className="mode-switcher" onClick={close}>{children}</div>}
  </div>;
}

export default function WorkspaceNavigation({
  workspace,
  platformSection,
  mode,
  activeJobs,
  onAgents,
  onGroups,
  onMissions,
  onTodo,
  onMode,
  onPlatform,
  onNewConversation,
  onProjects,
}) {
  const destinations = [
    ['groups', '项目群聊', 'group', 'mission-entry', onGroups],
    ['missions', '协作任务', 'tasks', 'mission-entry', onMissions],
    ['projects', '项目与资料', 'folder', 'projects-entry', onProjects],
    ['agents', 'Agent 与连接', 'settings', 'agent-settings-entry', onAgents],
    ['todo', '任务看板', 'dashboard', 'todo-entry', onTodo],
  ];
  const modeButton = (id, label, onClick) => <button type="button" key={id} className={mode === id ? 'active' : ''} aria-current={mode === id ? 'page' : undefined} onClick={onClick} title={MODES[id].description}>
    <Icon name={MODES[id].icon} size={15} />
    <span><strong>{label}</strong></span>
    {activeJobs.some((job) => job.mode === id) && <span className="nav-running" title="有任务运行中" />}
  </button>;
  const resourceButton = (id, onClick) => <button type="button" className={platformSection === id ? 'active' : ''} aria-current={platformSection === id ? 'page' : undefined} key={id} onClick={onClick}>
    <Icon name={PLATFORM_SECTIONS[id][1]} size={16} />
    <span>{PLATFORM_SECTIONS[id][0]}</span>
  </button>;
  const advancedResource = !QUICK_RESOURCES.includes(platformSection);
  const advancedMode = !Object.hasOwn(QUICK_MODES, mode);
  return <>
    <nav className="workspace-destinations" aria-label="核心工作台切换">
      <div className="sidebar-workspace-grid">
        {destinations.map(([id, label, icon, entryClass, onClick]) => <button type="button" key={id} className={`${entryClass} sidebar-grid-tile${workspace === id ? ' selected' : ''}`} aria-current={workspace === id ? 'page' : undefined} aria-haspopup={id === 'projects' ? 'dialog' : undefined} onClick={onClick}>
          <Icon name={icon} size={16} />
          <strong>{label}</strong>
        </button>)}
      </div>
      <div className="sidebar-navigation-tabs">
        <button type="button" className={workspace === 'tools' ? 'selected' : ''} aria-current={workspace === 'tools' ? 'page' : undefined} onClick={() => onMode(mode)}>单项协作</button>
        <button type="button" className={workspace === 'platform' ? 'selected' : ''} aria-current={workspace === 'platform' ? 'page' : undefined} onClick={() => onPlatform(platformSection)}>项目资源</button>
        <button type="button" className="new-task new-task-inline" onClick={onNewConversation} title="新建独立会话">
          <Icon name="plus" size={14} />新建会话
        </button>
      </div>
    </nav>
    {workspace === 'platform' && <div className="sidebar-primary-navigation">
      <nav className="platform-navigation" aria-label="项目资源">
        {QUICK_RESOURCES.map((id) => resourceButton(id, () => onPlatform(id)))}
        <NavigationDisclosure label="高级资源" currentLabel={advancedResource ? PLATFORM_SECTIONS[platformSection][0] : null} icon="settings">
          {Object.keys(PLATFORM_SECTIONS).filter((id) => !QUICK_RESOURCES.includes(id)).map((id) => resourceButton(id, () => onPlatform(id)))}
        </NavigationDisclosure>
      </nav>
    </div>}
    {workspace === 'tools' && <div className="sidebar-primary-navigation">
      <nav className="mode-navigation" aria-label="工作模式">
        <div className="quick-modes">
          {Object.entries(QUICK_MODES).map(([id, label]) => modeButton(id, label, () => onMode(id)))}
        </div>
        <NavigationDisclosure label="其他协作方式" currentLabel={advancedMode ? MODES[mode].label : null} icon={advancedMode ? MODES[mode].icon : 'layers'}>
          {Object.entries(MODES).filter(([id]) => !Object.hasOwn(QUICK_MODES, id)).map(([id, item]) => modeButton(id, item.label, () => onMode(id)))}
        </NavigationDisclosure>
      </nav>
    </div>}
  </>;
}
