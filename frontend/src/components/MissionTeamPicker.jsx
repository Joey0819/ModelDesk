import { useEffect, useRef, useState } from 'react';
import { harnessApi } from '../harness-api';
import { MEMBER_KINDS, memberKind, memberKindLabel, appendTeamMember, temporaryMembers, teamIssues, teamRequest } from '../mission-teams';
import { Icon } from './WorkbenchResults';

export default function MissionTeamPicker({ projectId, team, models = [], catalog = [], onChange, onPending, onOpenAgentSettings, editSignal = 0, requireSaved = false }) {
  const [teams, setTeams] = useState([]);
  const [editing, setEditing] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [newKind, setNewKind] = useState('model');
  const selectedId = useRef(team?.id);
  const handledEditSignal = useRef(0);
  useEffect(() => {
    if (!editSignal || handledEditSignal.current === editSignal) return;
    handledEditSignal.current = editSignal;
    // Explicit parent button activation opens the editor; normal polling never does.
    setEditing(appendTeamMember(team || { name: '项目协作组', description: '', members: temporaryMembers(models) }, catalog));
  }, [editSignal, team, models, catalog]);
  useEffect(() => { selectedId.current = team?.id; }, [team?.id]);
  useEffect(() => {
    let disposed = false;
    harnessApi.teams(projectId).then((result) => {
      if (disposed) return;
      const available = (Array.isArray(result) ? result : result.teams || []).filter((item) => !item.archived && (item.project_id || null) === (projectId || null));
      setTeams(available); setLoadError(false);
      if (selectedId.current) {
        const selected = available.find((item) => item.id === selectedId.current);
        if (selected) onChange(selected);
        else { onChange(null); setError('原协作组已归档或不在当前项目。已切换为临时协作组，请确认成员后继续。'); }
      }
    }).catch((failure) => { if (!disposed) { setError(failure.message); setLoadError(true); } }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [projectId, onChange, revision]);
  useEffect(() => { onPending(loading || busy || Boolean(editing) || Boolean(team?.id && loadError)); return () => onPending(false); }, [loading, busy, editing, team?.id, loadError, onPending]);

  const members = team?.members || temporaryMembers(models);
  const available = (model) => catalog.some((item) => item.id === model);
  const missing = members.filter((member) => memberKind(member) === 'model' && !available(member.model));
  const openEditor = () => { setEditing(structuredClone(team || { name: '临时协作组', description: '', members })); setError(''); };
  const changeMember = (index, patch) => setEditing({ ...editing, members: editing.members.map((member, position) => position === index ? { ...member, ...patch } : member) });
  const validate = () => {
    const issues = teamIssues(editing, catalog, { allowRepeatedModels: true, allowExecutableMembers: true }).filter((issue) => !issue.startsWith('当前未接入'));
    if (issues.length) { setError(issues[0]); return false; }
    return true;
  };
  const save = async () => {
    if (busy || !editing || !validate()) return;
    setBusy(true); setError('');
    try {
      const body = teamRequest(editing, projectId);
      const result = editing.id ? await harnessApi.updateTeam(editing.id, body) : await harnessApi.createTeam(body);
      const saved = result.team || result;
      setTeams((previous) => [saved, ...previous.filter((item) => item.id !== saved.id)]);
      onChange(saved); setEditing(null);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const useTemporarily = () => {
    if (!validate()) return;
    onChange(teamRequest(editing, projectId)); setEditing(null); setError('');
  };
  const archive = async () => {
    if (!editing?.id || busy) return;
    setBusy(true); setError('');
    try {
      await harnessApi.archiveTeam(editing.id, projectId);
      setTeams((previous) => previous.filter((item) => item.id !== editing.id));
      onChange(null); setEditing(null);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const addMember = () => setEditing(appendTeamMember(editing, catalog, newKind));
  const changeKind = (index, kind) => changeMember(index, { kind, model: kind === 'model' ? catalog.find((model) => model.verified)?.id || catalog[0]?.id || '' : null });
  const options = [...new Map([...teams, ...(team?.id ? [team] : [])].map((item) => [item.id, item])).values()];

  return <section className="mission-team-picker" aria-label="选择项目协作组">
    <div className="mission-team-heading">
      <label><Icon name="group" size={17} /><span>协作组</span><select aria-label="选择协作组" value={team?.id || ''} disabled={busy || loading} onChange={(event) => { onChange(options.find((item) => item.id === event.target.value) || null); setEditing(null); setError(''); }}><option value="">{team && !team.id ? team.name : '临时协作组'}</option>{options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <button className="subtle-button" type="button" disabled={busy} onClick={openEditor}><Icon name="settings" size={14} />成员与分工</button>
      {onOpenAgentSettings && <button className="subtle-button" type="button" onClick={() => onOpenAgentSettings('connections')}><Icon name="plus" size={14} />接入模型 / API Key</button>}
      <button className="subtle-button" type="button" disabled={busy} onClick={() => { setEditing({ name: '', description: '', members: temporaryMembers(models) }); setError(''); }}><Icon name="plus" size={14} />新建组</button>
    </div>
    <div className="mission-team-members">{members.map((member) => <span key={member.id} title={`${memberKindLabel(member)} · ${member.role}`} className={memberKind(member) !== 'model' || available(member.model) ? '' : 'unavailable'}><Icon name={memberKind(member) === 'model' ? 'chat' : 'tasks'} size={13} /><strong>{member.name}</strong><small>{memberKind(member) === 'model' ? catalog.find((model) => model.id === member.model)?.label || member.model : memberKindLabel(member)}</small></span>)}</div>
    {team?.coordinator_model && <p className="mission-coordinator-summary"><Icon name="council" size={14} /><strong>统筹模型（Ruler）</strong><span>{catalog.find((model) => model.id === team.coordinator_model)?.label || team.coordinator_model}</span></p>}
    {missing.length > 0 && <p className="mission-team-warning" role="status">当前未接入：{missing.map((member) => member.model).join('、')}。可以保存分工，运行前请在“成员与分工”更换模型或连接对应账户。</p>}
    {editing && <fieldset disabled={busy} className="mission-team-editor">
      <div className="mission-team-name"><label><span>协作组名称</span><input aria-label="协作组名称" value={editing.name} maxLength={100} placeholder="例如：产品研究组、代码审查组" onChange={(event) => setEditing({ ...editing, name: event.target.value })} /></label><button type="button" className="icon-button" disabled={busy} aria-label="关闭协作组编辑" onClick={() => { setEditing(null); setError(''); }}><Icon name="close" size={16} /></button></div>
      <label className="mission-team-description"><span>用途说明 <small>可选</small></span><input aria-label="协作组用途说明" value={editing.description || ''} maxLength={2000} placeholder="这个团队适合处理什么任务？" onChange={(event) => setEditing({ ...editing, description: event.target.value })} /></label>
      <label className="mission-coordinator-field"><span>统筹模型（Ruler）</span><select aria-label="统筹模型（Ruler）" value={editing.coordinator_model || ''} onChange={(event) => setEditing({ ...editing, coordinator_model: event.target.value || null })}><option value="">不设统筹，按成员顺序讨论</option>{editing.coordinator_model && !available(editing.coordinator_model) && <option value={editing.coordinator_model}>{editing.coordinator_model} · 未接入</option>}{catalog.map((model) => <option key={model.id} value={model.id}>{model.label || model.id}{model.verified ? ' · 已验证' : ''}</option>)}</select><small>项目群广播时先制定计划，成员讨论后再综合结论。@ 指定成员时由该成员回复。Agent 执行任务和代码合入仍由你明确派发或审批。</small></label>
      <div className="mission-member-editors">{editing.members.map((member, index) => <div key={member.id}>
        <div className="mission-member-fields mission-typed-member-fields"><span className="mission-step-number">{index + 1}</span><input aria-label={`成员 ${index + 1} 名称`} value={member.name} maxLength={80} onChange={(event) => changeMember(index, { name: event.target.value })} /><select aria-label={`成员 ${index + 1} 类型`} value={memberKind(member)} onChange={(event) => changeKind(index, event.target.value)}>{Object.entries(MEMBER_KINDS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select><button type="button" className="icon-button" aria-label={`移除成员 ${index + 1}`} disabled={editing.members.length <= 2 || busy} onClick={() => setEditing({ ...editing, members: editing.members.filter((_, position) => position !== index) })}><Icon name="close" size={14} /></button></div>
        {memberKind(member) === 'model' ? <label className="mission-member-model-field"><span>回复模型</span><select aria-label={`成员 ${index + 1} 模型`} value={member.model || ''} onChange={(event) => changeMember(index, { model: event.target.value })}>{!available(member.model) && <option value={member.model || ''}>{member.model || '选择模型'} · 未接入</option>}{catalog.map((model) => <option key={model.id} value={model.id}>{model.label || model.id}{model.verified ? ' · 已验证' : ''}</option>)}</select></label> : memberKind(member) === 'muse' ? <p className="mission-runtime-help">使用本机已登录的 Muse 应用。需要原生桥接就绪；保存成员不会授予系统权限。</p> : <label className="mission-member-model-field"><span>{memberKindLabel(member)} 的执行模型 <small>可选</small></span><input aria-label={`成员 ${index + 1} 执行模型`} value={member.model || ''} maxLength={120} placeholder="留空使用官方 CLI 默认模型" onChange={(event) => changeMember(index, { model: event.target.value || null })} /><small>使用官方 CLI 的现有登录，在隔离项目副本中工作。能否执行以本机登录与执行器检查为准。</small></label>}
        <textarea aria-label={`成员 ${index + 1} 职责`} value={member.role} maxLength={1600} rows={2} placeholder="这个成员负责什么？" onChange={(event) => changeMember(index, { role: event.target.value })} />
      </div>)}</div>
      <div className="mission-team-editor-actions"><select aria-label="添加成员类型" value={newKind} disabled={busy || editing.members.length >= 4} onChange={(event) => setNewKind(event.target.value)}>{Object.entries(MEMBER_KINDS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select><button type="button" className="subtle-button" disabled={busy || editing.members.length >= 4} onClick={addMember}><Icon name="plus" size={14} />添加 Agent</button>{editing.id && <button className="subtle-button" type="button" disabled={busy} onClick={archive}>归档组</button>}<span />{!requireSaved && <button className="subtle-button" type="button" disabled={busy} onClick={useTemporarily}>仅用于本次</button>}<button className="primary-button" type="button" disabled={busy} onClick={save}>{busy ? '正在保存' : editing.id ? '保存变更' : '保存协作组'}</button></div>
      <p>每组 2–4 名成员，可混合 LLM、Codex、Claude Code 和 Muse。统筹模型适用于项目群；单项协作流程仍使用模型成员。保存后可复用，历史任务保留原来的成员与统筹设置。</p>
    </fieldset>}
    {error && <p className="attachment-error" role="alert">{error}{loadError && <button type="button" className="subtle-button" disabled={loading} onClick={() => { setLoading(true); setError(''); setRevision((value) => value + 1); }}>重新连接</button>}</p>}
  </section>;
}
