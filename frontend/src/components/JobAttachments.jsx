import { useState } from 'react';
import { workbenchApi } from '../api';
import { readableSize } from '../attachment-files';
import { Icon } from './WorkbenchResults';
import './ProjectHub.css';

const sourceLabels = { turn: '本轮', project_shared: '项目共用', history: '历史图片' };

export default function JobAttachments({ job }) {
  const [preview, setPreview] = useState(null);
  const items = Array.isArray(job.attachments) ? job.attachments : [];
  if (!items.length) return null;
  return <section className="job-attachments" aria-label="这轮实际引用的附件"><div className="job-attachment-chips">{items.map((item) => <button type="button" key={`${item.id}-${item.source}`} title={`${item.relative_path || item.name} · ${sourceLabels[item.source] || '附件'} · ${readableSize(item.size)}`} onClick={() => setPreview(item)}>{item.kind === 'image' && workbenchApi.assetUrl(item) ? <img src={workbenchApi.assetUrl(item)} alt={item.name} loading="lazy" /> : <Icon name="file" size={17} />}<span><strong>{item.name}</strong><small>{sourceLabels[item.source] || '附件'}{item.truncated && ' · 文本截断'}</small></span></button>)}</div>{preview && <section className="job-attachment-preview"><header><strong title={preview.name}>{preview.name}</strong><div>{workbenchApi.assetUrl(preview) && <a href={workbenchApi.assetUrl(preview)} download={preview.name}>下载本轮资料副本</a>}<button type="button" className="icon-button" aria-label="收起附件预览" onClick={() => setPreview(null)}><Icon name="close" size={15} /></button></div></header>{preview.kind === 'image' && workbenchApi.assetUrl(preview) ? <img src={workbenchApi.assetUrl(preview)} alt={preview.name} /> : <pre>{preview.text_preview || '资料保存在本轮附件副本中，可下载复核。'}</pre>}<small>本轮保存的内容指纹：{preview.content_sha256 || preview.sha256 || '未记录'}{preview.source_job_id && ` · 最初来源：${preview.source_job_id}`}</small></section>}</section>;
}
