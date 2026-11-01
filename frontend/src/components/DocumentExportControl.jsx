import { useEffect, useId, useRef, useState } from 'react';
import { DOCUMENT_FORMATS, DOCUMENT_LAYOUTS, requestDocumentExport } from '../document-export';
import { Icon } from './WorkbenchResults';
import './DocumentExportControl.css';

export default function DocumentExportControl({ job, artifact }) {
  const dialog = useRef(null);
  const activeRequest = useRef(null);
  const download = useRef(null);
  const [format, setFormat] = useState('docx');
  const [layout, setLayout] = useState('report');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(null);
  const titleId = useId();

  useEffect(() => () => {
    activeRequest.current?.abort();
    if (download.current) URL.revokeObjectURL(download.current);
  }, []);

  const clearDownload = () => {
    if (download.current) URL.revokeObjectURL(download.current);
    download.current = null;
    setReady(null);
  };
  const close = () => {
    activeRequest.current?.abort();
    activeRequest.current = null;
    setBusy(false);
    dialog.current?.close();
  };
  const change = (setter, value) => { setter(value); setError(''); clearDownload(); };
  const generate = async (event) => {
    event.preventDefault();
    if (activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true); setError(''); clearDownload();
    try {
      const file = await requestDocumentExport(job, artifact, format, layout, { signal: controller.signal });
      if (controller.signal.aborted) return;
      // The desktop app permits only its exact backend download routes. Keep
      // native downloads on that route so Electron can show its save dialog.
      const native = typeof globalThis.modelDesk?.chooseInputFiles === 'function';
      if (native && !file.downloadUrl) throw new Error('文档已生成，但服务未返回可用下载地址。请更新服务后重试。');
      const url = native ? file.downloadUrl : URL.createObjectURL(file.blob);
      if (!native) download.current = url;
      setReady({ url, filename: file.filename });
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } catch (failure) {
      if (failure.name !== 'AbortError') setError(failure.message);
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null;
        setBusy(false);
      }
    }
  };

  return <>
    <button type="button" className="document-export-trigger" onClick={() => { setError(''); dialog.current?.showModal(); }} aria-label="导出 Word、PDF 或网页" title="导出 Word、PDF 或网页"><Icon name="export" size={15} /><span>导出</span></button>
    <dialog className="document-export-dialog" ref={dialog} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); close(); }}>
      <form onSubmit={generate}>
        <header><div><h2 id={titleId}>导出成果</h2><p>{artifact.title}</p></div><button type="button" className="artifact-icon-button" aria-label="关闭文档导出" onClick={close}><Icon name="close" size={18} /></button></header>
        <div className="document-export-options">
          <fieldset disabled={busy} className="document-format-options"><legend>文件格式</legend>{DOCUMENT_FORMATS.map((item) => <label key={item.id} className={format === item.id ? 'selected' : ''}><input type="radio" name={`${titleId}-format`} value={item.id} checked={format === item.id} onChange={() => change(setFormat, item.id)} /><span><strong>{item.label}</strong><small>{item.detail}</small></span><span className="document-format-extension">.{item.id}</span></label>)}</fieldset>
          <fieldset disabled={busy} className="document-layout-options"><legend>版式</legend><div>{DOCUMENT_LAYOUTS.map((item) => <label key={item.id} className={layout === item.id ? 'selected' : ''}><input type="radio" name={`${titleId}-layout`} value={item.id} checked={layout === item.id} onChange={() => change(setLayout, item.id)} /><span className={`document-layout-sample document-layout-${item.id}`} aria-hidden="true"><i /><i /><i /><i /><i /></span><strong>{item.label}</strong><small>{item.detail}</small></label>)}</div></fieldset>
          <p className="document-export-note">导出当前选中的成果，内嵌可验证的图片。视频保留在原成果与成果包中；Markdown 仍可单独下载。</p>
          {artifact.status === 'partial' && <p className="artifact-partial-note">这份成果尚未完整完成，导出文件会保留“部分输出”说明。</p>}
          {error && <p role="alert" className="document-export-error">{error}</p>}
          {ready && <p className="document-export-ready" role="status">文件已生成。<a href={ready.url} download={ready.filename}>再次下载 {DOCUMENT_FORMATS.find((item) => item.id === format)?.label}</a></p>}
        </div>
        <footer><span aria-live="polite">{busy ? '正在排版并生成文件…' : '已保存的正文内容保持不变'}</span><button type="button" onClick={close}>{busy ? '取消' : '关闭'}</button><button type="submit" className="primary-button" disabled={busy}>{busy ? '正在生成…' : `导出 ${DOCUMENT_FORMATS.find((item) => item.id === format)?.label}`}</button></footer>
      </form>
    </dialog>
  </>;
}
