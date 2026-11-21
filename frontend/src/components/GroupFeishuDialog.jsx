import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { feishuApi } from '../feishu-api';
import {
  feishuBinding,
  feishuSenderIds,
  roomFeishuSummary,
  safeFeishuError,
} from '../feishu-state';
import { CopyTextButton } from './ConversationActions';
import { Icon } from './WorkbenchResults';
import './GroupFeishuDialog.css';

const RECEIPT_STATES = {
  received: '已接收指令',
  dispatching: '正在派发',
  monitoring: '正在等待任务',
  attention: '任务有待处理事项',
  delivered: '回传已确认',
  needs_attention: '收发需要处理',
  rejected: '指令已拒绝',
};

const CONNECTION_LABELS = {
  connected: '长连接在线',
  connecting: '正在连接',
  disconnected: '长连接离线',
  disabled: '连接已停用',
  not_configured: '未配置机器人',
};

export function GroupFeishuAccess({ access, error, remote = REMOTE_CLIENT, onRetry, onConfigure, onManage }) {
  if (error) return <section className="group-feishu-callout">
    <p role="alert">{error}</p>
    <div className="group-feishu-callout-actions"><button type="button" className="subtle-button" onClick={onRetry}>重试</button></div>
  </section>;
  if (!access) return <p className="group-feishu-loading" role="status">正在读取飞书机器人连接状态…</p>;

  if (access.status === 'not_configured' || access.mode === 'none') {
    const configured = access.status !== 'not_configured';
    return <section className="group-feishu-callout">
      <h3>{configured ? '本群尚未接入飞书会话' : '先配置飞书机器人'}</h3>
      <p>{configured
        ? '可绑定一个飞书会话到本群，或让已有会话访问全部 ModelDesk 项目群。'
        : '在运行 ModelDesk 的电脑上配置一次机器人凭据，即可将它用于多个项目群。'}</p>
      {!remote && <div className="group-feishu-callout-actions">
        <button type="button" className="primary-button" onClick={configured ? onManage : onConfigure}>{configured ? '绑定或复用飞书会话' : '配置飞书机器人'}</button>
      </div>}
      {remote && configured && <p>请在运行 ModelDesk 的电脑上打开本群的「飞书机器人」完成接入。</p>}
    </section>;
  }

  const shared = access.mode === 'all_groups';
  const heading = shared ? '本群已接入飞书' : '本群已绑定飞书会话';
  if (access.status !== 'connected') return <section className="group-feishu-callout">
    <h3>{heading}</h3>
    <p>长连接在线后即可收发任务。</p>
    <div className="group-feishu-callout-actions"><button type="button" className="subtle-button" onClick={onRetry}>刷新连接状态</button></div>
  </section>;

  const switchCommand = access.switch_command;
  const taskCommand = shared ? `/modeldesk to ${access.room_id} ` : '/modeldesk task ';
  return <section className="group-feishu-callout">
    <h3>{heading}</h3>
    <p>{access.destination === 'private' && access.bot_url
      ? '打开机器人私聊，将指令粘贴到该会话。'
      : access.destination === 'group'
        ? '打开已接入的飞书群，@ 机器人后粘贴指令。'
        : '打开已接入的飞书会话并粘贴指令。在飞书群内请先 @ 机器人。'}</p>
    {access.destination === 'private' && access.bot_url && <div className="group-feishu-callout-actions">
      <a className="subtle-button group-feishu-bot-link" href={access.bot_url} target="_blank" rel="noreferrer">打开飞书机器人</a>
    </div>}
    <div className="group-feishu-cmd-list">
      {switchCommand && <div className="group-feishu-cmd-row">
        <code>{switchCommand}</code><CopyTextButton text={switchCommand} label="复制切换指令" />
      </div>}
      <div className="group-feishu-cmd-row">
        <code>{taskCommand}任务内容</code><CopyTextButton text={taskCommand} label="复制任务指令" />
      </div>
    </div>
    <p>{shared ? '用任务指令可单次指定本群。将指令粘贴到飞书后，在末尾填写实际任务。' : '将任务指令粘贴到飞书后，在末尾填写实际任务。'}</p>
  </section>;
}

