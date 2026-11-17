const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

// Where the workspace lives, independent of how the app was launched.
//
// A packaged macOS build runs from
//   <workspace>/releases/ModelDesk.app/Contents/Resources/app.asar/desktop
// so the parent of __dirname is the archive, not the workspace. Inside a bundle
// the workspace is the directory that holds releases/. In a source checkout it
// stays the parent of desktop/.
function defaultWorkspace(start = __dirname) {
  const resolved = path.resolve(start);
  const bundle = resolved.match(/^(.*)\.app[\\/]Contents[\\/]Resources(?:[\\/]|$)/);
  if (bundle) {
    const installedRoot = path.dirname(path.dirname(`${bundle[1]}.app`));
    if (path.isAbsolute(installedRoot)) return installedRoot;
  }
  return path.resolve(resolved, '..');
}

const DEFAULT_WORKSPACE = process.env.MODEL_DESK_WORKSPACE || defaultWorkspace();
const UI_URL = 'http://localhost:5173/';
const HEALTH_URLS = [
  'http://127.0.0.1:8317/v1/models',
  'http://127.0.0.1:8001/api/workbench/status',
  'http://127.0.0.1:5173/',
];
// The local model gateway (8317) is a model source, not a UI precondition.
// On Linux it usually is not installed; requiring it would block a perfectly
// working workbench behind the error page.
const LINUX_REQUIRED_HEALTH_URLS = HEALTH_URLS.filter(url => !url.includes(':8317/'));

function healthURLsFor(platform = process.platform) {
  return platform === 'darwin' ? HEALTH_URLS : LINUX_REQUIRED_HEALTH_URLS;
}

function workspaceRoot(environment = process.env) {
  const candidate = environment.MODEL_DESK_WORKSPACE || DEFAULT_WORKSPACE;
  if (!path.isAbsolute(candidate)) throw new Error('workspace-must-be-absolute');
  return path.resolve(candidate);
}

function serviceEnvironment(environment = process.env) {
  const platformPaths = process.platform === 'darwin'
    ? ['/usr/local/bin', '/opt/homebrew/bin', '/usr/bin', '/bin']
    : ['/usr/local/bin', '/usr/bin', '/bin'];
  return {
    ...environment,
    PATH: [...platformPaths, environment.PATH || ''].join(':'),
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
  if (/^\/api\/workbench\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/documents\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(url.pathname)) {
    return query.length === 0 || query.length === 1 && query[0][0] === 'project_id' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(query[0][1]);
  }
  if (/^\/api\/media\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(?:bundle|assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content)$/i.test(url.pathname)) {
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    return new Set(query.map(([key]) => key)).size === query.length && query.every(([key, value]) =>
      (key === 'project_id' && uuid.test(value)) || (key === 'download' && value === '1' && url.pathname.endsWith('/content')));
  }
  if (/^\/api\/group-tasks\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) {
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    return query.length === 3 && new Set(query.map(([key]) => key)).size === 3 && ['md', 'json'].includes(url.searchParams.get('format')) && uuid.test(url.searchParams.get('project_id') || '') && uuid.test(url.searchParams.get('room_id') || '') && query.every(([key]) => ['format', 'project_id', 'room_id'].includes(key));
  }
  if (/^\/api\/muse\/tasks\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) {
    return query.length >= 1 && query.length <= 2 && new Set(query.map(([key]) => key)).size === query.length && ['md', 'json'].includes(url.searchParams.get('format')) && query.every(([key, value]) => key === 'format' || (key === 'project_id' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)));
  }
  if (/^\/api\/harness\/runs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) {
    return query.length === 1 && query[0][0] === 'format' && ['md', 'json'].includes(query[0][1]);
  }
  if (/^\/api\/workbench\/projects\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content$/i.test(url.pathname)) return query.length === 0;
  if (/^\/api\/workbench\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/attachments\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content$/i.test(url.pathname)) return query.length === 0;
  if (url.pathname === '/api/platform/data/export') {
    return query.length === 0 || (query.length === 1 && query[0][0] === 'include_jobs' && ['true', 'false'].includes(query[0][1]));
  }
  if (!/^\/api\/workbench\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/export$/i.test(url.pathname)) return false;
  if (query.length === 1) return query[0][0] === 'format' && ['md', 'json'].includes(query[0][1]);
  const document = ['docx', 'pdf', 'html'].includes(url.searchParams.get('format'));
  if (query.length !== (document && url.searchParams.has('layout') ? 3 : 2) || new Set(query.map(([key]) => key)).size !== query.length || !['md', 'docx', 'pdf', 'html'].includes(url.searchParams.get('format'))) return false;
  if (url.searchParams.has('layout') && (!document || !['report', 'article', 'compact'].includes(url.searchParams.get('layout')))) return false;
  const artifact = url.searchParams.get('artifact_id');
  return query.every(([key]) => ['format', 'artifact_id', ...(document ? ['layout'] : [])].includes(key)) && typeof artifact === 'string' && artifact.length <= 200 && /^(chat:|council:(?:synthesis$|response:)|camel:|group:|task:|workflow:answer$|failure:|format:|review:findings$|sdk:(?:answer|result)$)/.test(artifact) && !/[\x00-\x1f\x7f]/.test(artifact);
}

function exportFormat(value) {
  if (!isExportURL(value)) return null;
  const url = new URL(value);
  if (url.pathname.endsWith('/content')) return 'attachment';
  if (url.pathname.endsWith('/bundle')) return 'zip';
  if (/\/documents\//.test(url.pathname)) return 'document';
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

module.exports = { DEFAULT_WORKSPACE, UI_URL, HEALTH_URLS, healthURLsFor, defaultWorkspace, workspaceRoot, serviceEnvironment, isWorkbenchURL, isOriginalFrameURL, isExportURL, exportFormat, isExternalURL, isStatusURL, isTrustedWorkbenchIPC };
