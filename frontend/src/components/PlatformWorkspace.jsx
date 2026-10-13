import { useState } from 'react';
import { Icon } from './WorkbenchResults';
import ContextAttachments from './ContextAttachments';
import { LibraryWorkspace, SdkStudio } from './PlatformLibraries';
import { DashboardWorkspace, MigrationWorkspace, OriginalsWorkspace, ToolsWorkspace } from './PlatformOperations';
import './PlatformWorkspace.css';
import { PLATFORM_SECTIONS } from '../platform-settings';

export default function PlatformWorkspace({ section, onSection, status, jobs, models, context, onContextChange, onUsePrompt, onUseAgent, onRunJob, onOpenProjects, onOpenNavigation, onStartCollaboration, onOpenJob, onOpenArtifacts }) {
  const [agentTab, setAgentTab] = useState('agents');
  const config = PLATFORM_SECTIONS[section] || PLATFORM_SECTIONS.overview;
  return <main className="workspace platform-workspace">
    <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name={config[1]} size={22} /></span><div><h1>{section === 'overview' ? '快速开始' : config[0]}</h1><span>{section === 'overview' ? '协作、项目与成果' : '保存、管理与复用资源'}</span></div></div><span className="platform-live-connection"><span className={`status-dot ${status?.gateway?.online ? 'online' : 'offline'}`} />{status?.gateway?.online ? '本地网关已连接' : '检查本地连接'}</span></header>
    {['agents', 'tools'].includes(section) && <ContextAttachments value={context} onChange={onContextChange} jobs={jobs} onOpenProjects={onOpenProjects} />}
    {section === 'agents' && <div className="platform-inner-tabs agent-workspace-tabs"><button className={agentTab === 'agents' ? 'selected' : ''} onClick={() => setAgentTab('agents')}>智能体库</button><button className={agentTab === 'flows' ? 'selected' : ''} onClick={() => setAgentTab('flows')}>流程库与条件分支</button><button className={agentTab === 'sdk' ? 'selected' : ''} onClick={() => setAgentTab('sdk')}>原生 SDK 工作室</button></div>}
    <div className="platform-content">
      {section === 'overview' && <DashboardWorkspace jobs={jobs} models={models} onSection={(next) => { if (next === 'agents') setAgentTab('agents'); onSection(next); }} onOpenProjects={onOpenProjects} onStartCollaboration={onStartCollaboration} onOpenJob={onOpenJob} onUsePrompt={onUsePrompt} onUseAgent={onUseAgent} onOpenSdk={() => { setAgentTab('sdk'); onSection('agents'); }} />}
      {['knowledge', 'memories', 'prompts'].includes(section) && <LibraryWorkspace key={section} kind={section} models={models} status={status} context={context} onContextChange={onContextChange} onUsePrompt={onUsePrompt} onRunJob={onRunJob} onOpenArtifacts={onOpenArtifacts} />}
      {section === 'agents' && (agentTab === 'sdk' ? <SdkStudio models={models} context={context} onContextChange={onContextChange} onRunJob={onRunJob} onOpenArtifacts={onOpenArtifacts} /> : <LibraryWorkspace key={agentTab} kind={agentTab} models={models} status={status} context={context} onContextChange={onContextChange} onUsePrompt={onUsePrompt} onUseAgent={onUseAgent} onRunJob={onRunJob} onOpenArtifacts={onOpenArtifacts} />)}
      {section === 'tools' && <ToolsWorkspace context={context} />}
      {section === 'migration' && <MigrationWorkspace />}
      {section === 'originals' && <><p className="project-assets-explanation">页面顶部项目适用于统一协作工作台。完整原版 ChatHub／MultiBot 使用自己的上下文；需要本项目图片或文件时，请回到统一聊天／群聊方式运行。</p><OriginalsWorkspace /></>}
    </div>
  </main>;
}
