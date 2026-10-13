export const ATTACHMENT_LIMITS = { file: 20000000, batch: 100000000, batchCount: 100, count: 30, image: 10000000, imageCount: 8, imageTotal: 40000000 };
const privateName = /(^|\/)(?:\.env(?:\..*)?|id_rsa|id_ed25519|credentials(?:\.[^/]*)?|tokens?(?:\.[^/]*)?|\.DS_Store|\.git|node_modules|__pycache__|\.venv|venv|\.cache|dist|build)(\/|$)/i;

export function readableSize(size = 0) {
  return size >= 1000000 ? `${(size / 1000000).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1000))} KB`;
}

export function relativeFilePath(file) {
  return String(file.relative_path || file.webkitRelativePath || file.name || '').replace(/\\/g, '/');
}

export function fileExclusion(file) {
  const path = relativeFilePath(file);
  if (!path || path.startsWith('/') || path.split('/').includes('..')) return '文件路径不安全';
  if (privateName.test(path)) return '敏感文件或缓存目录，未读取和上传';
  if (file.size > ATTACHMENT_LIMITS.file) return '超过单文件 20 MB 上限';
  if (file.type?.startsWith('image/') && !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return '图片仅支持 PNG、JPEG、WebP';
  if (file.type?.startsWith('image/') && file.size > ATTACHMENT_LIMITS.image) return '图片超过 10 MB 上限';
  return null;
}

export function prepareFiles(files) {
  const entries = [], omitted = [];
  let bytes = 0;
  for (const file of Array.from(files || [])) {
    const relative_path = relativeFilePath(file);
    const reason = fileExclusion(file) || (entries.length >= ATTACHMENT_LIMITS.batchCount ? '超过一次 100 个文件上限' : bytes + file.size > ATTACHMENT_LIMITS.batch ? '超过一次 100 MB 总量上限' : null);
    if (reason) omitted.push({ relative_path, reason });
    else { entries.push({ file, relative_path }); bytes += file.size; }
  }
  return { entries, omitted, bytes };
}

export function selectedAssets(assets, ids = [], includeShared = true) {
  return (assets || []).filter((asset) => ids.includes(asset.id) || (includeShared && asset.shared));
}

export function attachmentIssues(assets, ids = [], includeShared = true) {
  const selected = selectedAssets(assets, ids, includeShared);
  const issues = [];
  if (ids.some((id) => !assets.some((asset) => asset.id === id))) issues.push('有附件已被移除或尚未读取，请重新选择。');
  if (selected.some((asset) => asset.status !== 'ready')) issues.push('部分附件无法提供给模型，请移除或更换。');
  if (selected.length > ATTACHMENT_LIMITS.count) issues.push('本轮最多使用 30 项资料。');
  const images = selected.filter((asset) => asset.kind === 'image');
  if (images.length > ATTACHMENT_LIMITS.imageCount || images.reduce((sum, asset) => sum + asset.size, 0) > ATTACHMENT_LIMITS.imageTotal) issues.push('本轮图片最多 8 张、总计 40 MB。');
  return issues;
}

export function addAssetsWithinBudget(assets, currentIds, additions, includeShared = true) {
  let ids = [...new Set(currentIds)]; const omitted = [];
  for (const asset of additions) {
    if (asset.status !== 'ready') { omitted.push({ relative_path: asset.relative_path || asset.name, reason: asset.reason || '文件格式无法提供给模型' }); continue; }
    const next = [...new Set([...ids, asset.id])];
    if (attachmentIssues(assets, next, includeShared).length) omitted.push({ relative_path: asset.relative_path || asset.name, reason: '已保存到项目，但超过本轮资料预算，未自动加入' });
    else ids = next;
  }
  return { ids, omitted };
}
