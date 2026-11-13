import { useCallback, useEffect, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { fleetApi } from '../fleet-api';
import {
  STATUS_LABELS, PLATFORM_LABELS, fleetSummary, nodeStatus, relativeTime,
  formatPercent, formatLoad, barPercent, topologyPositions,
} from '../fleet-state';
import { Icon } from './WorkbenchResults';
import './FleetNodes.css';

const POLL_MS = 10000;

function platformGlyph(platform) {
  if (platform === 'darwin') return 'mac';
  if (platform === 'windows') return 'win';
  return 'lnx';
}

function NodeCard({ node, nowMs, onRemove }) {
  const status = nodeStatus(node, nowMs);
  const telemetry = node.telemetry || {};
  return <article className={`fleet-node-card status-${status}`}>
    <div className="fleet-node-identity">
      <span className={`fleet-node-platform platform-${node.platform}`} title={PLATFORM_LABELS[node.platform] || node.platform}>{platformGlyph(node.platform)}</span>
      <div>
        <h3>{node.name || node.node_id}</h3>
        <p>{node.hostname}</p>
      </div>
      <span className={`fleet-node-badge ${status}`}>{STATUS_LABELS[status] || status}</span>
    </div>
    {node.labels?.length > 0 && <div className="fleet-node-labels">{node.labels.map((label) => <span key={label}>{label}</span>)}</div>}
    <div className="fleet-node-meters">
      <div className="fleet-meter"><span>CPU {formatPercent(telemetry.cpu_percent)}</span><progress max="100" value={barPercent(telemetry.cpu_percent)} /></div>
      <div className="fleet-meter"><span>内存 {formatPercent(telemetry.mem_percent)}</span><progress max="100" value={barPercent(telemetry.mem_percent)} /></div>
      <p className="fleet-node-detail">load1 {formatLoad(telemetry.load1)} · 磁盘 {formatPercent(telemetry.disk_percent)} · 最后心跳 {relativeTime(node.last_seen_at, nowMs)}</p>
    </div>
    <div className="fleet-node-actions">
      <button type="button" className="subtle-button fleet-node-remove" onClick={() => onRemove(node)}>移除节点</button>
    </div>
  </article>;
}

function FleetTopology({ nodes, nowMs }) {
  const positions = topologyPositions(nodes.length);
  const width = 560, height = 360, hubX = width / 2, hubY = height / 2;
  return <svg className="fleet-topology" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="节点星型拓扑图">
    {nodes.map((node, index) => {
      const position = positions[index];
      const x = position.x * width, y = position.y * height;
      const status = nodeStatus(node, nowMs);
      return <line key={`line-${node.node_id}`} x1={hubX} y1={hubY} x2={x} y2={y}
        className={`fleet-topology-link ${status}`} />;
    })}
    <circle className="fleet-topology-hub" cx={hubX} cy={hubY} r="34" />
    <text className="fleet-topology-hub-text" x={hubX} y={hubY - 2} textAnchor="middle">ModelDesk</text>
    <text className="fleet-topology-hub-sub" x={hubX} y={hubY + 14} textAnchor="middle">中枢</text>
    {nodes.map((node, index) => {
      const position = positions[index];
      const x = position.x * width, y = position.y * height;
      const status = nodeStatus(node, nowMs);
      return <g key={`dot-${node.node_id}`} className="fleet-topology-node">
        <circle cx={x} cy={y} r="9" className={`fleet-topology-dot ${status}`} />
        <text x={x} y={y - 16} textAnchor="middle">{node.name || node.node_id}</text>
      </g>;
    })}
  </svg>;
}

export default function FleetNodes() {
  const [nodes, setNodes] = useState([]), [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(!REMOTE_CLIENT), [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [enrollToken, setEnrollToken] = useState(null), [removeTarget, setRemoveTarget] = useState(null);
  const [busy, setBusy] = useState(false), [nowMs, setNowMs] = useState(Date.now());
  const alive = useRef(true);

  const reload = useCallback(async () => {
    try {
      const data = await fleetApi.list();
      if (!alive.current) return;
      setNodes(data.nodes || []);
      setSummary(data.summary || null);
      setError('');
    } catch (failure) {
      if (alive.current) setError(failure.message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    if (!REMOTE_CLIENT) reload();
    const poll = setInterval(() => { reload(); setNowMs(Date.now()); }, POLL_MS);
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    return () => { alive.current = false; clearInterval(poll); clearInterval(tick); };
  }, [reload]);

  const perform = async (action) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setBusy(false); }
  };

  const generateToken = () => perform(async () => {
    const result = await fleetApi.createEnrollToken();
    if (!alive.current) return;
    setEnrollToken(result.token || '');
    setNotice('');
  });

  const removeNode = (node) => perform(async () => {
    await fleetApi.removeNode(node.node_id);
    if (!alive.current) return;
    setRemoveTarget(null);
    await reload();
    setNotice(`已移除节点 ${node.name || node.node_id}。`);
  });

  if (REMOTE_CLIENT) return <div className="agent-remote-note"><Icon name="settings" size={25} /><h2>节点与算力在本机管理</h2><p>请在本机的“Agent 与连接 → 节点与算力”生成注册令牌并管理算力节点。</p></div>;

  const derived = summary || fleetSummary(nodes, nowMs);
  const displayed = derived.nodes || nodes;

  return <>
    <div className="agent-section-heading">
      <div>
        <h2>节点与算力</h2>
        <p>把服务器和电脑接入 ModelDesk，实时查看各节点的状态与负载。本阶段只做注册、心跳和遥测展示，不支持远程执行命令。</p>
      </div>
      <div className="agent-inline-actions">
        <button type="button" className="subtle-button" disabled={busy} onClick={reload}><Icon name="refresh" size={15} />刷新</button>
        <button type="button" className="primary-button" disabled={busy} onClick={generateToken}><Icon name="plus" size={15} />生成注册令牌</button>
      </div>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <p className="agent-notice" role="status">{notice}</p>}
    <div className="fleet-summary">
      <span><strong>{derived.online}</strong>/<strong>{derived.total}</strong> 在线</span>
      <span>CPU 均值 {formatPercent(derived.cpu_percent_avg)}</span>
      <span>内存均值 {formatPercent(derived.mem_percent_avg)}</span>
      <span>离线 {derived.offline}</span>
      <span>从未连接 {derived.never}</span>
    </div>
    {loading ? <p className="connection-help">正在读取节点列表…</p> : displayed.length === 0 ? <div className="connection-empty fleet-empty">
      <h3>还没有接入任何算力节点</h3>
      <p>点击“生成注册令牌”，在目标机器上运行 ModelDesk 的节点 agent 完成注册。</p>
    </div> : <div className="fleet-node-grid">{displayed.map((node) => <NodeCard key={node.node_id} node={node} nowMs={nowMs} onRemove={setRemoveTarget} />)}</div>}
    {displayed.length > 0 && <section className="fleet-topology-box">
      <h3>星型拓扑</h3>
      <FleetTopology nodes={displayed} nowMs={nowMs} />
      <p className="connection-help">绿色实线表示在线，灰色实线表示离线，虚线表示从未连接。超过 90 秒没有心跳即判为离线。</p>
    </section>}
    {enrollToken !== null && <section className="connection-action-box fleet-token-box">
      <div className="agent-section-heading">
        <h3>一次性注册令牌</h3>
        <button className="icon-button" type="button" aria-label="关闭令牌对话框" onClick={() => setEnrollToken(null)}><Icon name="close" size={17} /></button>
      </div>
      <p>此令牌<b>只显示一次</b>，关闭后无法再次查看；服务器只保存它的哈希。在目标机器上运行：</p>
      <pre className="fleet-token-command">python scripts/fleet_agent.py --server http://127.0.0.1:8001 --node-id my-node --name "我的节点" --token &lt;粘贴注册令牌&gt;</pre>
      <div className="fleet-token-value"><code>{enrollToken || '生成失败，请重试'}</code></div>
      <div className="agent-inline-actions">
        <button type="button" className="subtle-button" onClick={() => { navigator.clipboard?.writeText(enrollToken).catch(() => {}); setNotice('注册令牌已复制，请尽快使用；令牌单次有效。'); }}>复制令牌</button>
        <button type="button" className="subtle-button" onClick={() => setEnrollToken(null)}>我已保存，关闭</button>
      </div>
    </section>}
    {removeTarget && <section className="connection-action-box">
      <h3>移除节点 {removeTarget.name || removeTarget.node_id}？</h3>
      <p>移除后该节点的注册与心跳凭据全部失效，需重新生成令牌注册才能再次接入。</p>
      <div className="agent-inline-actions">
        <button type="button" className="subtle-button" disabled={busy} onClick={() => setRemoveTarget(null)}>取消</button>
        <button type="button" className="primary-button" disabled={busy} onClick={() => removeNode(removeTarget)}>确认移除节点</button>
      </div>
    </section>}
  </>;
}
