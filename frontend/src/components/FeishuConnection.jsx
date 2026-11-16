import { useCallback, useEffect, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { feishuApi } from '../feishu-api';
import { FEISHU_SETUP_URL, feishuBinding, feishuBody, feishuConnectionStatus, feishuDraft, feishuIssue, feishuSenderIds, safeFeishuError } from '../feishu-state';
import { groupApi } from '../group-api';
import { useProjects } from '../project-context';
import { machineInfo, NEUTRAL_MACHINE } from '../machine';
import { Icon } from './WorkbenchResults';
import './FeishuConnection.css';

function BindingRoomSelect({ projectId, roomId, disabled, onChange }) {
  const [rooms, setRooms] = useState([]), [loading, setLoading] = useState(true), [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    groupApi.list(projectId).then((items) => {
      if (!disposed) setRooms((Array.isArray(items) ? items : []).filter((room) => (room.project_id || null) === (projectId || null)));
    }).catch((failure) => { if (!disposed) setError(failure.message); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [projectId]);
  return <label><span>ModelDesk 项目群</span><select aria-label="飞书绑定的 ModelDesk 项目群" value={roomId || ''} disabled={disabled || loading} onChange={(event) => onChange(event.target.value)}>
    <option value="">{loading ? '正在读取项目群…' : rooms.length ? '请选择项目群' : '请先在此项目中创建群聊'}</option>
    {roomId && !rooms.some((room) => room.id === roomId) && <option value={roomId}>{roomId} · {loading ? '正在核对' : '未在当前群列表找到'}</option>}
    {rooms.map((room) => <option key={room.id} value={room.id}>{room.title}</option>)}
  </select>{error && <small className="output-error">{error}</small>}</label>;
}

export function FeishuConfigurationForm({ draft, projects = [], chats = [], busy = false, onChange, onSave, renderRoomSelect, machine = NEUTRAL_MACHINE }) {
  const patch = (index, change) => onChange({ ...draft, bindings: draft.bindings.map((binding, at) => at === index ? { ...binding, ...change } : binding) });
  return <form className="connection-editor feishu-config-form" autoComplete="off" onSubmit={onSave}>
    <fieldset disabled={busy}>
      <h3>自建应用凭据</h3>
      <p className="connection-help">一个机器人可以连接多个协作组，凭据只需配置一次。新建 ModelDesk 协作组后，在下方添加或切换聊天绑定即可，无需再创建飞书机器人。</p>
      <label><span>App ID</span><input aria-label="飞书 App ID" value={draft.app_id} required spellCheck={false} autoCapitalize="none" placeholder="cli_…" onChange={(event) => onChange({ ...draft, app_id: event.target.value })} /></label>
      <label><span>App Secret {draft.has_app_secret && <small>已保存 · 留空保留</small>}</span><input aria-label="飞书 App Secret" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} value={draft.app_secret} placeholder={draft.has_app_secret ? '仅在更换密钥时填写' : '从飞书开发者后台复制到这里'} onChange={(event) => onChange({ ...draft, app_secret: event.target.value })} /></label>
      <p className="connection-help">密钥仅保存在{machine.label}的服务中，保存后不会回填到页面，也不会写入浏览器本地存储。</p>
      <h3>自助开通管理员（可选）</h3>
      <p className="connection-help">只有下面列出的这些人的<strong>私聊</strong>可以用 <code>/modeldesk setup</code> 在飞书里直接创建项目、团队和项目群并绑定会话；群聊永远不能自助开通，必须在本机添加绑定。留空则完全关闭飞书内自助开通。</p>
      <label><span>管理员 Open ID（最多 10 个）</span><textarea aria-label="飞书自助开通管理员 Open ID" rows={2} value={draft.owner_text ?? draft.owner_sender_ids.join('\n')} placeholder="每行一个 ou_… 开头的 Open ID；只有这些人可以在私聊里 /modeldesk setup 创建项目" spellCheck={false} onChange={(event) => onChange({ ...draft, owner_text: event.target.value, owner_sender_ids: feishuSenderIds(event.target.value) })} /></label>
      <h3>允许的飞书会话</h3>
      <p className="connection-help">只有下方明确允许的 Chat ID 可以派发任务。每个飞书会话绑定一个 ModelDesk 项目群，沿用该群的角色与项目配置。task 开始新任务；continue 会使用绑定群的共享历史与资料，请只授权可信会话。</p>
      <datalist id="modeldesk-feishu-chat-options">{chats.map((chat) => <option key={chat.chat_id} value={chat.chat_id}>{chat.name || chat.chat_id}</option>)}</datalist>
      {draft.bindings.map((binding, index) => <section className="feishu-binding" key={index} aria-label={`飞书会话绑定 ${index + 1}`}>
        <header><strong>会话 {index + 1}</strong><button type="button" className="subtle-button" onClick={() => onChange({ ...draft, bindings: draft.bindings.filter((_, at) => at !== index) })}>移除绑定</button></header>
        <label><span>飞书 Chat ID</span><input aria-label={`飞书会话 ${index + 1} Chat ID`} value={binding.chat_id} list="modeldesk-feishu-chat-options" required spellCheck={false} placeholder="oc_…（选择已读取的群聊或粘贴精确 ID）" onChange={(event) => patch(index, { chat_id: event.target.value, name: chats.find((chat) => chat.chat_id === event.target.value)?.name || binding.name })} /></label>
        <label><span>会话备注</span><input aria-label={`飞书会话 ${index + 1} 备注`} value={binding.name} maxLength={100} placeholder="例如：我的工作群" onChange={(event) => patch(index, { name: event.target.value })} /></label>
        <div className="feishu-binding-destination"><label><span>ModelDesk 项目</span><select aria-label={`飞书会话 ${index + 1} 项目`} value={binding.project_id || ''} onChange={(event) => patch(index, { project_id: event.target.value || null, room_id: '' })}>
          <option value="">未关联项目的群聊</option>
          {binding.project_id && !projects.some((project) => project.id === binding.project_id) && <option value={binding.project_id}>{binding.project_id} · 项目待核对</option>}
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select></label>{renderRoomSelect ? renderRoomSelect(binding, (room_id) => patch(index, { room_id }), index) : <label><span>ModelDesk 项目群</span><input aria-label={`飞书会话 ${index + 1} 项目群 ID`} value={binding.room_id} required onChange={(event) => patch(index, { room_id: event.target.value })} /></label>}</div>
        <label className="connection-enabled"><input type="checkbox" checked={binding.natural_language_tasks} onChange={(event) => patch(index, { natural_language_tasks: event.target.checked })} /><span>直接用自然语言派发任务</span></label>
        <p className="connection-help">开启后，直接发送需求即可开始新任务；发送“继续：修改要求”沿用该群上下文。群聊中仍需 @机器人，仍受发起人名单与本机权限限制。</p>
        <details className="feishu-binding-advanced"><summary>发起人限制与本机权限</summary>
          <label><span>允许的用户 Open ID（可选）</span><textarea aria-label={`飞书会话 ${index + 1} 允许的用户`} rows={2} value={binding.sender_text ?? binding.allowed_sender_ids.join('\n')} placeholder="留空：允许此会话中的所有用户；每行一个 Open ID 可限制发起人" onChange={(event) => patch(index, { sender_text: event.target.value, allowed_sender_ids: feishuSenderIds(event.target.value) })} /></label>
          <label className="connection-enabled"><input type="checkbox" checked={binding.allow_native} onChange={(event) => patch(index, { allow_native: event.target.checked })} /><span>允许此会话触发{machine.short}原生 Agent（如 Muse）</span></label>
          <p className="connection-help">默认关闭。开启后，此会话中获准的用户可让任务操作本机原生应用；{machine.short}仍需解锁并授予所需权限。代码合入保留审批。</p>
        </details>
      </section>)}
      <button type="button" className="subtle-button" onClick={() => onChange({ ...draft, bindings: [...draft.bindings, feishuBinding()] })}><Icon name="plus" size={14} />允许一个飞书会话</button>
      <label className="connection-enabled"><input type="checkbox" checked={draft.enabled} onChange={(event) => onChange({ ...draft, enabled: event.target.checked })} /><span>启用飞书收发任务</span></label>
      <p className="connection-help">保存后启动官方长连接；仅处理允许会话，按各会话的指令或自然语言设置接收。无需开放公网端口。</p>
      {draft.enabled && !draft.bindings.length && <p className="feishu-unbound-note">尚未允许任何聊天。可以先保存并连接，以完成飞书事件配置和识别 Chat ID；此时不会执行任何聊天指令。之后需明确添加绑定并保存。</p>}
      <div className="connection-save"><button type="submit" className="primary-button">{busy ? '正在保存…' : '保存飞书设置'}</button></div>
    </fieldset>
  </form>;
}

export function FeishuConnectionOverview({ connection, loading, error, notice, busy, chatsAvailable = false, onRefresh, onTest, onDiscover, onOpenGroups, remote = false, machine = NEUTRAL_MACHINE }) {
  const config = connection?.config;
  const status = connection?.status;
  const label = loading ? '正在读取状态' : error ? '状态未确认' : feishuConnectionStatus(status, config);
  return <>
    <div className="agent-section-heading"><div><h2>飞书远程指令与成果</h2><p>在手机飞书发出任务，ModelDesk 在{machine.label}执行，再把状态、成果和可用文件回传原会话。</p></div><span className={`connection-state ${!error && status?.state === 'connected' && config?.enabled ? 'verified' : ''}`}>{remote ? `请在${machine.short}设置` : label}</span></div>
    {error && <div className="error-banner" role="alert">{error}<button type="button" className="subtle-button" onClick={onRefresh}>重新读取</button></div>}
    {notice && <p className="agent-notice" role="status">{notice}</p>}
    {!remote && status?.reason && <p className="connection-help">连接说明：{status.reason}</p>}
    <ol className="feishu-setup-steps">
      <li>在<a href="https://open.feishu.cn/app" target="_blank" rel="noreferrer">飞书开发者后台</a>创建企业自建应用，开启机器人能力并配置收发消息权限。</li>
      <li>填写下方凭据并启用连接，再在“事件与回调”选择“使用长连接接收事件”，订阅 <code>im.message.receive_v1</code> 并发布应用。<a href={FEISHU_SETUP_URL} target="_blank" rel="noreferrer">官方长连接配置指南</a></li>
      <li>把机器人加入目标会话，发送一条消息（群聊中需 @机器人）。从下方“最近收到的未绑定会话”添加对应 Chat ID，选择项目群并保存；也可读取机器人可见的群聊或粘贴已核对的 ID。未绑定时不会执行任务。</li>
    </ol>
    {config?.app_id && <p className="connection-help"><a href={`https://applink.feishu.cn/client/bot/open?appId=${encodeURIComponent(config.app_id)}`} target="_blank" rel="noreferrer">直接打开已配置的飞书机器人</a>。手机搜索不到时，可在手机打开此链接，并确认当前飞书账号和组织在应用的可用范围内。</p>}
    <details className="feishu-required-permissions"><summary>需要开通哪些飞书权限？</summary><ul>
      <li><code>im:message.p2p_msg:readonly</code>：读取发给机器人的单聊消息</li>
      <li><code>im:message.group_at_msg:readonly</code>：接收群聊中 @机器人的消息</li>
      <li><code>im:message:send_as_bot</code>：以机器人身份回复</li>
      <li><code>im:resource</code>：回传文件成果</li>
      <li><code>im:chat:read</code> 或 <code>im:chat:readonly</code>：读取可用群聊列表（用于选择 Chat ID）</li>
    </ul><p>权限与事件配置完成后，发布应用版本，让目标用户可使用该机器人。</p></details>
    {!remote && config?.enabled && !config.bindings?.length && <p className="feishu-unbound-note">{status?.state === 'connected' ? '已连接，但尚未允许任何聊天。' : '尚未允许任何聊天。'}可先完成事件订阅、识别 Chat ID，再添加绑定。未绑定会话不会执行指令或回传成果。</p>}
    <div className="feishu-command-guide"><strong>绑定后，在飞书发送</strong><p>为会话开启“直接用自然语言派发任务”后，可直接发送“帮我完成一份竞品分析报告”；使用“继续：修改上次报告的结论”保留上下文。以下指令始终可用：</p><code>/modeldesk task 帮我完成一份竞品分析报告</code><code>/modeldesk continue 修改上次报告的结论</code><code>/modeldesk status</code><p><code>task</code> 从新任务开始，不带群聊旧对话；<code>continue</code> 会使用绑定群的共享历史与资料，同群其他成员的记录也可能被引用。群聊中请 @机器人。停止任务：<code>/modeldesk cancel 完整任务UUID</code>。收发是否成功，以飞书中的实际回执为准；“长连接在线”不代表任务已经完成。</p></div>
    <p className="connection-help">{machine.short}与 ModelDesk 服务需要保持运行并联网；原生应用操作还需要{machine.short}解锁。此连接采用官方出站长连接，无需配置 Webhook 或内网穿透。</p>
    <div className="feishu-command-guide"><strong>还没有任何绑定时</strong><p>配置了管理员 Open ID 后，管理员可直接在飞书<strong>私聊</strong>里发送 <code>/modeldesk setup 项目名</code>（可选 <code>with 模型1,模型2</code> 指定成员模型）自助创建项目、团队和项目群并绑定当前会话，再用 <code>/modeldesk confirm 4位确认码</code> 二次确认执行；<code>/modeldesk unlink</code> 可解除当前会话绑定。群聊不支持自助开通。</p><code>/modeldesk setup 竞品分析</code><code>/modeldesk setup 竞品分析 with qwen3.8-max,glm-5.3</code><code>/modeldesk confirm 1234</code><code>/modeldesk unlink</code></div>
    {remote ? <p className="agent-remote-note">App Secret 和允许会话在{machine.short}的“Agent 与连接 → 飞书远程”配置。完成后，可直接从手机飞书派发任务和接收结果。</p> : <div className="agent-inline-actions feishu-connection-actions"><button type="button" className="subtle-button" disabled={busy || loading} onClick={onRefresh}>刷新连接状态</button><button type="button" className="subtle-button" disabled={busy || !config?.app_id || !config?.has_app_secret} onClick={onTest}>验证已保存的应用凭据</button><button type="button" className="subtle-button" disabled={busy || !config?.app_id || !config?.has_app_secret} onClick={onDiscover}>{chatsAvailable ? '继续读取可用群聊' : '读取可用群聊'}</button><button type="button" className="subtle-button" onClick={onOpenGroups}>打开项目群</button></div>}
  </>;
}

export function FeishuRecentReceipts({ recent = [] }) {
  const records = (Array.isArray(recent) ? recent : [])
    .filter((item) => item && typeof item.id === 'string')
    .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''))).slice(0, 5);
  const states = { received: '已接收指令', dispatching: '正在派发', monitoring: '正在等待任务', attention: '任务有待处理事项', delivered: '回传已确认', needs_attention: '收发需要处理', rejected: '指令已拒绝' };
  const timeLabel = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };
  return <details className="feishu-recent-receipts">
    <summary>最近指令与回传（{records.length}）</summary>
    <p>展示最近 5 条实际收发记录。任务执行结果与飞书回传分别记录；文件权限、大小或送达未确认等问题会保留在这里。</p>
    {!records.length ? <p>暂无指令回执。绑定并保存允许的会话后，可从飞书发送 /modeldesk 指令。</p> : <ol>{records.map((record) => <li key={record.id}>
      <header><strong className={['attention', 'needs_attention', 'rejected'].includes(record.state) ? 'feishu-receipt-issue' : ''}>{states[record.state] || '状态待核对'}</strong><time dateTime={record.updated_at || record.created_at}>{timeLabel(record.updated_at || record.created_at)}</time></header>
      {typeof record.reason === 'string' && record.reason && <p>{record.reason}</p>}
      <div>{typeof record.chat_id === 'string' && record.chat_id && <code>飞书会话：{record.chat_id}</code>}{typeof record.job_id === 'string' && record.job_id && <code>关联任务：{record.job_id}</code>}</div>
    </li>)}</ol>}
  </details>;
}

