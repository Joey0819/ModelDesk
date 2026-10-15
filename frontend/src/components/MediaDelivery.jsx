import { defaultUrlTransform } from 'react-markdown';
import { API_BASE } from '../api-base';
import { deliveryMediaProgress, deliveryMediaWarnings, embeddedMediaIds, managedDeliveryMedia, resolveManagedMediaUrl } from '../delivery-media';
import { Icon, Markdown } from './WorkbenchResults';
import './MediaDelivery.css';

const taskStatus = { pending: '等待生成', running: '正在生成', complete: '已生成', error: '生成失败', cancelled: '已取消' };
const sizeLabel = (size) => size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`;
const label = (asset) => asset.caption || asset.name || (asset.kind === 'video' ? '生成的视频' : '生成的配图');
const hasSavedTaskMedia = (task, media) => {
  const ids = Array.isArray(task.asset_ids) && task.asset_ids.length ? task.asset_ids : task.asset_id ? [task.asset_id] : [];
  return ids.length > 0 && ids.every((id) => media.some((asset) => asset.id === id && asset.kind === task.kind));
};

function MediaPreview({ asset }) {
  return asset.kind === 'video'
    ? <video className="delivery-video" src={asset.url} controls playsInline preload="metadata" aria-label={label(asset)} />
    : <img className="delivery-image" src={asset.url} alt={label(asset)} loading="lazy" decoding="async" />;
}

export function DeliveryMediaProgress({ job, apiBase = API_BASE }) {
  const delivery = job?.result?.delivery;
  if (!delivery) return null;
  const media = managedDeliveryMedia(job, apiBase);
  const progress = deliveryMediaProgress({ ...delivery, media });
  const warnings = deliveryMediaWarnings(delivery);
  if (!progress.length && !warnings.length) return null;
  return <section className="delivery-media-progress" aria-label="图片与视频生成进度">
    <div className="delivery-media-counts">{progress.map((item) => <span key={item.kind}><Icon name={item.kind === 'image' ? 'file' : 'layers'} size={13} /><strong>{item.kind === 'image' ? '配图' : '视频'} {item.complete}/{Math.max(item.required, item.complete)}</strong>{item.tasks.some((task) => task.status === 'running') && <span className="small-spinner" />}</span>)}</div>
    {progress.some((item) => item.tasks.length) && <ul>{progress.flatMap((item) => item.tasks.map((task, index) => <li key={task.id || `${item.kind}-${index}`} className={`delivery-media-task-${task.status}`}><div><strong>{item.kind === 'image' ? '配图' : '视频'} {index + 1}</strong><span>{task.provider || task.model || '正在选择生成工具'}</span><span>{task.status === 'complete' && !hasSavedTaskMedia(task, media) ? '未收到已保存文件' : taskStatus[task.status] || '状态待同步'}</span></div>{task.error && <p className="output-error">{task.error}</p>}</li>))}</ul>}
    {warnings.length > 0 && <details className="delivery-media-warnings"><summary>未采用的资源说明（{warnings.length}）</summary><ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
  </section>;
}

/** Rich delivery only loads stored media from this job's verified asset manifest. */
export function MediaDelivery({ job, content, apiBase = API_BASE }) {
  const media = managedDeliveryMedia(job, apiBase);
  const embedded = embeddedMediaIds(content, media);
  const remaining = media.filter((asset) => !embedded.has(asset.id));
  const imageAsset = (source) => media.find((asset) => resolveManagedMediaUrl(source, asset, job, apiBase));
  const transform = (url, key) => {
    const asset = imageAsset(url);
    if (key === 'src') return asset?.url || '';
    return asset ? resolveManagedMediaUrl(url, asset, job, apiBase) : defaultUrlTransform(url);
  };
  const renderImage = ({ src, alt }) => {
    const asset = imageAsset(src);
    return asset ? <MediaPreview asset={{ ...asset, caption: alt || asset.caption }} /> : <span className="delivery-media-missing">图片或视频尚未保存，未加载未经确认的地址。</span>;
  };
  return <div className="media-delivery">
    <Markdown components={{ img: renderImage }} urlTransform={transform}>{content}</Markdown>
    {remaining.length > 0 && <section className="delivery-media-gallery" aria-label="本次生成的图片与视频">{remaining.map((asset) => <figure key={asset.id}><MediaPreview asset={asset} /><figcaption>{label(asset)}</figcaption></figure>)}</section>}
    {media.length > 0 && <section className="delivery-originals" aria-label="下载原始图片与视频"><h3>本次生成的文件</h3><ul>{media.map((asset) => <li key={asset.id}><div><strong>{asset.name || label(asset)}</strong><span>{asset.provider}{asset.model && ` · ${asset.model}`} · {sizeLabel(asset.size)}</span></div><a href={asset.download_url} download={asset.name} aria-label={`下载原始${asset.kind === 'image' ? '图片' : '视频'} ${label(asset)}`}><Icon name="export" size={14} />下载原件</a></li>)}</ul></section>}
  </div>;
}
