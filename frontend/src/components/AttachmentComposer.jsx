import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { addAssetsWithinBudget, attachmentIssues, prepareFiles, readableSize, selectedAssets } from '../attachment-files';
import { useProjects } from '../project-context';
import { Icon } from './WorkbenchResults';
import './ProjectHub.css';

const fileTypes = '.png,.jpg,.jpeg,.webp,.pdf,.docx,.csv,.txt,.md,.rst,.json,.yaml,.yml,.py,.js,.jsx,.ts,.tsx,.html,.xml,.toml,.ini,.sql,.c,.cpp,.h,.java,.go,.rs,.sh';

function nativeFile(item) {
  const data = Uint8Array.from(atob(item.data_base64), (character) => character.charCodeAt(0));
  const file = new File([data], item.name, { type: item.type || 'application/octet-stream' });
  if (item.relative_path) Object.defineProperty(file, 'relative_path', { value: item.relative_path });
  return file;
}

const AttachmentComposer = forwardRef(function AttachmentComposer({ attachmentIds = [], onChange, includeShared = true, onIncludeShared, disabled = false, shared = false, onPendingChange, compact = false }, ref) {
  const { activeProjectId, assets, refreshAssets, refreshProjects, ensureProject, beginTransfer, openManager } = useProjects();
  const [menuOpen, setMenuOpen] = useState(false);
  const [queue, setQueue] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [omitted, setOmitted] = useState([]);
  const [folderPreview, setFolderPreview] = useState(null);
  const [folderSelected, setFolderSelected] = useState([]);
  const [folderShared, setFolderShared] = useState(shared);
  const [pathEntry, setPathEntry] = useState(false), [folderPath, setFolderPath] = useState('');
  const input = useRef(null), directoryInput = useRef(null);
  const currentProject = useRef(activeProjectId), currentIds = useRef(attachmentIds), alive = useRef(true);
  const previousProject = useRef(activeProjectId);
  currentProject.current = activeProjectId; currentIds.current = attachmentIds;
  useEffect(() => { alive.current = true; return () => { alive.current = false; onPendingChange?.(false); }; }, [onPendingChange]);
  useEffect(() => { onPendingChange?.(Boolean(busy || queue.length || folderPreview)); }, [busy, queue.length, folderPreview, onPendingChange]);
  useEffect(() => {
    const previous = previousProject.current; previousProject.current = activeProjectId;
    if (previous === null || previous === activeProjectId) return;
    let disposed = false;
    queueMicrotask(() => { if (!disposed) { setNotice(''); setError(''); setOmitted([]); setFolderPreview(null); setFolderSelected([]); setQueue([]); setMenuOpen(false); setPathEntry(false); setFolderPath(''); } });
    return () => { disposed = true; };
  }, [activeProjectId]);

  const importResult = async (projectId, result) => {
    const fresh = await refreshAssets(projectId); await refreshProjects();
    if (!alive.current || projectId !== currentProject.current) return;
    const added = addAssetsWithinBudget(fresh, currentIds.current, result.assets || [], includeShared);
    onChange?.(added.ids); setOmitted([...(result.omitted || []), ...added.omitted]);
    setNotice(`已保存 ${result.assets?.length || 0} 项资料到项目${added.omitted.length ? '；超出本轮预算的资料未加入任务' : '，可用于本轮任务'}。`);
  };
  const upload = async (entries, localOmitted = [], asShared = shared) => {
    if (!entries.length) { setOmitted(localOmitted); return; }
    setBusy(true); setError(''); setNotice(''); setQueue(entries); setMenuOpen(false);
    const endTransfer = beginTransfer();
    try {
      const projectId = currentProject.current || await ensureProject();
      if (!projectId) { if (alive.current) setNotice('所选文件仍在这里。选择或新建项目后，可继续上传。'); return; }
      const result = await workbenchApi.uploadAssets(projectId, entries, asShared);
      if (alive.current) { setQueue([]); setOmitted(localOmitted); }
      await importResult(projectId, { ...result, omitted: [...localOmitted, ...(result.omitted || [])] });
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { endTransfer(); if (alive.current) setBusy(false); }
  };
  const addFiles = (files) => { const prepared = prepareFiles(files); return upload(prepared.entries, prepared.omitted); };
  const chooseFiles = async () => {
    setMenuOpen(false);
    if (!window.modelDesk?.chooseInputFiles) { input.current?.click(); return; }
    try { const selected = await window.modelDesk.chooseInputFiles(); if (selected) { const prepared = prepareFiles(selected.files.map(nativeFile)); await upload(prepared.entries, [...(selected.omitted || []).map((item) => ({ relative_path: item.path, reason: item.reason })), ...prepared.omitted]); } }
    catch (failure) { setError(failure.message); }
  };
  const previewBrowserFolder = (files) => {
    const prepared = prepareFiles(files);
    setFolderPreview({ entries: prepared.entries, omitted: prepared.omitted }); setFolderSelected(prepared.entries.map((item) => item.relative_path)); setFolderShared(shared);
  };
  const previewFolder = async (folder, projectId = currentProject.current) => {
    const inventory = await workbenchApi.folderFiles(projectId, folder.id);
    if (!alive.current || projectId !== currentProject.current) return;
    setFolderPreview({ folder, inventory, projectId }); setFolderSelected([]); setFolderShared(shared);
  };
  const linkFolder = async () => {
    if (!folderPath.trim() || busy) return;
    setBusy(true); setError(''); const endTransfer = beginTransfer();
    try {
      const projectId = currentProject.current || await ensureProject(); if (!projectId) return;
      const folder = await workbenchApi.addFolder(projectId, { path: folderPath.trim() });
      await previewFolder(folder, projectId); await refreshProjects(); setPathEntry(false); setFolderPath('');
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { endTransfer(); if (alive.current) setBusy(false); }
  };
  const chooseFolder = async () => {
    setMenuOpen(false);
    if (!window.modelDesk?.chooseAttachmentDirectory) { directoryInput.current?.click(); return; }
    try {
      const selected = await window.modelDesk.chooseAttachmentDirectory(); if (!selected?.directory_path) return;
      const projectId = currentProject.current || await ensureProject(); if (!projectId) return;
      const folder = await workbenchApi.addFolder(projectId, { path: selected.directory_path, name: selected.root_name });
      await previewFolder(folder, projectId); await refreshProjects();
    } catch (failure) { setError(failure.message); }
  };
  const importFolder = async () => {
    const selection = folderPreview; if (!selection || !folderSelected.length) return;
    if (selection.entries) { setFolderPreview(null); await upload(selection.entries.filter((item) => folderSelected.includes(item.relative_path)), selection.omitted, folderShared); return; }
    setBusy(true); setError(''); const endTransfer = beginTransfer();
    try { const result = await workbenchApi.importFolder(selection.projectId, selection.folder.id, { files: folderSelected, shared: folderShared }); await importResult(selection.projectId, result); if (alive.current) setFolderPreview(null); }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally { endTransfer(); if (alive.current) setBusy(false); }
  };
  const handlePaste = (event) => {
    if (disabled) return;
    const images = [...(event.clipboardData?.items || [])].filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
    if (images.length) { event.preventDefault(); addFiles(images); }
  };
  const handleDrop = (event) => {
    if (disabled || !event.dataTransfer?.files?.length) return;
    event.preventDefault(); const files = Array.from(event.dataTransfer.files);
    if (files.some((file) => file.webkitRelativePath)) previewBrowserFolder(files); else addFiles(files);
  };
  useImperativeHandle(ref, () => ({ handlePaste, handleDrop, addFiles, previewFolder }));
  const chosen = selectedAssets(assets, attachmentIds, false);
  const sharedAssets = assets.filter((asset) => asset.shared && asset.status === 'ready');
  const issues = attachmentIssues(assets, attachmentIds, includeShared);
  const folderItems = folderPreview?.entries?.map((item) => ({ relative_path: item.relative_path, size: item.file.size, name: item.file.name })) || folderPreview?.inventory?.files || [];
  const folderOmitted = folderPreview?.omitted || folderPreview?.inventory?.omitted || [];
  return <div className={`attachment-composer ${compact ? 'attachment-compact' : ''}`} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={handleDrop}>
    <div className="attachment-add-control"><button type="button" className="attachment-plus" onClick={() => setMenuOpen(!menuOpen)} disabled={disabled || busy} aria-label="添加图片、文件或文件夹" aria-expanded={menuOpen}><Icon name="plus" size={17} /><span>添加资料</span></button>{menuOpen && <div className="attachment-add-menu"><button type="button" onClick={chooseFiles}><Icon name="file" size={16} />图片或文件</button><button type="button" onClick={chooseFolder}><Icon name="folder" size={16} />文件夹（先选择文件）</button><button type="button" onClick={() => { setMenuOpen(false); openManager(); }}><Icon name="library" size={16} />选择项目已有资料</button></div>}<input ref={input} type="file" multiple accept={fileTypes} hidden onChange={(event) => { addFiles(event.target.files); event.target.value = ''; }} /><input ref={directoryInput} type="file" multiple webkitdirectory="" directory="" hidden onChange={(event) => { previewBrowserFolder(event.target.files); event.target.value = ''; }} /></div>
    {!compact && <span className="attachment-short-help">可粘贴图片或拖入文件 · 本轮最多 30 项资料、8 张图片</span>}
    {!window.modelDesk?.chooseAttachmentDirectory && <div className="attachment-local-folder"><button type="button" className="subtle-button" disabled={disabled || busy} onClick={() => setPathEntry(!pathEntry)}>关联本地文件夹</button>{pathEntry && <div><label><span>本地文件夹绝对路径</span><input aria-label="本地文件夹绝对路径" value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder="粘贴 Finder 中复制的文件夹路径" /></label><button type="button" disabled={busy || !folderPath.trim()} onClick={linkFolder}>预览文件夹</button></div>}</div>}
    {chosen.length > 0 && <div className="attachment-tray">{chosen.map((asset) => <span className="attachment-chip" key={asset.id} title={`${asset.relative_path || asset.name} · ${readableSize(asset.size)}`}>{asset.kind === 'image' ? <img src={workbenchApi.assetUrl(asset)} alt={asset.name} /> : <Icon name="file" size={16} />}<span>{asset.name}<small>{readableSize(asset.size)}{asset.truncated && ' · 文本截断'}</small></span><button type="button" disabled={disabled} aria-label={`移除本轮附件 ${asset.name}`} onClick={() => onChange?.(attachmentIds.filter((id) => id !== asset.id))}><Icon name="close" size={12} /></button></span>)}</div>}
    {sharedAssets.length > 0 && <label className="attachment-shared-toggle"><input type="checkbox" checked={includeShared} disabled={disabled} onChange={(event) => onIncludeShared?.(event.target.checked)} />引用项目共用资料（{sharedAssets.length} 项）<span>与本轮附件分别管理</span></label>}
    {queue.length > 0 && <div className="attachment-upload-queue"><span>{busy ? '正在上传并读取实际内容…' : '尚未上传，文件选择已保留'}</span>{queue.map((item, index) => <span key={index} title={item.relative_path}><Icon name="file" size={14} />{item.file.name}</span>)}{!busy && <><button type="button" onClick={() => upload(queue, omitted)}>继续上传</button><button type="button" onClick={() => { setQueue([]); setError(''); }}>移除待上传文件</button></>}</div>}
    {notice && <p className="attachment-notice" role="status">{notice}</p>}{error && <p className="attachment-error" role="alert">{error}</p>}{issues.map((issue) => <p className="attachment-error" role="alert" key={issue}>{issue}</p>)}
    {omitted.length > 0 && <details className="attachment-omitted"><summary>未上传或未加入本轮的资料（{omitted.length}）</summary>{omitted.map((item, index) => <p key={index}><span title={item.relative_path || item.path}>{item.relative_path || item.path}</span><small>{item.reason}</small></p>)}</details>}
    {folderPreview && <div className="project-hub-modal-backdrop"><section className="folder-import-dialog" role="dialog" aria-modal="true" aria-label="选择文件夹中的资料"><header><div><strong>选择要加入的文件</strong><span>{folderPreview.folder?.name || folderItems[0]?.relative_path.split('/')[0] || '文件夹'}</span></div><button type="button" className="icon-button" onClick={() => setFolderPreview(null)} disabled={busy} aria-label="关闭文件夹预览"><Icon name="close" size={18} /></button></header><p>只复制你勾选的文件。敏感文件和缓存目录不会自动加入。</p><div className="folder-selection-toolbar"><button type="button" onClick={() => setFolderSelected(folderItems.slice(0, 100).map((item) => item.relative_path))}>选择前 100 项以内</button><button type="button" onClick={() => setFolderSelected([])}>清空选择</button><span>已选 {folderSelected.length} 项</span></div><div className="folder-inventory-list">{folderItems.map((item) => <label key={item.relative_path}><input type="checkbox" checked={folderSelected.includes(item.relative_path)} disabled={busy || (!folderSelected.includes(item.relative_path) && folderSelected.length >= 100)} onChange={(event) => setFolderSelected(event.target.checked ? [...folderSelected, item.relative_path] : folderSelected.filter((path) => path !== item.relative_path))} /><span title={item.relative_path}>{item.relative_path}</span><small>{readableSize(item.size)}</small></label>)}</div>{folderOmitted.length > 0 && <details className="attachment-omitted"><summary>排除的文件（{folderOmitted.length}）</summary>{folderOmitted.map((item, index) => <p key={index}><span>{item.relative_path}</span><small>{item.reason}</small></p>)}</details>}<label className="attachment-shared-toggle"><input type="checkbox" checked={folderShared} onChange={(event) => setFolderShared(event.target.checked)} />作为项目共用资料，在项目任务中使用</label><footer><button type="button" className="primary-button" disabled={busy || !folderSelected.length} onClick={importFolder}>{busy ? '正在复制与解析' : `导入所选 ${folderSelected.length} 项`}</button></footer></section></div>}
  </div>;
});

export default AttachmentComposer;
