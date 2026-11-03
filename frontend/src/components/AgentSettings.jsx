import Connections from './ModelConnections';
export { ConnectionForm, ConnectionRow } from './ModelConnections';
import SkillsSettings from './SkillsSettings';
import FeishuConnection from './FeishuConnection';
import ReadinessPanel from './ReadinessPanel';
import { useCallback, useEffect, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { museApi } from '../muse-api';
import { museReadiness } from '../muse-state';
import { useProjects } from '../project-context';
import MissionTeamPicker from './MissionTeamPicker';
import { Icon } from './WorkbenchResults';
import './AgentSettings.css';

function MuseConnection({ onOpenGroups }) {
  const [probe, setProbe] = useState(null), [checking, setChecking] = useState(false), [error, setError] = useState('');
  const check = useCallback(async () => { setChecking(true); setError(''); try { setProbe(await museApi.status()); } catch (failure) { setError(failure.message); } finally { setChecking(false); } }, []);
  useEffect(() => { if (!REMOTE_CLIENT) { const timer = setTimeout(check, 0); return () => clearTimeout(timer); } }, [check]);
  const readiness = museReadiness(probe);
  return <section className="agent-muse-connection"><div className="connection-identity"><span className="connection-symbol muse">M</span><div><h2>Muse Mac 原生连接</h2><p>通过已登录的 Muse 窗口执行任务和回收附件</p></div><span className={`connection-state ${readiness.ready ? 'verified' : ''}`}>{REMOTE_CLIENT ? '请在 Mac 操作' : readiness.label}</span></div><p>在群聊添加 Muse 成员后，选择“自动完成”。平台按任务分工调用 Muse；Mac 需要保持解锁且 Muse 窗口可操作。</p><p>回复文字、生成完成和文件可用会分别核对。出现 Video not found 时，作品尚不可交付；继续观察只检查原任务，不重复创作。</p>{error && <div className="error-banner">{error}</div>}<p className="connection-help">{readiness.detail}</p><div className="agent-inline-actions">{!REMOTE_CLIENT && <button type="button" className="subtle-button" disabled={checking} onClick={check}><Icon name="refresh" size={15} />{checking ? '正在检查…' : '检查 Muse 连接'}</button>}<button type="button" className="primary-button" onClick={onOpenGroups}>打开项目群</button></div></section>;
}

export default function AgentSettings({ status, models, initialSection = 'agents', onOpenNavigation, onOpenGroups, onStartGroup, onOpenLibrary, onRefresh }) {
  const { activeProjectId, activeProject } = useProjects();
  const [section, setSection] = useState(initialSection), [team, setTeam] = useState(null), [pending, setPending] = useState(false), [editSignal, setEditSignal] = useState(0);
  const chooseTeam = useCallback((value) => setTeam(value), []);
  return <main className="agent-settings-workspace"><header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name="group" size={22} /></span><div><h1>Agent 与连接</h1><span>设置模型、角色和协作成员</span></div></div><button type="button" className="subtle-button" onClick={onOpenGroups}>返回项目群</button></header><nav className="agent-settings-tabs" aria-label="Agent 设置分类">{[['agents', '项目 Agent'], ['connections', '模型连接 / API Key'], ['muse', 'Muse 应用'], ['feishu', '飞书远程'], ['skills', 'Skills 能力库']].map(([id, label]) => <button type="button" key={id} aria-current={section === id ? 'page' : undefined} className={section === id ? 'selected' : ''} onClick={() => setSection(id)}>{label}</button>)}</nav><div className="agent-settings-scroll"><div className="agent-settings-content">
    {section === 'agents' && <><div className="agent-section-heading"><div><h2>选择统筹模型，组建 Agent 团队</h2><p>{activeProject?.name || '当前项目'}的协作组保存成员类型、模型和职责；可加入 Codex、Claude Code 或 Muse。</p></div><button type="button" className="primary-button" disabled={pending} onClick={() => setEditSignal((value) => value + 1)}><Icon name="plus" size={15} />添加 Agent</button></div><MissionTeamPicker key={activeProjectId || 'unassigned'} projectId={activeProjectId} team={team} models={models} catalog={status?.models || []} onChange={chooseTeam} onPending={setPending} editSignal={editSignal} onOpenAgentSettings={() => setSection('connections')} /><div className="agent-next-step"><div><strong>选择统筹 LLM → 添加模型与执行 Agent → 在项目群协作</strong><p>找不到想用的模型？先添加连接。已有群聊可从群顶部“管理成员”更新名单，历史任务保持原来的成员记录。</p></div><button type="button" className="subtle-button" onClick={() => setSection('connections')}>接入 Grok / 其他模型</button></div><div className="agent-inline-actions"><button type="button" className="primary-button" disabled={pending} onClick={() => team?.id ? onStartGroup(team) : onOpenGroups()}>{team?.id ? "用此协作组新建群聊" : "前往项目群"}</button><button type="button" className="subtle-button" onClick={onOpenLibrary}>打开智能体模板库</button><button type="button" className="subtle-button" onClick={() => setSection('muse')}>设置 Muse</button><button type="button" className="subtle-button" onClick={() => setSection('feishu')}>飞书收发任务</button></div></>}
    {section === 'connections' && <><Connections onRefresh={onRefresh} /><section className="existing-connections"><h3>已有登录连接</h3><p>继续使用本机现有登录；添加 API 连接不会替换它们。</p>{status?.providers?.filter((provider) => ['codex', 'antigravity', 'claude'].includes(provider.id)).map((provider) => <div key={provider.id}><strong>{provider.label}</strong><span>{provider.authenticated ? '已发现登录凭据' : '尚未接入'}</span><small>{provider.verified_models?.length ? `${provider.verified_models.length} 个模型有调用记录` : provider.reason || '调用状态以实际请求为准'}</small></div>)}</section></>}
    {section === 'agents' && <ReadinessPanel />}
    {section === 'feishu' && <FeishuConnection onOpenGroups={onOpenGroups} />}
    {section === 'skills' && <SkillsSettings />}
    {section === 'muse' && <MuseConnection onOpenGroups={onOpenGroups} />}
  </div></div></main>;
}
