import { useMemo, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { groupTaskApi } from '../group-task-api';
import { museApi } from '../muse-api';
import { computeArtifactDiff, deriveArtifacts, deriveRoomArtifacts, filterArtifactsByCategory } from '../artifacts';
import { API_BASE } from '../api-base';
import { managedDeliveryMedia, mediaBundleUrl } from '../delivery-media';
import { Icon, Markdown } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import { DeliveryMediaProgress, MediaDelivery } from './MediaDelivery';
import DocumentExportControl from './DocumentExportControl';
import { canExportDocument, DOCUMENT_FORMATS, DOCUMENT_LAYOUTS, preparedDocuments } from '../document-export';
import './ArtifactPanel.css';

const statusLabel = (artifact) => artifact.status === 'live' ? '生成中' : artifact.status === 'partial' ? '部分输出' : artifact.kind === 'discussion' ? '已保存发言' : '已生成';

const CATEGORY_TABS = [
  ['all', '全部'],
  ['deliverable', '交付文档'],
  ['media', '图文与视频'],
  ['code', '代码与补丁'],
  ['discussion', '讨论发言'],
];

function ArtifactPreview({ artifact, viewMode, diffTarget, job }) {
  const effectiveJob = artifact.origin_job || job;
  const rawId = artifact.raw_id || artifact.id;
  if (artifact.media_only) return <MediaDelivery job={effectiveJob} content="" />;
  if (viewMode === 'diff' && diffTarget) {
    const diff = computeArtifactDiff(diffTarget.content, artifact.content);
    return (
      <div className="artifact-diff-view" aria-label="历史版本对比视图">
        <div className="artifact-diff-summary">
          <strong>版本差异对比</strong>
          <span className="artifact-diff-added">+{diff.addedCount} 行新增</span>
          <span className="artifact-diff-removed">-{diff.removedCount} 行移除</span>
          <span>基准：{diffTarget.title}</span>
        </div>
        <pre className="artifact-diff-lines">
          {diff.lines.map((line, index) => (
            <div key={index} className={`artifact-diff-line artifact-diff-${line.type}`}>
              <span className="artifact-diff-prefix">{line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}</span>
              <code>{line.text || ' '}</code>
            </div>
          ))}
        </pre>
      </div>
    );
  }
  const source = viewMode === 'source';
  return <>
    {!source && artifact.video_url && (
      <div className="artifact-video-container">
        <video className="artifact-video-player" src={artifact.video_url} controls playsInline preload="metadata" loop />
        <div className="artifact-video-actions">
          <span>Muse 已将生成的视频回传至当前工作台</span>
          <a className="primary-button" href={artifact.video_url} download="ModelDesk-Muse-Video.mp4">下载视频 MP4</a>
        </div>
      </div>
    )}
    {(source || ['code', 'text'].includes(artifact.format))
      ? <pre className="artifact-source-preview"><code>{artifact.content}</code></pre>
      : rawId === 'group:delivery' ? <MediaDelivery job={effectiveJob} content={artifact.content} /> : <Markdown>{artifact.content}</Markdown>}
  </>;
}

function downloadUrl(job, artifact) {
  const effectiveJob = artifact.origin_job || job;
  const rawId = artifact.raw_id || artifact.id;
  if (effectiveJob.mode === 'muse_task') return museApi.exportUrl(effectiveJob.id, effectiveJob.project_id);
  if (effectiveJob.mode === 'group_agent') return groupTaskApi.exportUrl(effectiveJob.id, effectiveJob.project_id, effectiveJob.room_id);
  return `${workbenchApi.exportUrl(effectiveJob.id, 'md')}&artifact_id=${encodeURIComponent(rawId)}`;
}

function downloadName(job, artifact) {
  const effectiveJob = artifact.origin_job || job;
  return `ModelDesk-${effectiveJob.id.slice(0, 8)}-${artifact.title.replace(/[^\p{L}\p{N}._-]+/gu, '-').slice(0, 60)}.md`;
}

export default function ArtifactPanel({ job, onClose }) {
  const [selection, setSelection] = useState(null);
  const [viewMode, setViewMode] = useState('preview');
  const [diffBaseId, setDiffBaseId] = useState('');
  const [scope, setScope] = useState('current');
  const [category, setCategory] = useState('all');
  const dialog = useRef(null);
  const currentArtifacts = useMemo(() => deriveArtifacts(job), [job]);
  const roomArtifacts = useMemo(() => deriveRoomArtifacts(job?.room_history), [job?.room_history]);
  const hasRoomGallery = roomArtifacts.length > 1;
  const activeScope = hasRoomGallery && scope === 'room' ? 'room' : 'current';
  const baseList = activeScope === 'room' ? roomArtifacts : currentArtifacts;
  const filteredArtifacts = useMemo(() => {
    const matches = filterArtifactsByCategory(baseList, category);
    return matches.length ? matches : baseList;
  }, [baseList, category]);

  const effectiveJobForSelection = (artifact) => artifact?.origin_job || job;
  const selected = (selection && filteredArtifacts.find((artifact) => artifact.id === selection.id)) || filteredArtifacts[0];
  const hasText = Boolean(selected?.content?.trim());
  const diffCandidates = useMemo(
    () => hasText ? filteredArtifacts.filter((item) => item.id !== selected?.id && item.content?.trim()) : [],
    [filteredArtifacts, selected?.id, hasText]
  );
  const diffTarget = useMemo(
    () => diffCandidates.find((item) => item.id === diffBaseId) || diffCandidates[0] || null,
    [diffCandidates, diffBaseId]
  );
  const selectedJob = effectiveJobForSelection(selected);
  const selectedRawId = selected?.raw_id || selected?.id;
  const delivery = selectedJob?.result?.delivery;
  const downloadLabel = selectedJob?.mode === 'group_agent' ? '下载任务记录 Markdown' : '下载当前成果 Markdown';
  const bundleUrl = selectedRawId === 'group:delivery' && managedDeliveryMedia(selectedJob, API_BASE).length ? mediaBundleUrl(selectedJob, API_BASE) : null;
  const documentFiles = preparedDocuments(selectedJob, selected);
  const documentExport = canExportDocument(selectedJob, selected);
  const bodyHeading = selected?.content?.trimStart().match(/^#{1,2}\s+([^\n]+)(?:\n|$)/)?.[1]?.trim();
  const showTitle = viewMode !== 'preview' || bodyHeading !== selected?.title;
  const choose = (id) => {
    setSelection({ jobId: job?.id, id });
    if (viewMode === 'diff' && diffCandidates.length === 0) setViewMode('preview');
  };

  return <aside className="artifact-panel" aria-label="当前任务成果">
    <header className="artifact-panel-header">
      <div>
        <Icon name="file" size={18} />
        <strong>成果</strong>
        {filteredArtifacts.length > 0 && <span>{filteredArtifacts.length}</span>}
      </div>
      {hasRoomGallery && (
        <div className="artifact-scope-tabs" role="group" aria-label="成果聚合范围">
          <button type="button" className={activeScope === 'current' ? 'selected' : ''} onClick={() => { setScope('current'); setSelection(null); }}>
            当前轮 ({currentArtifacts.length})
          </button>
          <button type="button" className={activeScope === 'room' ? 'selected' : ''} onClick={() => { setScope('room'); setSelection(null); }}>
            本群全部成果 ({roomArtifacts.length})
          </button>
        </div>
      )}
      {onClose && <button className="artifact-icon-button artifact-close-button" type="button" onClick={onClose} aria-label="收起成果面板" title="返回对话"><span className="artifact-close-label">返回对话</span><Icon name="close" size={17} /></button>}
    </header>
    {baseList.length > 1 && (
      <div className="artifact-category-bar" role="toolbar" aria-label="筛选成果类型">
        {CATEGORY_TABS.map(([id, label]) => {
          const count = id === 'all' ? baseList.length : filterArtifactsByCategory(baseList, id).length;
          if (id !== 'all' && count === 0) return null;
          return (
            <button key={id} type="button" className={category === id ? 'selected' : ''} onClick={() => { setCategory(id); setSelection(null); }}>
              {label} ({count})
            </button>
          );
        })}
      </div>
    )}
    {filteredArtifacts.length > 1 && (
      <div className="artifact-gallery-strip" role="region" aria-label="本群产物时间轴">
        {filteredArtifacts.map((artifact) => (
          <button
            key={artifact.id}
            type="button"
            className={`artifact-gallery-card ${selected?.id === artifact.id ? 'selected' : ''}`}
            onClick={() => choose(artifact.id)}
            title={artifact.title}
          >
            <span className="artifact-gallery-badge">
              {artifact.video_url ? '视频' : artifact.format === 'code' ? '代码' : artifact.kind === 'discussion' ? '发言' : '交付'}
            </span>
            <strong>{artifact.title}</strong>
          </button>
        ))}
      </div>
    )}
    {selected ? <>
      <div className="artifact-selection">
        <label>
          <span>{activeScope === 'room' ? '本群成果列表' : '当前成果'}</span>
          <select aria-label="选择预览成果" value={selected.id} onChange={(event) => choose(event.target.value)}>
            {filteredArtifacts.map((artifact) => (
              <option key={artifact.id} value={artifact.id}>
                {artifact.title}{artifact.status === 'partial' ? ' · 部分输出' : ''}
              </option>
            ))}
          </select>
        </label>
        <span className={`artifact-status artifact-status-${selected.status}`}>{statusLabel(selected)}</span>
      </div>
      <div className="artifact-preview-toolbar">
        <div className="artifact-preview-tabs" aria-label="预览方式">
          <button className={viewMode === 'preview' || !hasText ? 'selected' : ''} type="button" onClick={() => setViewMode('preview')}>{hasText ? '预览' : '媒体预览'}</button>
          {hasText && <button className={viewMode === 'source' ? 'selected' : ''} type="button" onClick={() => setViewMode('source')}>{selected.format === 'markdown' ? 'Markdown' : '文本'}</button>}
          {diffCandidates.length > 0 && (
            <button className={viewMode === 'diff' ? 'selected' : ''} type="button" onClick={() => setViewMode('diff')}>对比版本</button>
          )}
        </div>
        <div className="artifact-toolbar-actions">
          {hasText && <CopyTextButton text={selected.content} label="复制" />}
          {documentExport ? <DocumentExportControl key={`${selectedJob.id}:${selectedRawId}`} job={selectedJob} artifact={selected} /> : hasText && <a className="artifact-icon-button" title={downloadLabel} aria-label={downloadLabel} href={downloadUrl(selectedJob, selected)} download={downloadName(selectedJob, selected)}><Icon name="export" size={15} /></a>}
          <button className="artifact-icon-button" type="button" title="放大预览" aria-label="放大预览" onClick={() => dialog.current?.showModal()}><Icon name="expand" size={15} /></button>
        </div>
      </div>
      {documentFiles.length > 0 && <div className="artifact-prepared-documents" aria-label="已生成的交付文件"><span>交付文件</span>{documentFiles.map((file) => <a key={file.id} href={file.url} download={file.filename}><Icon name="file" size={14} /><strong>{DOCUMENT_FORMATS.find((item) => item.id === file.format)?.label}</strong><small>{DOCUMENT_LAYOUTS.find((item) => item.id === file.layout)?.label}</small><Icon name="export" size={13} /></a>)}</div>}
      {viewMode === 'diff' && diffCandidates.length > 0 && (
        <div className="artifact-diff-Selector">
          <label>
            <span>对比基准版本</span>
            <select aria-label="选择对比基准版本" value={diffTarget?.id || ''} onChange={(event) => setDiffBaseId(event.target.value)}>
              {diffCandidates.map((item) => (
                <option key={item.id} value={item.id}>{item.title} · {item.source}</option>
              ))}
            </select>
          </label>
        </div>
      )}
      <div className="artifact-preview-scroll"><div className="artifact-document-heading">{showTitle && <h2>{selected.title}</h2>}<p>{selected.source}{selected.model && ` · ${selected.model}`}</p></div>{selected.status === 'partial' && <p className="artifact-partial-note">这是已收到的部分内容，整体任务尚未完整完成。</p>}<ArtifactPreview artifact={selected} viewMode={viewMode} diffTarget={diffTarget} job={selectedJob} />{(selected.note || selectedRawId === 'group:delivery') && <details className="artifact-validation"><summary>查看验收意见与运行依据</summary>{selected.note && <p className="artifact-content-note">{selected.note}</p>}{selectedRawId === 'group:delivery' && viewMode !== 'diff' && <DeliveryMediaProgress job={selectedJob} />}</details>}</div>
      <footer className="artifact-panel-footer"><span>{selected.status === 'live' ? '内容随模型输出更新' : '来自当前任务的实际输出'}</span><div className="artifact-download-links">{bundleUrl && <a href={bundleUrl} download={`ModelDesk-${selectedJob.id.slice(0, 8)}.zip`}><Icon name="export" size={13} />下载图文成果包</a>}{hasText && <a href={downloadUrl(selectedJob, selected)} download={downloadName(selectedJob, selected)}>{selectedJob?.mode === 'group_agent' ? '下载任务记录' : '下载 Markdown'}</a>}</div></footer>
      <dialog className="artifact-preview-dialog" ref={dialog}><header><div><strong>{selected.title}</strong><span>{selected.source} · {statusLabel(selected)}</span></div><div>{bundleUrl && <a className="artifact-bundle-button" href={bundleUrl} download={`ModelDesk-${selectedJob.id.slice(0, 8)}.zip`}>下载图文成果包</a>}{hasText && <><CopyTextButton text={selected.content} label="复制内容" />{documentExport ? <DocumentExportControl key={`${selectedJob.id}:${selectedRawId}:expanded`} job={selectedJob} artifact={selected} /> : <a className="artifact-icon-button" href={downloadUrl(selectedJob, selected)} download={downloadName(selectedJob, selected)} aria-label={downloadLabel}><Icon name="export" size={17} /></a>}</>}<button type="button" className="artifact-icon-button" aria-label="关闭放大预览" onClick={() => dialog.current?.close()}><Icon name="close" size={18} /></button></div></header><div className="artifact-dialog-content">{selected.status === 'partial' && <p className="artifact-partial-note">部分输出，任务尚未完整完成。</p>}<ArtifactPreview artifact={selected} viewMode={viewMode} diffTarget={diffTarget} job={selectedJob} />{(selected.note || selectedRawId === 'group:delivery') && <details className="artifact-validation"><summary>查看验收意见与运行依据</summary>{selected.note && <p className="artifact-content-note">{selected.note}</p>}{selectedRawId === 'group:delivery' && viewMode !== 'diff' && <DeliveryMediaProgress job={selectedJob} />}</details>}</div></dialog>
    </> : <div className="artifact-empty"><Icon name="file" size={29} /><strong>{delivery?.status === 'blocked' || delivery?.status === 'partial' ? '本次未形成完整成果' : job ? ['error', 'failed', 'cancelled', 'interrupted'].includes(job.status) ? '本次没有可预览的成果' : '等待成果生成' : '成果会显示在这里'}</strong><p>{delivery ? '模型与 Agent 的执行进度显示在群聊中。完整交付正文生成后会在此呈现，讨论发言单独保留在时间线。' : job ? '实际回答、交付内容或保留下来的证据生成后，会自动出现在此处。' : '选择一条记录或发起任务，查看本次输出并复制、下载。'}</p>{delivery && <DeliveryMediaProgress job={job} />}{Array.isArray(delivery?.limitations) && delivery.limitations.filter((item) => typeof item === 'string').map((limit, index) => <p key={index}>{limit}</p>)}</div>}
  </aside>;
}
