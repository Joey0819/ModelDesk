import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { feishuApi } from '../feishu-api';
import {
  feishuBinding,
  feishuConnectionStatus,
  feishuSenderIds,
  roomFeishuSummary,
  safeFeishuError,
} from '../feishu-state';
import { machineInfo } from '../machine';
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

function receiptTimeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function GroupFeishuDialog({
  room,
  projectId,
  status,
  feishuData,
  onUpdated,
  onOpenFeishuSettings,
  onClose,
}) {
  const dialogRef = useRef(null);
  const aliveRef = useRef(true);
  const busyRef = useRef(false);
  const closedRef = useRef(false);
  const onCloseRef = useRef(onClose);
  const onUpdatedRef = useRef(onUpdated);

  const datalistId = useId();
  const chatIdInputId = useId();
  const nameInputId = useId();
  const senderInputId = useId();

  const [localData, setLocalData] = useState(null);
  const [loading, setLoading] = useState(!feishuData && !REMOTE_CLIENT);
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
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    onCloseRef.current = onClose;
    onUpdatedRef.current = onUpdated;
  }, [onClose, onUpdated]);

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

  useEffect(() => {
    if (REMOTE_CLIENT) return undefined;
    let disposed = false;
    feishuApi.rooms().then((result) => {
      if (disposed || !aliveRef.current) return;
      setLocalData(result);
      onUpdatedRef.current?.(result);
      setError('');
    }).catch((failure) => {
      if (disposed || !aliveRef.current) return;
      setError(safeFeishuError(failure));
    }).finally(() => {
      if (!disposed && aliveRef.current) setLoading(false);
    });
    return () => { disposed = true; };
  }, []);

  const effectiveData = localData ?? feishuData;
  const machine = machineInfo(status);
  const pid = projectId || null;
  const roomId = room?.id || '';
  const roomTitle = room?.title || '当前项目群';
  const summary = useMemo(
    () => roomFeishuSummary(effectiveData, pid, roomId),
    [effectiveData, pid, roomId],
  );
  const config = effectiveData?.config && typeof effectiveData.config === 'object' ? effectiveData.config : null;
  const feishuStatus = effectiveData?.status && typeof effectiveData.status === 'object' ? effectiveData.status : null;
  const roomsCatalog = useMemo(
    () => (Array.isArray(effectiveData?.rooms) ? effectiveData.rooms : []),
    [effectiveData],
  );
  const allBindings = useMemo(
    () => (Array.isArray(config?.bindings) ? config.bindings.map(feishuBinding) : []),
    [config],
  );
  const connectionLabel = loading && !effectiveData
    ? '正在读取状态'
    : feishuConnectionStatus(feishuStatus, config);
  const botLink = config?.app_id
    ? `https://applink.feishu.cn/client/bot/open?appId=${encodeURIComponent(config.app_id)}`
    : '';

  const reusableBindings = useMemo(
    () => allBindings.filter((item) => !item.allow_all_groups && item.chat_id && item.room_id),
    [allBindings],
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
    onUpdatedRef.current?.(nextData);
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('modeldesk-feishu-changed'));
    }
    if (message) setNotice(message);
  };

  const runAction = async (action) => {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      if (aliveRef.current) setError(safeFeishuError(failure));
      if (failure?.status === 409) {
        feishuApi.rooms().then((fresh) => {
          if (aliveRef.current) {
            setLocalData(fresh);
            onUpdatedRef.current?.(fresh);
          }
        }).catch(() => {});
      }
    } finally {
      if (aliveRef.current) setBusy(false);
    }
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
    const nextData = await feishuApi.saveRoomBinding(targetBinding.room_id, {
      chat_id: targetBinding.chat_id,
      name: targetBinding.name || '',
      project_id: targetBinding.project_id || null,
      allowed_sender_ids: targetBinding.allowed_sender_ids || [],
      allow_native: targetBinding.allow_native === true,
      natural_language_tasks: targetBinding.natural_language_tasks === true,
      allow_all_groups: true,
      push_group_results: targetBinding.push_group_results === true,
      replace_existing: true,
      enable_connection: !config?.enabled,
    });
    if (!aliveRef.current) return;
    applyServerUpdate(nextData, `已开启全群接入！现在可在飞书发送 /modeldesk use ${roomTitle} 切换到本群。`);
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

  const statusTone = summary.online
    ? 'online'
    : summary.configured
      ? 'idle'
      : 'unconfigured';

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
            <span className={`group-feishu-icon ${summary.connected ? 'connected' : ''}`} aria-hidden="true">
              <Icon name="phone" size={19} />
            </span>
            <div className="group-feishu-heading-text">
              <h2 id="group-feishu-heading">飞书机器人连接</h2>
              <p title={`${roomTitle} · 让当前项目群接入飞书收发任务与成果`}>
                {roomTitle} · 让当前项目群接入飞书收发任务与成果
              </p>
            </div>
          </div>
          <div className="group-feishu-header-right">
            <span className={`group-feishu-pill ${statusTone}`}>
              <span className="group-feishu-pill-dot" aria-hidden="true" />
              {connectionLabel}
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

          {REMOTE_CLIENT ? (
            <section className="group-feishu-callout">
              <h3>请在{machine.short}管理飞书连接</h3>
              <p>
                飞书机器人的 App Secret 与项目群绑定仅保存在{machine.label}。请在{machine.short}的「项目群聊 → 飞书」或「Agent 与连接 → 飞书远程」中完成配置，随后即可随时从手机飞书向本群派发任务并接收成果回传。
              </p>
            </section>
          ) : loading && !effectiveData ? (
            <p className="group-feishu-loading" role="status">
              <span className="small-spinner" aria-hidden="true" />
              正在读取飞书机器人连接状态…
            </p>
          ) : !summary.configured ? (
            <section className="group-feishu-callout">
              <h3>只需配置一次飞书机器人凭据，即可接入全部项目群</h3>
              <p>
                在「Agent 与连接 → 飞书远程」填写一次飞书企业自建应用的 App ID 与 App Secret 后，ModelDesk 中的每一个项目群都可以直接收发飞书任务、同步群聊成果，无需重复创建机器人。
              </p>
              <div className="group-feishu-callout-actions">
                <button type="button" className="primary-button" onClick={openGlobalSettings}>
                  前往配置飞书机器人凭据
                </button>
              </div>
            </section>
          ) : (
            <>
              <div className="group-feishu-bot-bar">
                <div className="group-feishu-bot-summary">
                  <span className={`group-feishu-status-chip ${summary.connected ? 'connected' : summary.bound ? 'bound' : 'unbound'}`}>
                    {summary.statusLabel}
                  </span>
                  <div className="group-feishu-bot-meta">
                    <strong>应用 {config.app_id}</strong>
                    <span>
                      {summary.connected
                        ? '本群已可接收手机飞书任务并回传成果。'
                        : summary.bound
                          ? '本群已绑定飞书会话，启用并保持长连接在线即可收发。'
                          : '机器人凭据已就绪，将飞书会话绑定到此群或开启全群接入即可使用。'}
                    </span>
                  </div>
                </div>
                <div className="group-feishu-bot-actions">
                  {botLink && (
                    <a
                      className="subtle-button group-feishu-bot-link"
                      href={botLink}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开飞书机器人 ↗
                    </a>
                  )}
                  <button type="button" className="subtle-button" onClick={openGlobalSettings}>
                    管理全局凭据
                  </button>
                </div>
              </div>

              <section className="group-feishu-section">
                <h3>当前群接入状态</h3>

                {summary.directBindings.length > 0 && (
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
                )}

                {summary.allGroupsBindings.length > 0 && (
                  <div className="group-feishu-binding-list">
                    {summary.allGroupsBindings.map((binding) => (
                      <article className="group-feishu-binding-card all-groups" key={binding.chat_id}>
                        <div className="group-feishu-binding-top">
                          <div className="group-feishu-binding-info">
                            <strong>{binding.name || '共享飞书会话'}</strong>
                            <code>{binding.chat_id}</code>
                          </div>
                          <span className="group-feishu-badge jade">全群机器人已接入</span>
                        </div>
                        <p className="group-feishu-hint">
                          该会话已开启全群切换，复制下方指令到飞书即可切换到本群或直接派发任务：
                        </p>
                        <div className="group-feishu-cmd-list">
                          <div className="group-feishu-cmd-row">
                            <code>{`/modeldesk use ${roomTitle}`}</code>
                            <CopyTextButton text={`/modeldesk use ${roomTitle}`} label="复制切换指令" />
                          </div>
                          <div className="group-feishu-cmd-row">
                            <code>{`/modeldesk to ${roomTitle} 帮我整理本轮任务`}</code>
                            <CopyTextButton text={`/modeldesk to ${roomTitle} 帮我整理本轮任务`} label="复制派发指令" />
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                )}

                {!summary.bound && reusableBindings.length > 0 && (
                  <div className="group-feishu-shortcut-card">
                    <div className="group-feishu-shortcut-text">
                      <strong>一键复用已有飞书机器人</strong>
                      <p>
                        检测到已有飞书绑定（<code>{reusableBindings[0].name || reusableBindings[0].chat_id}</code>）。点击下方按钮为该会话开启「全部项目群接入」，本群和所有其他 ModelDesk 项目群将立即可用，且保留原有绑定不变。
                      </p>
                    </div>
                    <button
                      type="button"
                      className="primary-button"
                      disabled={busy}
                      onClick={() => enableExistingForAllGroups(reusableBindings[0])}
                    >
                      {busy ? '正在启用…' : '让已有飞书机器人接入全部项目群'}
                    </button>
                  </div>
                )}

                {!summary.bound && reusableBindings.length === 0 && (
                  <p className="group-feishu-hint">
                    当前群尚未接入飞书会话。可在下方读取飞书会话或粘贴 Chat ID 完成绑定。
                  </p>
                )}
              </section>

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
                        <span>允许此会话触发{machine.short}原生 Agent（如 Muse）</span>
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
                <summary>本群相关飞书指令回执（{roomReceipts.length}）</summary>
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
          )}
        </div>
      </div>
    </dialog>
  );
}
