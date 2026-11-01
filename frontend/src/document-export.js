import { API_BASE, apiFetch } from './api-base.js';

export const DOCUMENT_FORMATS = [
  { id: 'docx', label: 'Word', detail: '可继续编辑的文档', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  { id: 'pdf', label: 'PDF', detail: '适合打印与分享', type: 'application/pdf' },
  { id: 'html', label: '网页', detail: '含图片的独立 HTML', type: 'text/html' },
];

export const DOCUMENT_LAYOUTS = [
  { id: 'report', label: '标准报告', detail: '清晰标题、正文与表格' },
  { id: 'article', label: '阅读文章', detail: '宽松行距，适合长文' },
  { id: 'compact', label: '紧凑简报', detail: '减少留白，便于速览' },
];

export function canExportDocument(job, artifact) {
  const source = artifact?.origin_job || job;
  return Boolean(source?.id && artifact?.content?.trim() && artifact.status !== 'live'
    && !['muse_task', 'group_agent'].includes(source.mode));
}

export function documentExportUrl(job, artifact, format, layout, base = API_BASE) {
  const source = artifact?.origin_job || job;
  if (!canExportDocument(job, artifact)) throw new Error('请等待实际成果生成后再导出文档。');
  if (!DOCUMENT_FORMATS.some((item) => item.id === format) || !DOCUMENT_LAYOUTS.some((item) => item.id === layout)) {
    throw new Error('请选择支持的文档格式与版式。');
  }
  const query = new URLSearchParams({ format, artifact_id: artifact.raw_id || artifact.id, layout });
  return `${base}/api/workbench/jobs/${encodeURIComponent(source.id)}/export?${query}`;
}

export function documentFilename(job, artifact, format) {
  const source = artifact?.origin_job || job;
  const title = String(artifact?.title || '成果').replace(/[^\p{L}\p{N}._-]+/gu, '-').replace(/^\.+/, '').slice(0, 80) || '成果';
  return `ModelDesk-${String(source?.id || '').slice(0, 8)}-${title}.${format}`;
}

export function verifiedDocumentUrl(source, value, base = API_BASE) {
  if (typeof value !== 'string' || !source?.id) return '';
  try {
    const url = new URL(value, base);
    const prefix = `/api/workbench/jobs/${encodeURIComponent(source.id)}/documents/`;
    const identifier = url.pathname.slice(prefix.length);
    if (url.origin !== new URL(base).origin || !url.pathname.startsWith(prefix)
      || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(identifier)
      || url.hash || url.username || url.password) return '';
    const project = url.searchParams.get('project_id');
    if ((source.project_id || null) !== project) return '';
    if ([...url.searchParams.keys()].some((key) => key !== 'project_id') || url.searchParams.getAll('project_id').length > 1) return '';
    return url.href;
  } catch { return ''; }
}

export function preparedDocuments(job, artifact, base = API_BASE) {
  const source = artifact?.origin_job || job;
  if (!source?.id || (artifact?.raw_id || artifact?.id) !== 'group:delivery') return [];
  const files = source.result?.delivery?.documents;
  return (Array.isArray(files) ? files : []).flatMap((file) => {
    if (!file || file.status !== 'ready' || !DOCUMENT_FORMATS.some((item) => item.id === file.format)
      || !DOCUMENT_LAYOUTS.some((item) => item.id === file.layout)
      || !/^[a-f0-9]{64}$/.test(file.sha256 || '') || !/^[a-f0-9]{64}$/.test(file.content_sha256 || '')
      || !Number.isSafeInteger(file.size) || file.size < 1) return [];
    const url = verifiedDocumentUrl(source, file.url, base);
    if (!url || !new URL(url).pathname.endsWith(`/documents/${file.id}`)) return [];
    return [{ ...file, url }];
  });
}

export async function requestDocumentExport(job, artifact, format, layout, { signal, fetcher = apiFetch } = {}) {
  let response;
  try {
    response = await fetcher(documentExportUrl(job, artifact, format, layout), { signal });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(error.message?.startsWith('请选择') || error.message?.startsWith('请等待')
      ? error.message : '无法连接文档服务，请检查连接后重试。');
  }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* A proxy can return a non-JSON error. */ }
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg).filter(Boolean).join('；');
    throw new Error(typeof detail === 'string' ? detail : `文档生成失败（${response.status}），请重试。`);
  }
  const expected = DOCUMENT_FORMATS.find((item) => item.id === format).type;
  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== expected) {
    throw new Error('服务未返回所选文档格式，未下载无效文件。');
  }
  const blob = await response.blob();
  if (!blob.size) throw new Error('生成的文档为空，请重试。');
  const rawUrl = response.headers.get('X-ModelDesk-Document-URL');
  const downloadUrl = rawUrl ? verifiedDocumentUrl(artifact.origin_job || job, rawUrl) : '';
  if (rawUrl && !downloadUrl) throw new Error('文档下载地址与当前任务不匹配，未下载文件。');
  return { blob, downloadUrl, filename: documentFilename(job, artifact, format) };
}
