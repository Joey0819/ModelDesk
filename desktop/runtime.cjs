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

function isExportURL(value) {
  if (typeof value !== 'string') return false;
  const url = parseURL(value);
  if (!url || url.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(url.hostname) || url.port !== '8001' || url.hash) return false;
  const rawPath = value.match(/^[a-z][a-z0-9+.-]*:\/\/[^/?#]+([^?#]*)/i)?.[1];
  if (rawPath !== url.pathname) return false;
  if (!/^\/api\/workbench\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) return false;
  const query = Array.from(url.searchParams.entries());
  return query.length === 1 && query[0][0] === 'format' && ['md', 'json'].includes(query[0][1]);
}

function isExternalURL(value) {
  const url = parseURL(value);
  return Boolean(url && ['http:', 'https:'].includes(url.protocol) && !isWorkbenchURL(value) && !isExportURL(value));
}

function isStatusURL(value, statusPath) {
  const url = parseURL(value);
  return Boolean(url && url.protocol === 'file:' && url.pathname === pathToFileURL(statusPath).pathname && !url.host);
}

module.exports = { DEFAULT_WORKSPACE, UI_URL, HEALTH_URLS, workspaceRoot, serviceEnvironment, isWorkbenchURL, isExportURL, isExternalURL, isStatusURL };
