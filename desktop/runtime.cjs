const path = require('node:path');
const { pathToFileURL } = require('node:url');

const DEFAULT_WORKSPACE = '/Users/chengneng/Project/Skills/llm-tools';
const UI_URL = 'http://localhost:5173/';
const HEALTH_URLS = [
  'http://127.0.0.1:8317/v1/models',
  'http://127.0.0.1:8001/api/workbench/status',
  'http://127.0.0.1:5173/',
];

function workspaceRoot(environment = process.env) {
  const candidate = environment.MODEL_DESK_WORKSPACE || DEFAULT_WORKSPACE;
  if (!path.isAbsolute(candidate)) throw new Error('workspace-must-be-absolute');
  return path.resolve(candidate);
}

function serviceEnvironment(environment = process.env) {
  return {
    ...environment,
    PATH: ['/usr/local/bin', '/opt/homebrew/bin', '/usr/bin', '/bin', environment.PATH || ''].join(':'),
  };
}

function parseURL(value) {
  try {
    const url = new URL(value);
    return url.username || url.password ? null : url;
  } catch {
    return null;
  }
}

function isWorkbenchURL(value) {
  const url = parseURL(value);
  return Boolean(url && url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '5173');
}

function isOriginalFrameURL(value) {
  const url = parseURL(value);
  return Boolean(url && url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '8501');
}

function isExportURL(value) {
  if (typeof value !== 'string') return false;
  const url = parseURL(value);
  if (!url || url.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(url.hostname) || url.port !== '8001' || url.hash) return false;
  const rawPath = value.match(/^[a-z][a-z0-9+.-]*:\/\/[^/?#]+([^?#]*)/i)?.[1];
  if (rawPath !== url.pathname) return false;
  const query = Array.from(url.searchParams.entries());
  if (url.pathname === '/api/platform/data/export') {
    return query.length === 0 || (query.length === 1 && query[0][0] === 'include_jobs' && ['true', 'false'].includes(query[0][1]));
  }
  if (!/^\/api\/workbench\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) return false;
  if (query.length === 1) return query[0][0] === 'format' && ['md', 'json'].includes(query[0][1]);
  if (query.length !== 2 || new Set(query.map(([key]) => key)).size !== 2 || url.searchParams.get('format') !== 'md') return false;
  const artifact = url.searchParams.get('artifact_id');
  return query.every(([key]) => ['format', 'artifact_id'].includes(key)) && typeof artifact === 'string' && artifact.length <= 200 && /^(chat:|council:(?:synthesis$|response:)|camel:|group:|task:|workflow:answer$|failure:|format:|review:findings$|sdk:(?:answer|result)$)/.test(artifact) && !/[\x00-\x1f\x7f]/.test(artifact);
}

function exportFormat(value) {
  if (!isExportURL(value)) return null;
  const url = new URL(value);
  return url.pathname === '/api/platform/data/export' ? 'json' : url.searchParams.get('format');
}

function isExternalURL(value) {
  const url = parseURL(value);
  return Boolean(url && ['http:', 'https:'].includes(url.protocol) && !isWorkbenchURL(value) && !isExportURL(value));
}

function isStatusURL(value, statusPath) {
  const url = parseURL(value);
  return Boolean(url && url.protocol === 'file:' && url.pathname === pathToFileURL(statusPath).pathname && !url.host);
}

function isTrustedWorkbenchIPC(event, contents) {
  try {
    const frame = event?.senderFrame;
    const mainFrame = contents?.mainFrame;
    return Boolean(contents && !contents.isDestroyed() && event.sender === contents &&
      frame && mainFrame && Number.isInteger(frame.processId) && Number.isInteger(frame.routingId) &&
      frame.processId === mainFrame.processId && frame.routingId === mainFrame.routingId &&
      isWorkbenchURL(frame.url) && isWorkbenchURL(contents.getURL()));
  } catch {
    return false;
  }
}

module.exports = { DEFAULT_WORKSPACE, UI_URL, HEALTH_URLS, workspaceRoot, serviceEnvironment, isWorkbenchURL, isOriginalFrameURL, isExportURL, exportFormat, isExternalURL, isStatusURL, isTrustedWorkbenchIPC };
