/** Keep phone requests on the paired gateway; never call the phone's localhost. */
export function isRemoteLocation(location = globalThis.location) {
  if (!location || !/^https?:$/.test(location.protocol || '')) return false;
  const local = ['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname);
  return !local || !['5173', '8001', '3000'].includes(location.port);
}
export function resolveApiBase(location = globalThis.location, configured = '') {
  if (isRemoteLocation(location)) return location.origin;
  if (configured) return configured;
  // Follow the page's own host. A 127.0.0.1 page calling localhost:8001 is
  // cross-site to the browser, so the admin guard rejects it with 403.
  const host = location?.hostname || 'localhost';
  return `http://${host}:8001`;
}
export const REMOTE_CLIENT = isRemoteLocation();
export const API_BASE = resolveApiBase(globalThis.location, import.meta.env?.VITE_API_BASE);
export async function apiFetch(url, options) {
  const response = await fetch(url, options);
  if (REMOTE_CLIENT && response.status === 401) globalThis.dispatchEvent?.(new Event('modeldesk-session-expired'));
  return response;
}
