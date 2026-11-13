/** Pure functions for fleet node status, summaries, and display formatting. */

export const STATUS_LABELS = {
  online: '在线',
  offline: '离线',
  never: '从未连接',
};

export const PLATFORM_LABELS = {
  linux: 'Linux',
  darwin: 'macOS',
  windows: 'Windows',
};

export const OFFLINE_AFTER_SECONDS = 90;

/**
 * Map a node to its derived status. The server already sends `status`,
 * but recompute defensively for offline drift between polls.
 */
export function nodeStatus(node, nowMs = Date.now()) {
  if (!node || !node.last_seen_at) return node?.status || 'never';
  const seen = Date.parse(node.last_seen_at);
  if (Number.isNaN(seen)) return node?.status || 'offline';
  return (nowMs - seen) / 1000 <= OFFLINE_AFTER_SECONDS ? 'online' : 'offline';
}

/**
 * Compute the fleet summary bar: counts + online-only averages.
 */
export function fleetSummary(nodes = [], nowMs = Date.now()) {
  const rows = (nodes || []).map((node) => ({ ...node, status: nodeStatus(node, nowMs) }));
  const online = rows.filter((row) => row.status === 'online');
  const offline = rows.filter((row) => row.status === 'offline');
  const never = rows.filter((row) => row.status === 'never');
  const cpuValues = online.map((row) => row.telemetry?.cpu_percent).filter((value) => typeof value === 'number');
  const memValues = online.map((row) => row.telemetry?.mem_percent).filter((value) => typeof value === 'number');
  const avg = (values) => (values.length ? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10 : null);
  return {
    total: rows.length,
    online: online.length,
    offline: offline.length,
    never: never.length,
    cpu_percent_avg: avg(cpuValues),
    mem_percent_avg: avg(memValues),
    nodes: rows,
  };
}

/**
 * Format a relative time like "8 秒前" / "3 分钟前" / "2 小时前".
 */
export function relativeTime(value, nowMs = Date.now()) {
  if (!value) return '从未连接';
  const stamp = Date.parse(value);
  if (Number.isNaN(stamp)) return '时间无效';
  const seconds = Math.max(0, Math.floor((nowMs - stamp) / 1000));
  if (seconds < 5) return '刚刚';
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

/**
 * Format a telemetry percent for display; null stays as "—".
 */
export function formatPercent(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 10) / 10}%` : '—';
}

/**
 * Format load1 with two decimals; null stays as "—".
 */
export function formatLoad(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—';
}

/**
 * Progress-bar value clamped to 0..100.
 */
export function barPercent(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 0;
  return Math.min(100, Math.round(value));
}

/**
 * Compute star topology positions for node dots around a central hub.
 * Returns [{ x, y }] in a 0..1 coordinate box.
 */
export function topologyPositions(count, centerX = 0.5, centerY = 0.5, radius = 0.38) {
  if (!Number.isFinite(count) || count <= 0) return [];
  const positions = [];
  for (let index = 0; index < count; index++) {
    const angle = (2 * Math.PI * index) / count - Math.PI / 2;
    positions.push({
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    });
  }
  return positions;
}