export default function FeishuConnection({ onOpenGroups, status }) {
  const machine = machineInfo(status);
  const { projects, activeProjectId } = useProjects();
  const [connection, setConnection] = useState(null), [draft, setDraft] = useState(null), [loading, setLoading] = useState(!REMOTE_CLIENT), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [loadError, setLoadError] = useState('');
  const [chats, setChats] = useState([]), [pageToken, setPageToken] = useState(null);
  const alive = useRef(true), secretRef = useRef('');
  secretRef.current = draft?.app_secret || '';
  const reload = useCallback(async (initializeDraft = false) => {
    try {
      const result = await feishuApi.get();
      if (alive.current) { setConnection(result); if (initializeDraft) setDraft(feishuDraft(result.config)); setLoadError(''); }
    } catch (failure) { if (alive.current) setLoadError(safeFeishuError(failure, secretRef.current)); }
    finally { if (alive.current) setLoading(false); }
  }, []);
  useEffect(() => {
    alive.current = true;
    if (REMOTE_CLIENT) return () => { alive.current = false; };
    reload(true);
    const timer = setInterval(() => reload(), 8000);
    return () => { alive.current = false; clearInterval(timer); };
  }, [reload]);
  const perform = async (action) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await action(); }
    catch (failure) { if (alive.current) setError(safeFeishuError(failure, secretRef.current)); }
    finally { if (alive.current) setBusy(false); }
  };
  const save = (event) => {
    event.preventDefault();
    const issue = feishuIssue(draft);
    if (issue) { setError(issue); return; }
    perform(async () => {
      const result = await feishuApi.save(feishuBody(draft));
      if (!alive.current) return;
      setConnection(result); setDraft(feishuDraft(result.config)); setChats([]); setPageToken(null);
      setNotice('飞书设置已保存。请核对上方的实际连接状态，再从允许的飞书会话发送任务验证收发。');
    });
  };
  const test = () => perform(async () => {
    const result = await feishuApi.test();
    if (!result.ok) throw new Error(result.message || '应用凭据验证未通过，请检查配置。');
    if (alive.current) setNotice(`${result.message || '应用凭据验证通过。'} 尚未验证飞书消息收发，请从允许的会话发送任务。`);
    await reload();
  });
  const discover = () => perform(async () => {
    const result = await feishuApi.chats(pageToken);
    if (!alive.current) return;
    const items = Array.isArray(result.items) ? result.items.filter((item) => typeof item.chat_id === 'string') : [];
    setChats((previous) => [...new Map([...previous, ...items].map((item) => [item.chat_id, item])).values()]);
    setPageToken(result.has_more && typeof result.page_token === 'string' ? result.page_token : null);
    setNotice(items.length ? '已读取机器人可见的会话。仍需在下方选择或粘贴 Chat ID 并保存绑定。' : '暂未读取到会话。请把机器人加入目标群，或填写你已核对的 Chat ID。');
  });
  const unboundChats = (Array.isArray(connection?.observed_chats) ? connection.observed_chats : [])
    .filter((item) => typeof item.chat_id === 'string' && !draft?.bindings.some((binding) => binding.chat_id === item.chat_id));
  const latestObserved = (connection?.observed_chats || []).filter((item) => typeof item.last_reason === 'string')
    .sort((a, b) => String(b.last_seen_at).localeCompare(String(a.last_seen_at)))[0];
  return <section className="feishu-connection">
    <FeishuConnectionOverview connection={connection} loading={loading} error={error || loadError} notice={notice} busy={busy} remote={REMOTE_CLIENT} chatsAvailable={Boolean(pageToken)} machine={machine} onRefresh={() => { setError(''); reload(!draft); }} onTest={test} onDiscover={discover} onOpenGroups={onOpenGroups} />
    {!REMOTE_CLIENT && connection && <FeishuRecentReceipts recent={connection.recent} />}
    {!REMOTE_CLIENT && latestObserved && <p className="connection-help" role="status">最近一条手机消息：{latestObserved.last_reason}</p>}
    {!REMOTE_CLIENT && draft && <FeishuConfigurationForm draft={draft} projects={projects} chats={chats} busy={busy} machine={machine} onChange={setDraft} onSave={save} renderRoomSelect={(binding, onChange, index) => <BindingRoomSelect key={`${index}:${binding.project_id || 'unassigned'}`} projectId={binding.project_id} roomId={binding.room_id} disabled={busy} onChange={onChange} />} />}
    {!REMOTE_CLIENT && draft && <details className="feishu-observed-chats"><summary>最近收到的未绑定会话（{unboundChats.length}）</summary><p>{unboundChats.length ? '这些会话只保留识别信息，尚未授权执行。你需要添加绑定并保存，才能接受对应会话的指令。' : '暂无未绑定会话。长连接在线并保存事件配置后，向机器人发送消息，再刷新连接状态。'}</p><ul>{unboundChats.map((chat) => <li key={chat.chat_id}><div><code>{chat.chat_id}</code><small>{chat.chat_type === 'p2p' ? '单聊' : '群聊'}{chat.last_sender_id ? ` · 最近发起人：${chat.last_sender_id}` : ''}</small></div><button type="button" className="subtle-button" disabled={busy} onClick={() => setDraft((previous) => ({ ...previous, bindings: [...previous.bindings, feishuBinding({ chat_id: chat.chat_id, project_id: activeProjectId })] }))}>添加为允许会话</button></li>)}</ul></details>}
  </section>;
}
