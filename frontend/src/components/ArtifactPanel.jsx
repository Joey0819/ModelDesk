import { useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { groupTaskApi } from '../group-task-api';
import { museApi } from '../muse-api';
import { deriveArtifacts } from '../artifacts';
import { Icon, Markdown } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import './ArtifactPanel.css';

const statusLabel = (artifact) => artifact.status === 'live' ? '生成中' : artifact.status === 'partial' ? '部分输出' : artifact.kind === 'discussion' ? '已保存发言' : '已生成';

function ArtifactPreview({ artifact, source }) {
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
      : <Markdown>{artifact.content}</Markdown>}
  </>;
}

function downloadUrl(job, artifact) {
  if (job.mode === 'muse_task') return museApi.exportUrl(job.id, job.project_id);
  if (job.mode === 'group_agent') return groupTaskApi.exportUrl(job.id, job.project_id, job.room_id);
  return `${workbenchApi.exportUrl(job.id, 'md')}&artifact_id=${encodeURIComponent(artifact.id)}`;
}

function downloadName(job, artifact) {
  return `ModelDesk-${job.id.slice(0, 8)}-${artifact.title.replace(/[^\p{L}\p{N}._-]+/gu, '-').slice(0, 60)}.md`;
}

export default function ArtifactPanel({ job, onClose }) {
  const [selection, setSelection] = useState(null);
  const [source, setSource] = useState(false);
  const dialog = useRef(null);
  const artifacts = deriveArtifacts(job);
  const downloadLabel = job?.mode === 'group_agent' ? '下载任务记录 Markdown' : '下载当前成果 Markdown';
  const selected = (selection?.jobId === job?.id && artifacts.find((artifact) => artifact.id === selection.id)) || artifacts[0];
  const choose = (id) => { setSelection({ jobId: job.id, id }); setSource(false); };
  return <aside className="artifact-panel" aria-label="当前任务成果">
    <header className="artifact-panel-header"><div><Icon name="file" size={18} /><strong>成果</strong>{artifacts.length > 0 && <span>{artifacts.length}</span>}</div>{onClose && <button className="artifact-icon-button" type="button" onClick={onClose} aria-label="收起成果面板" title="收起成果面板"><Icon name="close" size={17} /></button>}</header>
    {selected ? <>
      <div className="artifact-selection"><label><span>当前成果</span><select aria-label="选择预览成果" value={selected.id} onChange={(event) => choose(event.target.value)}>{artifacts.map((artifact) => <option key={artifact.id} value={artifact.id}>{artifact.title}{artifact.status === 'partial' ? ' · 部分输出' : ''}</option>)}</select></label><span className={`artifact-status artifact-status-${selected.status}`}>{statusLabel(selected)}</span></div>
      <div className="artifact-preview-toolbar"><div className="artifact-preview-tabs" aria-label="预览方式"><button className={!source ? 'selected' : ''} type="button" onClick={() => setSource(false)}>预览</button><button className={source ? 'selected' : ''} type="button" onClick={() => setSource(true)}>{selected.format === 'markdown' ? 'Markdown' : '文本'}</button></div><div className="artifact-toolbar-actions"><CopyTextButton text={selected.content} label="复制" /><a className="artifact-icon-button" title={downloadLabel} aria-label={downloadLabel} href={downloadUrl(job, selected)} download={downloadName(job, selected)}><Icon name="export" size={15} /></a><button className="artifact-icon-button" type="button" title="放大预览" aria-label="放大预览" onClick={() => dialog.current?.showModal()}><Icon name="expand" size={15} /></button></div></div>
      <div className="artifact-preview-scroll"><div className="artifact-document-heading"><h2>{selected.title}</h2><p>{selected.source}{selected.model && ` · ${selected.model}`}</p></div>{selected.status === 'partial' && <p className="artifact-partial-note">这是已收到的部分内容，整体任务尚未完整完成。</p>}{selected.note && <p className="artifact-content-note">{selected.note}</p>}<ArtifactPreview artifact={selected} source={source} /></div>
      <footer className="artifact-panel-footer"><span>{selected.status === 'live' ? '内容随模型输出更新' : '来自当前任务的实际输出'}</span><a href={downloadUrl(job, selected)} download={downloadName(job, selected)}>{job?.mode === 'group_agent' ? '下载任务记录' : '下载 Markdown'}</a></footer>
      <dialog className="artifact-preview-dialog" ref={dialog}><header><div><strong>{selected.title}</strong><span>{selected.source} · {statusLabel(selected)}</span></div><div><CopyTextButton text={selected.content} label="复制内容" /><a className="artifact-icon-button" href={downloadUrl(job, selected)} download={downloadName(job, selected)} aria-label={downloadLabel}><Icon name="export" size={17} /></a><button type="button" className="artifact-icon-button" aria-label="关闭放大预览" onClick={() => dialog.current?.close()}><Icon name="close" size={18} /></button></div></header><div className="artifact-dialog-content">{selected.status === 'partial' && <p className="artifact-partial-note">部分输出，任务尚未完整完成。</p>}{selected.note && <p className="artifact-content-note">{selected.note}</p>}<ArtifactPreview artifact={selected} source={source} /></div></dialog>
    </> : <div className="artifact-empty"><Icon name="file" size={29} /><strong>{job ? ['error', 'failed', 'cancelled', 'interrupted'].includes(job.status) ? '本次没有可预览的成果' : '等待成果生成' : '成果会显示在这里'}</strong><p>{job ? '实际回答、交付内容或保留下来的证据生成后，会自动出现在此处。' : '选择一条记录或发起任务，查看本次输出并复制、下载。'}</p></div>}
  </aside>;
}
