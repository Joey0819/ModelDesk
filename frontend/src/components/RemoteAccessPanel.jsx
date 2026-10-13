import { useCallback, useEffect, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { remoteApi } from '../remote-api';
import { pairingSeconds, remoteDate as formatDate } from '../remote-state';
import { Icon } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import './RemoteAccess.css';

export default function RemoteAccessPanel({ onClose }) {
  const dialog = useRef(null);
  const alive = useRef(true);
  const [status, setStatus] = useState(null);
  const [pairing, setPairing] = useState(null);
  const [label, setLabel] = useState('我的手机');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const refresh = useCallback(async () => { try { const next = await (REMOTE_CLIENT ? remoteApi.session() : remoteApi.status()); if (alive.current) { setStatus(next); setError(''); } } catch (failure) { if (alive.current) setError(failure.message); } }, []);
  useEffect(() => { alive.current = true; dialog.current?.showModal(); refresh(); const timer = setInterval(() => { setNow(Date.now()); }, 1000); return () => { alive.current = false; clearInterval(timer); }; }, [refresh]);
  const mutate = async (action) => { setBusy(true); setError(''); try { await action(); await refresh(); } catch (failure) { if (alive.current) setError(failure.message); } finally { if (alive.current) setBusy(false); } };
  const expires = pairing ? pairingSeconds(pairing.expires_at, now) : 0;
  const configuredLocally = Boolean(status?.configured && status?.gateway_running && status?.origin);
  return <dialog className="remote-access-dialog" ref={dialog} onCancel={onClose} onClick={(event) => { if (event.target === dialog.current) onClose(); }}><header><div><Icon name="phone" size={21} /><strong>{REMOTE_CLIENT ? '当前远程连接' : '手机连接'}</strong></div><button className="icon-button" type="button" aria-label="关闭手机连接" onClick={onClose}><Icon name="close" size={19} /></button></header><div className="remote-access-body">
    {error && <p className="output-error" role="alert">{error}</p>}
    {REMOTE_CLIENT ? <><h2>当前浏览器已连接 Mac 工作台</h2><p>项目与任务运行在 Mac，当前设备用于操作和审批。有效期至 {formatDate(status?.expires_at)}。</p><p>新建配对与设备撤销需要回到 Mac；Muse 原生窗口操作也需在 Mac 上完成。</p><button className="subtle-button" type="button" disabled={busy} onClick={() => mutate(async () => { await remoteApi.logout(); window.dispatchEvent(new Event('modeldesk-session-expired')); })}>断开此设备</button></> : <>
      <h2>在手机上继续项目协作</h2><p>Mac 和手机登录同一个 Tailscale 网络，通过加密连接访问。每台设备需单独配对。</p>
      <div className={`remote-connection-state ${configuredLocally ? 'configured' : ''}`}><span className={`status-dot ${configuredLocally ? 'online' : 'offline'}`} /><strong>{configuredLocally ? 'Mac 入口已配置' : status ? 'Mac 入口尚未启用' : '正在检查 Mac 配置'}</strong><button className="subtle-button" type="button" disabled={busy} onClick={refresh}>刷新配置</button></div>
      {configuredLocally && <p className="remote-reachability-note">这表示 Mac 网关正在运行。手机能否访问仍需在手机浏览器打开下方地址确认；本机配置检查不能验证手机的网络与 DNS。</p>}
      {status && <div className="remote-enable-actions">{status.enabled ? <button type="button" className="subtle-button" disabled={busy} onClick={() => mutate(async () => { await remoteApi.stop(); setPairing(null); })}>关闭手机访问并撤销设备</button> : <button type="button" className="primary-button" disabled={busy || !status.tailscale?.installed || status.tailscale?.state !== 'Running'} onClick={() => mutate(() => remoteApi.start())}>{busy ? '正在启用' : '启用手机访问'}</button>}</div>}
      {!configuredLocally && status && <div className="remote-setup-help"><p>{!status.tailscale?.installed ? '请先在 Mac 和手机安装 Tailscale，并登录同一账号。' : status.tailscale?.state !== 'Running' ? '请先在 Mac 打开 Tailscale 并连接，同时让手机连接同一网络。' : 'Tailscale 已连接，请点击“启用手机访问”配置本机入口。'}</p><p>入口启用后，这里会显示访问地址。先用手机打开配对页，再生成一次性配对码。</p></div>}
      {(pairing?.origin || status?.origin) && <section className="remote-phone-address"><h3>手机访问地址</h3><code>{pairing?.origin || status.origin}</code><CopyTextButton text={pairing?.origin || status.origin} label="复制手机访问地址" /><p>复制完整 HTTPS 地址，在手机浏览器打开，保留网址末尾的端口。先看到配对页，再生成下方配对码；地址不包含访问凭证。</p></section>}
      <details className="remote-phone-help"><summary>手机打不开？按这几步检查</summary><ol><li>保持 Mac 在线，ModelDesk 和 Tailscale 正在运行。</li><li>在手机打开 Tailscale，确认显示“已连接”，并与 Mac 登录同一账号或网络。</li><li>重新复制上方完整 HTTPS 地址到手机浏览器；不要替换为 localhost 或 127.0.0.1。</li><li>手机若同时使用其他 VPN、代理或自定义 DNS，检查它们是否与 Tailscale 的路由、域名解析冲突，再重新确认 Tailscale 连接状态。</li></ol><p>只有手机实际打开配对页并完成配对，才能确认那台手机的访问路径。下方历史配对记录不代表它目前仍然在线。</p></details>
      <section className="remote-pairing-controls"><h3>配对新设备</h3><label>设备名称<input aria-label="新配对设备名称" maxLength={60} value={label} onChange={(event) => setLabel(event.target.value)} /></label><button type="button" className="primary-button" disabled={!configuredLocally || busy} onClick={() => mutate(async () => { const next = await remoteApi.createPairing(label.trim() || '我的手机'); if (alive.current) setPairing(next); })}>{busy ? '正在处理' : pairing ? '生成新的配对码' : '生成一次性配对码'}</button>{pairing && <div className="remote-generated-code" aria-live="polite"><strong>{expires ? pairing.code : '配对码已过期'}</strong><span>{expires ? `${Math.floor(expires / 60)}:${String(expires % 60).padStart(2, '0')} 内有效 · 只能使用一次` : '请生成新的配对码'}</span>{expires > 0 && <CopyTextButton text={pairing.code} label="复制配对码" />}</div>}</section>
      <section className="remote-session-list"><h3>已配对设备记录 <span>{status?.sessions?.length || 0}</span></h3>{status?.sessions?.length ? status.sessions.map((session) => <article key={session.id}><div><strong>{session.label || '未命名设备'}</strong><small>最近使用 {formatDate(session.last_used)} · 到期 {formatDate(session.expires_at)}</small></div><button type="button" className="subtle-button" disabled={busy} onClick={() => mutate(() => remoteApi.revoke(session.id))}>撤销连接</button></article>) : <p>还没有已配对设备。</p>}</section>
    </>}
  </div></dialog>;
}
