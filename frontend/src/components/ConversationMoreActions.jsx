import { useId, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { Icon } from './WorkbenchResults';
import WorkspaceDialog from './WorkspaceDialog';
import './ConversationMoreActions.css';

function MoreActionsDialog({ job, status, refreshing, canSavePrompt, onClose, onConnections, onRefresh, onSavePrompt }) {
  const [accountOpen, setAccountOpen] = useState(false);
  const closeRef = useRef(null);
  const id = useId();
  const providers = status?.providers || [];
  const hasAccount = providers.some((provider) => provider.authenticated);
  return <WorkspaceDialog className="conversation-more-dialog" aria-labelledby={`${id}-title`} onDismiss={onClose} focusRef={closeRef} dismissOutside>
    <header>
      <h2 id={`${id}-title`}>更多操作</h2>
      <button ref={closeRef} type="button" className="icon-button" aria-label="关闭更多操作" onClick={onClose}><Icon name="close" size={18} /></button>
    </header>
    <div className="conversation-more-body">
      <section>
        <button type="button" className="subtle-button conversation-account-toggle" aria-expanded={accountOpen} aria-controls={accountOpen ? `${id}-accounts` : undefined} onClick={() => setAccountOpen(!accountOpen)}>
          <span className={`status-dot ${hasAccount ? 'online' : 'offline'}`} />已有账户<Icon name="chevron" size={14} />
        </button>
        {accountOpen && <div className="conversation-accounts" id={`${id}-accounts`}>
          {providers.length ? <ul>
            {providers.map((provider) => <li key={provider.id}>
              <strong>{provider.label}</strong>
              <span>{provider.authenticated ? '已发现登录凭据' : '未接入'} · {provider.verified_models?.length ? `${provider.verified_models.length} 个模型已验证` : '尚无已验证调用'}</span>
              {provider.reason && <p>{provider.reason}</p>}
            </li>)}
          </ul> : <p>{status?.gateway?.error || (status ? '当前没有账户连接记录。' : '正在读取本地登录状态…')}</p>}
          <div className="conversation-more-buttons">
            <button type="button" className="subtle-button" onClick={onRefresh} disabled={refreshing}><Icon name="refresh" size={15} />{refreshing ? '正在刷新' : '刷新连接状态'}</button>
            <button type="button" className="subtle-button" onClick={() => { onClose(); onConnections(); }}><Icon name="plus" size={15} />管理模型与 API</button>
          </div>
        </div>}
      </section>
      <section>
        <button type="button" className="subtle-button" disabled={!canSavePrompt} aria-describedby={canSavePrompt ? undefined : `${id}-save`} onClick={() => { onClose(); onSavePrompt(); }}>保存提示</button>
        {!canSavePrompt && <p id={`${id}-save`}>输入提示后即可保存到提示词库。</p>}
      </section>
      <section aria-labelledby={`${id}-export`}>
        <h3 id={`${id}-export`}>导出当前轮</h3>
        {job ? <div className="conversation-more-buttons">
          <a className="subtle-button" href={workbenchApi.exportUrl(job.id, 'md')} download={`ModelDesk-${job.mode}-${job.id.slice(0, 8)}.md`}><Icon name="export" size={15} />导出 Markdown</a>
          <a className="subtle-button" href={workbenchApi.exportUrl(job.id, 'json')} download={`ModelDesk-${job.mode}-${job.id.slice(0, 8)}.json`}><Icon name="export" size={15} />导出完整 JSON</a>
        </div> : <p>当前会话有运行记录后即可导出 Markdown 或 JSON。</p>}
      </section>
    </div>
  </WorkspaceDialog>;
}

export default function ConversationMoreActions(props) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="subtle-button conversation-more-trigger" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      更多操作<Icon name="chevron" size={14} />
    </button>
    {open && <MoreActionsDialog {...props} onClose={() => setOpen(false)} />}
  </>;
}
