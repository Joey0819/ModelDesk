/** Remote sessions use UNIX seconds, while worker timestamps use ISO strings. */
export function epochMillis(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value * 1000 : NaN;
  return value ? new Date(value).getTime() : NaN;
}
export function pairingSeconds(expiresAt, now = Date.now()) { const value = epochMillis(expiresAt); return Number.isFinite(value) ? Math.max(0, Math.ceil((value - now) / 1000)) : 0; }
export function remoteDate(value) { const timestamp = epochMillis(value); return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'; }