export function FeishuReuseChoices({ bindings, busy, onEnable }) {
  const inputId = useId();
  const [chatId, setChatId] = useState('');
  const selected = bindings.length === 1 ? bindings[0] : bindings.find((binding) => binding.chat_id === chatId);
  return <section className="group-feishu-shortcut-card">
    <div className="group-feishu-shortcut-text">
      <strong>复用已有飞书会话</strong>
      <p>允许已有会话中的授权成员访问并切换到全部 ModelDesk 项目群。</p>
      {bindings.length === 1 ? <p>飞书会话 · {selected.name || selected.chat_id}</p> : (
        <div className="group-feishu-field">
          <label htmlFor={inputId}>选择要复用的飞书会话</label>
          <select id={inputId} value={selected?.chat_id || ''} onChange={(event) => setChatId(event.target.value)} disabled={busy}>
            <option value="">请选择会话</option>
            {bindings.map((binding) => <option key={binding.chat_id} value={binding.chat_id}>
              {binding.name ? `${binding.name} · ${binding.chat_id}` : binding.chat_id}
            </option>)}
          </select>
        </div>
      )}
    </div>
    <button type="button" className="primary-button" disabled={busy || !selected} onClick={() => onEnable(selected)}>开启全部项目群接入</button>
  </section>;
}

function receiptTimeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function GroupFeishuDialog({
  room,
  projectId,
  access,
  accessError,
  onRetry,
  onOpenFeishuSettings,
  onClose,
}) {
  const dialogRef = useRef(null);
  const aliveRef = useRef(true);
  const busyRef = useRef(false);
  const closedRef = useRef(false);
  const onCloseRef = useRef(onClose);

  const datalistId = useId();
  const chatIdInputId = useId();
  const nameInputId = useId();
  const senderInputId = useId();

  const [adminOpen, setAdminOpen] = useState(false);
  const [effectiveData, setLocalData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [chats, setChats] = useState([]);
  const [pageToken, setPageToken] = useState(null);
  const [editingChatId, setEditingChatId] = useState(null);
  const [chatId, setChatId] = useState('');
  const [name, setName] = useState(room?.title || '');
  const [naturalLanguageTasks, setNaturalLanguageTasks] = useState(true);
  const [allowAllGroups, setAllowAllGroups] = useState(false);
  const [pushGroupResults, setPushGroupResults] = useState(false);
  const [senderText, setSenderText] = useState('');
  const [allowNative, setAllowNative] = useState(false);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [enableConnection, setEnableConnection] = useState(true);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    aliveRef.current = true;
    closedRef.current = false;
    const dialog = dialogRef.current;
    if (dialog && !dialog.open && typeof dialog.showModal === 'function') {
      dialog.showModal();
    }
    const handleClose = () => {
      if (closedRef.current) return;
      closedRef.current = true;
      onCloseRef.current?.();
    };
    const handleCancel = (event) => {
      if (busyRef.current) event.preventDefault();
    };
    dialog?.addEventListener('close', handleClose);
    dialog?.addEventListener('cancel', handleCancel);

    let removeLightDismiss;
    if (dialog && typeof HTMLDialogElement !== 'undefined' && !('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (event) => {
        const rect = dialog.getBoundingClientRect();
        return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
      };
      let pressedOutside = false;
      const onPointerDown = (event) => {
        pressedOutside = outside(event);
      };
      const onPointerUp = (event) => {
        if (pressedOutside && outside(event) && !busyRef.current) {
          dialog.close();
        }
        pressedOutside = false;
      };
      dialog.addEventListener('pointerdown', onPointerDown);
      dialog.addEventListener('pointerup', onPointerUp);
      removeLightDismiss = () => {
        dialog.removeEventListener('pointerdown', onPointerDown);
        dialog.removeEventListener('pointerup', onPointerUp);
      };
    }

    return () => {
      aliveRef.current = false;
      dialog?.removeEventListener('close', handleClose);
      dialog?.removeEventListener('cancel', handleCancel);
      removeLightDismiss?.();
    };
  }, []);

  const pid = projectId || null;
  const roomId = room?.id || '';
  const roomTitle = room?.title || '当前项目群';
  const summary = useMemo(
    () => roomFeishuSummary(effectiveData, pid, roomId),
    [effectiveData, pid, roomId],
  );
  const config = effectiveData?.config && typeof effectiveData.config === 'object' ? effectiveData.config : null;
  const roomsCatalog = useMemo(
    () => (Array.isArray(effectiveData?.rooms) ? effectiveData.rooms : []),
    [effectiveData],
  );
  const allBindings = useMemo(
    () => (Array.isArray(config?.bindings) ? config.bindings.map(feishuBinding) : []),
    [config],
  );
  const reusableBindings = useMemo(
    () => allBindings.filter((item) => !item.allow_all_groups && item.chat_id && roomsCatalog.some((entry) => entry.id === item.room_id && (entry.project_id || null) === item.project_id)),
    [allBindings, roomsCatalog],
  );

  const quickPickChats = useMemo(() => {
    const map = new Map();
    for (const item of chats) {
      if (typeof item?.chat_id === 'string' && item.chat_id) {
        map.set(item.chat_id, {
          chat_id: item.chat_id,
          name: item.name || '',
          chat_type: item.chat_type || 'group',
          source: 'discovered',
        });
      }
    }
    const observed = Array.isArray(effectiveData?.observed_chats) ? effectiveData.observed_chats : [];
    for (const item of observed) {
      if (typeof item?.chat_id === 'string' && item.chat_id && !map.has(item.chat_id)) {
        map.set(item.chat_id, {
          chat_id: item.chat_id,
          name: item.name || '',
          chat_type: item.chat_type || 'group',
          source: 'observed',
        });
      }
    }
    return [...map.values()];
  }, [chats, effectiveData]);

  const trimmedChatId = chatId.trim();
  const existingOther = useMemo(() => {
    if (!trimmedChatId) return null;
    return allBindings.find((item) => (
      item.chat_id === trimmedChatId
      && !(item.room_id === roomId && (item.project_id || null) === pid)
    )) || null;
  }, [allBindings, trimmedChatId, roomId, pid]);

  const existingOtherLabel = useMemo(() => {
    if (!existingOther) return '';
    const match = roomsCatalog.find(
      (entry) => entry.id === existingOther.room_id && (entry.project_id || null) === (existingOther.project_id || null),
    );
    if (match) {
      return match.project_name ? `${match.project_name} · ${match.title}` : match.title;
    }
    return existingOther.name || existingOther.room_id;
  }, [existingOther, roomsCatalog]);

  const roomReceipts = useMemo(() => {
    const recent = Array.isArray(effectiveData?.recent) ? effectiveData.recent : [];
    const activeIds = new Set([
      ...summary.directBindings.map((item) => item.chat_id),
      ...summary.allGroupsBindings.map((item) => item.chat_id),
    ].filter(Boolean));
    if (!activeIds.size) return [];
    return recent
      .filter((item) => item && typeof item.id === 'string' && activeIds.has(item.chat_id))
      .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')))
      .slice(0, 5);
  }, [effectiveData, summary.directBindings, summary.allGroupsBindings]);

  const requestClose = () => {
    if (busy) return;
    if (dialogRef.current?.open) dialogRef.current.close();
    else if (!closedRef.current) {
      closedRef.current = true;
      onClose?.();
    }
  };

  const openGlobalSettings = () => {
    if (busy) return;
    closedRef.current = true;
    onClose?.();
    onOpenFeishuSettings?.();
  };

  const resetForm = () => {
    setEditingChatId(null);
    setChatId('');
    setName(roomTitle);
    setNaturalLanguageTasks(true);
    setAllowAllGroups(false);
    setPushGroupResults(false);
    setSenderText('');
    setAllowNative(false);
    setReplaceExisting(false);
  };

  const loadBindingForEdit = (binding) => {
    setError('');
    setNotice('');
    setEditingChatId(binding.chat_id);
    setChatId(binding.chat_id);
    setName(binding.name || roomTitle);
    setNaturalLanguageTasks(binding.natural_language_tasks === true);
    setAllowAllGroups(binding.allow_all_groups === true);
    setPushGroupResults(binding.push_group_results === true);
    setSenderText((binding.allowed_sender_ids || []).join('\n'));
    setAllowNative(binding.allow_native === true);
    setReplaceExisting(false);
  };

  const applyServerUpdate = (nextData, message) => {
    setLocalData(nextData);
    window.dispatchEvent(new Event('modeldesk-feishu-changed'));
    if (message) setNotice(message);
  };

  const runAction = async (action) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      if (aliveRef.current) {
        setError(safeFeishuError(failure));
        if (failure?.status === 409 || failure?.status === 404) {
          setLocalData(null);
          onRetry?.();
          try {
            const fresh = await feishuApi.rooms();
            if (aliveRef.current) setLocalData(fresh);
          } catch { /* The user can retry reading the local settings. */ }
        }
      }
    } finally {
      busyRef.current = false;
      if (aliveRef.current) setBusy(false);
    }
  };

  const openAdmin = () => {
    setAdminOpen(true);
    if (!effectiveData) runAction(async () => {
      const data = await feishuApi.rooms();
      if (aliveRef.current) setLocalData(data);
    });
  };

  const discoverChats = () => runAction(async () => {
    const result = await feishuApi.chats(pageToken);
    if (!aliveRef.current) return;
    const items = Array.isArray(result.items) ? result.items.filter((item) => typeof item.chat_id === 'string') : [];
    setChats((previous) => [...new Map([...previous, ...items].map((item) => [item.chat_id, item])).values()]);
    setPageToken(result.has_more && typeof result.page_token === 'string' ? result.page_token : null);
    setNotice(items.length
      ? '已读取可用飞书会话，点击下方会话卡片即可填入 Chat ID。'
      : '暂未读取到飞书群聊。请先将机器人加入目标群或给机器人发一条消息，也可直接粘贴精确的 Chat ID。');
  });

  const enableExistingForAllGroups = (targetBinding) => runAction(async () => {
    await feishuApi.enableAllGroups(targetBinding.room_id, targetBinding.chat_id, targetBinding.project_id);
    if (!aliveRef.current) return;
    setAdminOpen(false);
    applyServerUpdate(null, '已开启全群接入。现在可以复制指令，指定当前项目群。');
  });

  const removeBinding = (targetChatId) => runAction(async () => {
    const nextData = await feishuApi.removeRoomBinding(roomId, targetChatId, pid);
    if (!aliveRef.current) return;
    if (editingChatId === targetChatId) resetForm();
    applyServerUpdate(nextData, '已解除该飞书会话与当前群的绑定。');
  });

  const submitBinding = (event) => {
    event.preventDefault();
    if (!trimmedChatId) {
      setError('请填写或选择一个飞书 Chat ID。');
      return;
    }
    if (existingOther && !replaceExisting) {
      setError(`此飞书会话已绑定到「${existingOtherLabel}」。请勾选确认改绑，或改用「允许访问全部项目群」。`);
      return;
    }
    runAction(async () => {
      const nextData = await feishuApi.saveRoomBinding(roomId, {
        chat_id: trimmedChatId,
        name: (name.trim() || roomTitle).slice(0, 100),
        project_id: pid,
        allowed_sender_ids: feishuSenderIds(senderText),
        allow_native: allowNative,
        natural_language_tasks: naturalLanguageTasks,
        allow_all_groups: allowAllGroups,
        push_group_results: pushGroupResults,
        replace_existing: Boolean(existingOther && replaceExisting),
        enable_connection: !config?.enabled ? enableConnection : false,
      });
      if (!aliveRef.current) return;
      resetForm();
      applyServerUpdate(nextData, '当前群的飞书绑定已保存。');
    });
  };

  const statusTone = access?.status === 'connected' ? 'online' : access && access.status !== 'not_configured' ? 'idle' : 'unconfigured';

  return (
    <dialog
      ref={dialogRef}
      closedby="any"
      className="group-feishu-dialog"
      aria-labelledby="group-feishu-heading"
    >
      <div className="group-feishu-frame">
        <header className="group-feishu-header">
          <div className="group-feishu-title-group">
            <span className={`group-feishu-icon ${access?.status === 'connected' && access.mode !== 'none' ? 'connected' : ''}`} aria-hidden="true">
              <Icon name="phone" size={19} />
            </span>
            <div className="group-feishu-heading-text">
              <h2 id="group-feishu-heading">飞书机器人连接</h2>
              <p title={roomTitle}>
                {roomTitle}
              </p>
            </div>
          </div>
          <div className="group-feishu-header-right">
            <span className={`group-feishu-pill ${statusTone}`}>
              <span className="group-feishu-pill-dot" aria-hidden="true" />
              {accessError ? '状态读取失败' : access ? CONNECTION_LABELS[access.status] : '正在读取状态'}
            </span>
            <button
              type="button"
              className="icon-button"
              aria-label="关闭飞书连接"
              disabled={busy}
              onClick={requestClose}
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        </header>

        <div className="group-feishu-body">
          {error && <div className="group-feishu-error" role="alert">{error}</div>}
          {notice && <p className="group-feishu-notice" role="status">{notice}</p>}

          <GroupFeishuAccess access={access} error={accessError} onRetry={onRetry} onConfigure={openGlobalSettings} onManage={openAdmin} />
          {!REMOTE_CLIENT && access && access.status !== 'not_configured' && <div className="group-feishu-bot-actions">
            {access.mode !== 'none' && <button type="button" className="subtle-button" aria-expanded={adminOpen} onClick={adminOpen ? () => setAdminOpen(false) : openAdmin}>{adminOpen ? '收起会话绑定' : '管理飞书会话绑定'}</button>}
            <button type="button" className="subtle-button" onClick={openGlobalSettings}>飞书连接设置</button>
          </div>}
          {!REMOTE_CLIENT && adminOpen && !accessError && (effectiveData ? (
            <>
              {summary.directBindings.length > 0 && <section className="group-feishu-section">
                <h3>本群专属绑定</h3>
                <div className="group-feishu-binding-list">
                  {summary.directBindings.map((binding) => (
                    <article className="group-feishu-binding-card" key={binding.chat_id}>
                      <div className="group-feishu-binding-top">
                        <div className="group-feishu-binding-info">
                          <strong>{binding.name || '已绑定的飞书会话'}</strong>
                          <code>{binding.chat_id}</code>
                        </div>
                        <div className="group-feishu-binding-actions">
                          <button
                            type="button"
                            className="subtle-button"
                            aria-label={`编辑 ${binding.name || binding.chat_id}`}
                            disabled={busy}
                            onClick={() => loadBindingForEdit(binding)}
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            className="subtle-button group-feishu-danger-btn"
                            aria-label={`解除绑定 ${binding.name || binding.chat_id}`}
                            disabled={busy}
                            onClick={() => removeBinding(binding.chat_id)}
                          >
                            解除绑定
                          </button>
                        </div>
                      </div>
                      <div className="group-feishu-badges">
                        <span className="group-feishu-badge primary">专属绑定</span>
                        {binding.natural_language_tasks && <span className="group-feishu-badge">自然语言派发</span>}
                        {binding.allow_all_groups && <span className="group-feishu-badge accent">全群可切换</span>}
                        {binding.push_group_results && <span className="group-feishu-badge jade">自动推送成果</span>}
                        {binding.allow_native && <span className="group-feishu-badge warn">原生 Agent</span>}
                      </div>
                    </article>
                  ))}
                </div>
              </section>}
              {access?.mode === 'none' && reusableBindings.length > 0 && <FeishuReuseChoices bindings={reusableBindings} busy={busy} onEnable={enableExistingForAllGroups} />}

              <form className="group-feishu-form" autoComplete="off" onSubmit={submitBinding}>
                <div className="group-feishu-form-header">
                  <h3>{editingChatId ? '编辑当前群的飞书绑定' : '将飞书会话绑定到此群'}</h3>
                  {editingChatId && (
                    <button type="button" className="subtle-button" disabled={busy} onClick={resetForm}>
                      取消编辑
                    </button>
                  )}
                </div>

                <div className="group-feishu-discover-bar">
                  <button type="button" className="subtle-button" disabled={busy} onClick={discoverChats}>
                    <Icon name="refresh" size={14} />
                    {pageToken ? '继续读取可用飞书会话' : '读取可用飞书会话'}
                  </button>
                  <span className="group-feishu-hint">可读取机器人所在群聊，或从最近收到的飞书会话快速填入。</span>
                </div>

                {quickPickChats.length > 0 && (
                  <div className="group-feishu-quick-picks" role="group" aria-label="快速选择飞书会话">
                    {quickPickChats.map((item) => (
                      <button
                        key={item.chat_id}
                        type="button"
                        className={`group-feishu-pick-chip${trimmedChatId === item.chat_id ? ' selected' : ''}`}
                        disabled={busy}
                        onClick={() => {
                          setChatId(item.chat_id);
                          setReplaceExisting(false);
                          if (item.name) setName(item.name);
                          else if (!name.trim()) setName(roomTitle);
                        }}
                      >
                        <span>{item.name || item.chat_id}</span>
                        <small>{item.chat_type === 'p2p' ? '单聊' : '群聊'}</small>
                      </button>
                    ))}
                  </div>
                )}

                <datalist id={datalistId}>
                  {quickPickChats.map((item) => (
                    <option key={item.chat_id} value={item.chat_id}>
                      {item.name || item.chat_id}
                    </option>
                  ))}
                </datalist>

                <fieldset className="group-feishu-fields" disabled={busy}>
                  <div className="group-feishu-field-grid">
                    <div className="group-feishu-field">
                      <label htmlFor={chatIdInputId}>飞书 Chat ID</label>
                      <input
                        id={chatIdInputId}
                        value={chatId}
                        list={datalistId}
                        required
                        spellCheck={false}
                        autoCapitalize="none"
                        placeholder="oc_…（选择已读取的会话或粘贴精确 ID）"
                        onChange={(event) => {
                          const nextVal = event.target.value;
                          setChatId(nextVal);
                          setReplaceExisting(false);
                          const matched = quickPickChats.find((item) => item.chat_id === nextVal.trim());
                          if (matched?.name) setName(matched.name);
                        }}
                      />
                    </div>
                    <div className="group-feishu-field">
                      <label htmlFor={nameInputId}>会话备注</label>
                      <input
                        id={nameInputId}
                        value={name}
                        maxLength={100}
                        placeholder={roomTitle}
                        onChange={(event) => setName(event.target.value)}
                      />
                    </div>
                  </div>

                  {existingOther && (
                    <div className="group-feishu-conflict" role="status">
                      <p>
                        <strong>此飞书会话已绑定到「{existingOtherLabel}」</strong>
                      </p>
                      <p>
                        提示：如果希望同一个飞书会话同时使用两个项目群，推荐改为对原绑定开启「允许访问与切换全部 ModelDesk 项目群」，两个群都会保持连通。
                      </p>
                      <div className="group-feishu-conflict-actions">
                        <label className="group-feishu-check">
                          <input
                            type="checkbox"
                            checked={replaceExisting}
                            onChange={(event) => setReplaceExisting(event.target.checked)}
                          />
                          <span>确认将此飞书会话改绑到当前群</span>
                        </label>
                        {!existingOther.allow_all_groups && (
                          <button
                            type="button"
                            className="subtle-button"
                            disabled={busy}
                            onClick={() => enableExistingForAllGroups(existingOther)}
                          >
                            改为开启全群接入
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="group-feishu-toggles">
                    <label className="group-feishu-check">
                      <input
                        type="checkbox"
                        checked={naturalLanguageTasks}
                        onChange={(event) => setNaturalLanguageTasks(event.target.checked)}
                      />
                      <span>直接用自然语言派发任务</span>
                    </label>
                    <label className="group-feishu-check">
                      <input
                        type="checkbox"
                        checked={allowAllGroups}
                        onChange={(event) => setAllowAllGroups(event.target.checked)}
                      />
                      <span>允许此会话访问与切换全部 ModelDesk 项目群</span>
                    </label>
                    <label className="group-feishu-check">
                      <input
                        type="checkbox"
                        checked={pushGroupResults}
                        onChange={(event) => setPushGroupResults(event.target.checked)}
                      />
                      <span>将本群在桌面端 / 自动化完成的成果自动推送到飞书</span>
                    </label>
                  </div>

                  <details className="group-feishu-advanced">
                    <summary>发起人限制与本机权限</summary>
                    <div className="group-feishu-advanced-body">
                      <div className="group-feishu-field">
                        <label htmlFor={senderInputId}>允许的用户 Open ID（可选）</label>
                        <textarea
                          id={senderInputId}
                          rows={2}
                          value={senderText}
                          spellCheck={false}
                          placeholder="留空：允许此会话中的所有用户；每行一个 ou_… 开头的 Open ID 可限制发起人"
                          onChange={(event) => setSenderText(event.target.value)}
                        />
                      </div>
                      <label className="group-feishu-check">
                        <input
                          type="checkbox"
                          checked={allowNative}
                          onChange={(event) => setAllowNative(event.target.checked)}
                        />
                        <span>允许此会话触发主机上的原生 Agent（如 Muse）</span>
                      </label>
                    </div>
                  </details>

                  {!config?.enabled && (
                    <label className="group-feishu-check group-feishu-enable-toggle">
                      <input
                        type="checkbox"
                        checked={enableConnection}
                        onChange={(event) => setEnableConnection(event.target.checked)}
                      />
                      <span>保存同时启用飞书长连接</span>
                    </label>
                  )}

                  <div className="group-feishu-submit-row">
                    <button
                      type="submit"
                      className="primary-button"
                      disabled={busy || !trimmedChatId || (Boolean(existingOther) && !replaceExisting)}
                    >
                      {busy ? '正在保存…' : '保存当前群飞书绑定'}
                    </button>
                  </div>
                </fieldset>
              </form>

              <details className="group-feishu-receipts">
                <summary>关联飞书会话的近期回执（{roomReceipts.length}）</summary>
                {roomReceipts.length === 0 ? (
                  <p className="group-feishu-hint">
                    暂无与本群绑定的飞书指令回执。绑定并在线后，从飞书发送任务会在此记录实际收发状态。
                  </p>
                ) : (
                  <ol>
                    {roomReceipts.map((record) => (
                      <li key={record.id}>
                        <header>
                          <strong className={['attention', 'needs_attention', 'rejected'].includes(record.state) ? 'issue' : ''}>
                            {RECEIPT_STATES[record.state] || '状态待核对'}
                          </strong>
                          <time dateTime={record.updated_at || record.created_at}>
                            {receiptTimeLabel(record.updated_at || record.created_at)}
                          </time>
                        </header>
                        {typeof record.reason === 'string' && record.reason && <p>{record.reason}</p>}
                        <div>
                          {typeof record.chat_id === 'string' && record.chat_id && <code>飞书会话：{record.chat_id}</code>}
                          {typeof record.job_id === 'string' && record.job_id && <code>关联任务：{record.job_id}</code>}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </details>
            </>
          ) : busy ? <p className="group-feishu-loading" role="status">正在读取飞书会话绑定…</p> : (
            <button type="button" className="subtle-button" onClick={openAdmin}>重试读取飞书会话绑定</button>
          ))}
        </div>
      </div>
    </dialog>
  );
}
