import { useCallback, useEffect, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { remoteApi } from '../remote-api';
import './RemoteAccess.css';

function PairedWorkspace({ children }) {
  const [session, setSession] = useState(null);
  const [code, setCode] = useState('');
  const [label, setLabel] = useState('我的手机');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const check = useCallback(async () => {
    try { const result = await remoteApi.session(); if (alive.current) { setSession(result); setError(''); } }
    catch (failure) { if (alive.current) { setSession({ authenticated: false }); setError(failure.message); } }
  }, []);
  useEffect(() => {
    alive.current = true; check();
    const expire = () => { setSession({ authenticated: false }); setCode(''); setError('连接凭证已过期或已被 Mac 撤销，请重新配对。未发送的草稿仍保留在当前设备。'); };
    window.addEventListener('modeldesk-session-expired', expire);
    const timer = setInterval(check, 60000);
    return () => { alive.current = false; window.removeEventListener('modeldesk-session-expired', expire); clearInterval(timer); };
  }, [check]);
  const pair = async (event) => { event.preventDefault(); if (!code.trim() || busy) return; setBusy(true); setError(''); try { await remoteApi.pair(code.trim(), label.trim() || '我的手机'); setCode(''); await check(); } catch (failure) { setError(failure.message); } finally { setBusy(false); } };
  if (session?.authenticated) return children;
  return <main className="remote-pairing-page"><section><div className="remote-brand"><span className="brand-symbol"><i /><i /><i /><i /></span><strong>ModelDesk</strong></div><h1>把项目工作台带在身边</h1><p>在 Mac 的 ModelDesk 中打开「手机连接」，生成一次性配对码，再在这里输入。</p>{!session ? <p role="status">正在检查连接…</p> : <form onSubmit={pair}><label>设备名称<input aria-label="手机设备名称" maxLength={60} value={label} onChange={(event) => setLabel(event.target.value)} autoComplete="off" /></label><label>一次性配对码<input autoFocus aria-label="一次性配对码" className="remote-code-input" autoComplete="one-time-code" autoCapitalize="characters" autoCorrect="off" spellCheck={false} maxLength={32} placeholder="输入 Mac 上显示的配对码" value={code} onChange={(event) => setCode(event.target.value)} /></label>{error && <p role="alert" className="remote-error">{error}</p>}<button type="submit" disabled={busy || !code.trim()}>{busy ? '正在配对' : '连接我的工作台'}</button></form>}<p className="remote-pairing-note">配对后可以查看项目、发起协作、检查改动并审批。请保持 Mac 在线；原生窗口操作留在 Mac 完成。</p><button className="remote-retry" type="button" onClick={check}>重新检查连接</button></section></main>;
}
export default function RemoteAccessGate({ children }) { return REMOTE_CLIENT ? <PairedWorkspace>{children}</PairedWorkspace> : children; }
