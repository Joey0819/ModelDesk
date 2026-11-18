import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer as createViteServer } from 'vite';

const frontend = fileURLToPath(new URL('../../', import.meta.url));
const models = [
  { id: 'gpt-6.1-sol', label: 'Fixture GPT', provider: 'fixture' },
  { id: 'gemini-3-flash', label: 'Fixture Gemini', provider: 'fixture' },
];
const project = { id: 'fixture-project', name: '导航测试项目', managed: true, path: '/fixture', folders: [], files: [] };
const reads = {
  '/remote/session': { authenticated: true },
  '/remote/info': { platform: { system: 'darwin' } },
  '/api/workbench/status': { gateway: { online: true }, models, providers: [], tools: {} },
  '/api/workbench/projects': [project],
  '/api/workbench/projects/fixture-project': project,
  '/api/platform/overview': { counts: {} },
  '/api/platform/tools': { tools: [], mcp: [] },
  '/api/platform/originals': { apps: [] },
  '/api/skills': { installed: [], available: [] },
};

export async function startNavigationFixture(root = frontend, handleRead) {
  const cacheDir = await mkdtemp(join(tmpdir(), 'modeldesk-vite-'));
  const removeCache = () => rm(cacheDir, { recursive: true, force: true });
  const vite = await createViteServer({
    root,
    cacheDir,
    envFile: false,
    server: { middlewareMode: true, hmr: false, ws: false },
    plugins: [{
      name: 'modeldesk-navigation-fixture',
      transformIndexHtml() { return [{ tag: 'meta', attrs: { name: 'modeldesk-navigation-fixture' } }]; },
    }],
  }).catch(async (error) => { await removeCache(); throw error; });
  const writes = [];
  const server = createServer((request, response) => {
    // Keep the frontend and every API request on the fixture origin.
    response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'");
    const url = new URL(request.url, 'http://127.0.0.1');
    const { pathname } = url;
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      writes.push(`${request.method} ${pathname}`);
      response.writeHead(405).end('Fixture is read-only');
      return;
    }
    if (handleRead?.(url, response)) return;
    if (pathname.startsWith('/api/') || pathname.startsWith('/remote/')) {
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(reads[pathname] ?? []));
      return;
    }
    vite.middlewares(request, response);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    writes,
    async close() {
      try { await vite.close(); }
      finally {
        await new Promise((resolve) => server.close(resolve));
        await removeCache();
      }
    },
  };
}
