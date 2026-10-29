const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MIME = { image: /^(?:image\/(?:png|jpeg|webp|gif))$/, video: /^(?:video\/(?:mp4|webm|quicktime))$/ };
const projectScope = (value) => value || null;
const MEDIA_WARNINGS = new Set([
  'Muse 媒体任务记录无法读取，未将资源计为完成。',
  'Muse 原生附件没有通过当前任务的文件完整性或来源校验。',
  'Muse 返回的一个资源未通过下载或真实媒体校验，未作为成品展示。',
  'Muse 附件下载未全部确认；未取得的文件不能作为已完成媒体。',
  'Muse 附件标识无效，未读取该文件。',
  'Muse 附件未通过真实图片或视频校验，未计入已完成媒体。',
]);

export function deliveryMediaWarnings(delivery = {}) {
  const values = Array.isArray(delivery.media_warnings) ? delivery.media_warnings : [];
  return [...new Set(values.filter((value) => typeof value === 'string' && MEDIA_WARNINGS.has(value.trim())).map((value) => value.trim()))];
}

/** Managed media must belong to this job and project, and be served by our API. */
export function resolveManagedMediaUrl(value, asset, job, apiBase) {
  if (!UUID.test(job?.id || '') || !UUID.test(asset?.id || '') || asset.job_id !== job.id
      || projectScope(asset.project_id) !== projectScope(job.project_id) || typeof value !== 'string' || !value.trim()) return null;
  try {
    const base = new URL(apiBase);
    const candidate = new URL(value, base);
    const path = `/api/media/jobs/${job.id}/assets/${asset.id}/content`;
    if (!/^https?:$/.test(candidate.protocol) || candidate.origin !== base.origin
        || candidate.username || candidate.password || candidate.hash || candidate.pathname !== path) return null;
    const parameters = [...candidate.searchParams.keys()];
    if (parameters.some((key) => !['project_id', 'download'].includes(key))
        || new Set(parameters).size !== parameters.length
        || projectScope(candidate.searchParams.get('project_id')) !== projectScope(job.project_id)
        || (candidate.searchParams.has('download') && candidate.searchParams.get('download') !== '1')) return null;
    // Always use the current API host, including the paired phone's origin.
    return candidate.href;
  } catch { return null; }
}

export function managedDeliveryMedia(job, apiBase) {
  const seen = new Set();
  const media = job?.result?.delivery?.media;
  return (Array.isArray(media) ? media : []).flatMap((asset) => {
    if (!asset || !MIME[asset.kind]?.test(asset.mime_type || '') || seen.has(asset.id)
        || !/^[a-f0-9]{64}$/i.test(asset.sha256 || '') || !Number.isSafeInteger(asset.size) || asset.size < 1) return [];
    const url = resolveManagedMediaUrl(asset.url, asset, job, apiBase);
    const downloadUrl = resolveManagedMediaUrl(asset.download_url, asset, job, apiBase);
    if (!url || !downloadUrl) return [];
    seen.add(asset.id);
    return [{ ...asset, original_url: asset.url, url, download_url: downloadUrl }];
  });
}

/** Reused files have new IDs; old source links must open this job's verified copy. */
export function resolveReusedMediaLink(value, asset, job, apiBase) {
  const current = resolveManagedMediaUrl(asset?.url, asset, job, apiBase);
  const sourceId = /^reused-([0-9a-f-]+)$/i.exec(asset?.slot_id || '')?.[1];
  if (!current || !UUID.test(sourceId || '') || !UUID.test(asset.source_task_id || '')) return null;
  const source = resolveManagedMediaUrl(value,
    { ...asset, id: sourceId, job_id: asset.source_task_id },
    { ...job, id: asset.source_task_id }, apiBase);
  if (!source) return null;
  return new URL(source).searchParams.has('download')
    ? resolveManagedMediaUrl(asset.download_url, asset, job, apiBase) : current;
}

export function mediaBundleUrl(job, apiBase) {
  if (!UUID.test(job?.id || '') || job.mode !== 'group' || !job.result?.delivery) return null;
  try {
    const url = new URL(`/api/media/jobs/${job.id}/bundle`, apiBase);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) return null;
    if (job.project_id) url.searchParams.set('project_id', job.project_id);
    return url.href;
  } catch { return null; }
}

/** Match inline and reference Markdown images for the gallery's duplicate check. */
export function embeddedMediaIds(content, media) {
  const definitions = new Map();
  const value = String(content || '').replace(/^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?^ {0,3}\1[^\n]*$/gm, '').replace(/(`+)[\s\S]*?\1/g, '');
  const normalize = (label) => label.trim().replace(/\s+/g, ' ').toLowerCase();
  for (const match of value.matchAll(/^ {0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?/gm)) definitions.set(normalize(match[1]), match[2]);
  const urls = [];
  for (const match of value.matchAll(/(?<!\\)!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+["'][^"']*["'])?\s*\)/g)) urls.push(match[1] || match[2]);
  for (const match of value.matchAll(/(?<!\\)!\[([^\]]*)\](?:\[([^\]]*)\])?(?!\()/g)) urls.push(definitions.get(normalize(match[2] || match[1])));
  return new Set(media.filter((asset) => urls.some((url) => url && [asset.url, asset.original_url].includes(url))).map((asset) => asset.id));
}

export function deliveryMediaProgress(delivery = {}) {
  const requirements = delivery.task_spec?.media || delivery.media_requirements || {};
  const assets = Array.isArray(delivery.media) ? delivery.media : [];
  const tasks = Array.isArray(delivery.media_tasks) ? delivery.media_tasks : [];
  return ['image', 'video'].map((kind) => ({
    kind,
    required: Math.max(0, Number(requirements[kind === 'image' ? 'images' : 'videos']) || 0),
    complete: assets.filter((asset) => asset.kind === kind).length,
    tasks: tasks.filter((task) => task.kind === kind),
  })).filter((item) => item.required || item.complete || item.tasks.length);
}

/** Only publish safe capability metadata; a catalog candidate is not a proven image tool. */
export function normalizeMediaCapabilities(payload) {
  const candidates = Array.isArray(payload) ? payload : payload?.providers;
  if (!Array.isArray(candidates)) throw new Error('暂时无法读取生图能力，可继续提交任务。');
  const seen = new Set();
  return candidates.slice(0, 1000).flatMap((entry) => {
    if (!entry || entry.kind !== 'image' || !['openai', 'gemini', 'xai'].includes(entry.provider)
        || typeof entry.id !== 'string' || !entry.id.trim() || seen.has(entry.id)) return [];
    seen.add(entry.id);
    const model = typeof entry.model === 'string' && entry.model || entry.id;
    return [{
      id: entry.id.slice(0, 200), provider: entry.provider,
      model: model.slice(0, 200),
      label: (typeof entry.label === 'string' && entry.label || model).slice(0, 200),
      available: entry.available === true,
      verified: entry.available === true && entry.verified === true,
    }];
  }).sort((a, b) => Number(b.verified) - Number(a.verified) || Number(b.available) - Number(a.available) || a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model));
}

export async function loadMediaCapabilities(apiBase, request = globalThis.fetch, signal) {
  try {
    const response = await request(new URL('/api/media/capabilities', apiBase).href, { signal });
    if (!response.ok) throw new Error('capability request failed');
    return normalizeMediaCapabilities(await response.json());
  } catch (error) {
    if (signal?.aborted) throw error;
    // Never expose an upstream body or transport URL, which may contain credentials.
    throw new Error('暂时无法读取生图能力，可继续提交任务；系统执行时会再次检查。');
  }
}
