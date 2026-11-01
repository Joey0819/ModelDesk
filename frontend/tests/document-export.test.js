import test from 'node:test';
import assert from 'node:assert/strict';
import { canExportDocument, documentExportUrl, documentFilename, preparedDocuments, requestDocumentExport, verifiedDocumentUrl } from '../src/document-export.js';

const job = { id: 'current-job', mode: 'group', project_id: 'current-project' };
const artifact = { id: 'group:delivery', title: '测试成果 / Report', content: '# Complete output', status: 'complete' };
const entry = { id: '588cfad2-022d-41c8-8af8-93fa90c5b064', format: 'docx', layout: 'report', filename: 'report.docx', sha256: 'a'.repeat(64), content_sha256: 'b'.repeat(64), size: 1000, status: 'ready', url: '/api/workbench/jobs/current-job/documents/588cfad2-022d-41c8-8af8-93fa90c5b064?project_id=current-project' };

test('document export preserves selected historical artifact ownership and paired gateway origin', () => {
  const historical = { ...artifact, id: 'historical:group:delivery', raw_id: 'group:delivery', origin_job: { ...job, id: 'older-job' } };
  const url = new URL(documentExportUrl(job, historical, 'pdf', 'article', 'https://my-mac.example:8443'));
  assert.equal(url.origin, 'https://my-mac.example:8443');
  assert.equal(url.pathname, '/api/workbench/jobs/older-job/export');
  assert.equal(url.searchParams.get('artifact_id'), 'group:delivery');
  assert.equal(url.searchParams.get('format'), 'pdf');
  assert.equal(url.searchParams.get('layout'), 'article');
  assert.equal(documentFilename(job, historical, 'pdf'), 'ModelDesk-older-jo-测试成果-Report.pdf');
});

test('unfinished streams and unsupported native endpoints cannot offer document downloads; partial content can', () => {
  for (const invalid of [{ ...artifact, status: 'live' }, { ...artifact, content: '  ' }]) {
    assert.equal(canExportDocument(job, invalid), false);
    assert.throws(() => documentExportUrl(job, invalid, 'docx', 'report'), /实际成果/);
  }
  for (const mode of ['muse_task', 'group_agent']) assert.equal(canExportDocument({ ...job, mode }, artifact), false);
  assert.equal(canExportDocument(job, { ...artifact, status: 'partial' }), true);
  assert.throws(() => documentExportUrl(job, artifact, 'exe', 'report'), /支持/);
  assert.throws(() => documentExportUrl(job, artifact, 'pdf', 'custom'), /支持/);
});

test('prepared files reject cross-project, other-job, non-ready, unverified and injected URLs', () => {
  const prepared = { ...job, result: { delivery: { documents: [entry] } } };
  assert.equal(preparedDocuments(prepared, artifact, 'https://paired.test')[0].url, `https://paired.test${entry.url}`);
  assert.deepEqual(preparedDocuments(prepared, { ...artifact, id: 'group:1' }), []);
  for (const override of [
    { url: 'https://evil.test/api/workbench/jobs/current-job/documents/588cfad2-022d-41c8-8af8-93fa90c5b064' },
    { url: '/api/workbench/jobs/other-job/documents/588cfad2-022d-41c8-8af8-93fa90c5b064' },
    { url: '/api/workbench/jobs/current-job/documents/588cfad2-022d-41c8-8af8-93fa90c5b064?project_id=other-project' },
    { url: `${entry.url}&project_id=current-project` },
    { url: `${entry.url}&download=1` },
    { url: `${entry.url}#unsafe` },
    { url: entry.url.split('?')[0] },
    { status: 'pending' }, { sha256: '' }, { content_sha256: '' }, { size: 0 }, { format: 'exe' }, { id: '../588cfad2-022d-41c8-8af8-93fa90c5b064' },
  ]) {
    const invalid = { ...job, result: { delivery: { documents: [{ ...entry, ...override }] } } };
    assert.deepEqual(preparedDocuments(invalid, artifact, 'https://paired.test'), [], JSON.stringify(override));
  }
  assert.deepEqual(preparedDocuments({ ...job, result: { delivery: { documents: 'invalid' } } }, artifact), []);
});

test('download exposes server failures and refuses empty or wrong-type responses', async () => {
  const run = (response) => requestDocumentExport(job, artifact, 'pdf', 'report', { fetcher: async () => response });
  await assert.rejects(run(Response.json({ detail: '图片校验失败，请重新生成' }, { status: 422 })), /图片校验失败/);
  await assert.rejects(run(new Response('login required', { headers: { 'Content-Type': 'text/html' } })), /未返回所选文档/);
  await assert.rejects(run(new Response('', { headers: { 'Content-Type': 'application/pdf' } })), /文档为空/);
  const controller = new AbortController();
  const result = await requestDocumentExport(job, artifact, 'pdf', 'compact', {
    signal: controller.signal,
    fetcher: async (url, options) => {
      assert.equal(options.signal, controller.signal);
      assert.match(url, /layout=compact/);
      return new Response('%PDF-test', { headers: { 'Content-Type': 'application/pdf' } });
    },
  });
  assert.equal(result.blob.size, 9);
  assert.match(result.filename, /\.pdf$/);
  await assert.rejects(requestDocumentExport(job, artifact, 'pdf', 'report', { fetcher: async () => { throw new DOMException('Cancelled', 'AbortError'); } }), { name: 'AbortError' });
});


test('native download header uses saved file URL only with matching task and project scope', async () => {
  assert.equal(verifiedDocumentUrl(job, entry.url, 'https://paired.test'), `https://paired.test${entry.url}`);
  assert.equal(verifiedDocumentUrl(job, '/api/workbench/jobs/current-job/documents/not-a-uuid?project_id=current-project'), '');
  assert.equal(verifiedDocumentUrl({ ...job, project_id: null }, entry.url), '');
  assert.ok(verifiedDocumentUrl({ ...job, project_id: null }, entry.url.split('?')[0]));
  const good = await requestDocumentExport(job, artifact, 'pdf', 'report', { fetcher: async () => new Response('%PDF-test', { headers: { 'Content-Type': 'application/pdf', 'X-ModelDesk-Document-URL': entry.url } }) });
  assert.equal(new URL(good.downloadUrl).pathname, entry.url.split('?')[0]);
  await assert.rejects(requestDocumentExport(job, artifact, 'pdf', 'report', { fetcher: async () => new Response('%PDF-test', { headers: { 'Content-Type': 'application/pdf', 'X-ModelDesk-Document-URL': entry.url.replace('current-project', 'other-project') } }) }), /与当前任务不匹配/);
});
