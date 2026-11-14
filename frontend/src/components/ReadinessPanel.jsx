import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, apiFetch, REMOTE_CLIENT } from '../api-base';
import { machineShort } from '../machine';
import { Icon } from './WorkbenchResults';
import './ReadinessPanel.css';

const labels = { ready: '可执行', reachable: '连接可访问', unauthorized: '需要登录', unavailable: '尚未就绪',
  unreachable: '连接失败', unknown: '待确认', disabled: '已停用', needs_attention: '需要处理', degraded: '服务异常' };

export function WorkerStatus({ worker, machineShort: hostShort = machineShort() }) {
  const active = worker?.online === true;
  const ready = active && worker?.state === 'running';
  const attention = active && Object.values(worker.services || {}).some((item) => item.state === 'needs_attention');
  return <div className={`worker-status ${ready ? 'worker-ready' : ''}`} role="status">
    <Icon name="tasks" size={17} /><div><strong>{ready ? '后台服务正在守护任务' : attention ? '后台服务需要处理' : active ? '后台服务正在恢复连接' : '后台守护尚未连接'}</strong>
      <p>{ready ? `可以关闭工作台窗口。${hostShort}需保持唤醒和联网。` : attention ? '部分服务已达到恢复次数上限；请检查服务后重试，已有成果会保留。' : active ? '恢复有次数限制；任务与已生成文件会保留。' : '打开 ModelDesk 桌面应用以启动守护；已有任务状态保存在本机。'}</p></div>
  </div>;
}

export default function ReadinessPanel() {
  const [value, setValue] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const alive = useRef(false), pending = useRef(false);
  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true; setLoading(true);
    try {
      const response = await apiFetch(`${API_BASE}/api/readiness`);
      if (!response.ok) throw new Error('暂时无法检查，请确认后台服务已运行。');
      const result = await response.json();
      if (alive.current) { setValue(result); setError(''); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { pending.current = false; if (alive.current) setLoading(false); }
  }, []);
  useEffect(() => {
    alive.current = true;
    if (!REMOTE_CLIENT) refresh();
    const timer = !REMOTE_CLIENT && setInterval(refresh, 30000);
    return () => { alive.current = false; if (timer) clearInterval(timer); };
  }, [refresh]);
  if (REMOTE_CLIENT) return null;
  return <section className="readiness-panel" aria-label="Agent 当前可用性">
    <header><div><h3>开始前，确认谁可以接手</h3><p>检查连接、执行器和窗口，不发送创作任务。</p></div><button type="button" className="subtle-button" onClick={refresh} disabled={loading}><Icon name="refresh" size={14} />{loading ? '检查中…' : '重新检查'}</button></header>
    {error && <p role="alert">{error} {value && '下方保留的是上次检查结果。'}</p>}
    {value && <><WorkerStatus worker={error ? null : value.worker} /><dl>{value.agents.map((item) => <div key={item.id}><dt>{item.label}</dt><dd><strong className={`readiness-${error ? 'unknown' : item.state}`}>{error ? '状态待刷新' : labels[item.state] || '待确认'}</strong><span>{item.reason}</span></dd></div>)}</dl><small>检查时间：{new Date(value.checked_at).toLocaleString()} · 每 30 秒更新，实际生成仍受模型权限与额度影响。</small></>}
  </section>;
}
