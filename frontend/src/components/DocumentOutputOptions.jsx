import { DOCUMENT_FORMATS, DOCUMENT_LAYOUTS } from '../document-export';
import './DocumentExportControl.css';

export default function DocumentOutputOptions({ value = {}, onChange, disabled = false }) {
  const formats = value.documentFormats;
  const mode = formats == null ? 'auto' : formats.length ? 'files' : 'page';
  const update = (change) => onChange({ ...value, ...change });
  const setMode = (next) => update({ documentFormats: next === 'auto' ? null : next === 'page' ? [] : ['docx'] });
  const toggle = (id, checked) => update({ documentFormats: DOCUMENT_FORMATS.filter((item) => item.id === id ? checked : formats?.includes(item.id)).map((item) => item.id) });
  return <section className="document-output-options" aria-label="交付文件设置">
    <div className="document-output-selects">
      <label>交付文件<select aria-label="交付文件" value={mode} disabled={disabled} onChange={(event) => setMode(event.target.value)}><option value="auto">按要求自动</option><option value="page">只在页面显示</option><option value="files">选择文件格式</option></select></label>
      {mode !== 'page' && <label>文档版式<select aria-label="文档版式" value={value.documentLayout || 'auto'} disabled={disabled} onChange={(event) => update({ documentLayout: event.target.value })}><option value="auto">按要求自动</option>{DOCUMENT_LAYOUTS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
    </div>
    {mode === 'files' && <div className="document-output-formats" role="group" aria-label="要生成的文件格式">{DOCUMENT_FORMATS.map((item) => <label key={item.id}><input type="checkbox" checked={formats.includes(item.id)} disabled={disabled || formats.length === 1 && formats.includes(item.id)} onChange={(event) => toggle(item.id, event.target.checked)} />{item.label}<small>.{item.id}</small></label>)}</div>}
    <p>{mode === 'auto' ? '在任务中说明“生成 Word”“交付 PDF”等要求，协作完成后会自动生成文件。' : mode === 'page' ? '成果保留在右侧面板，之后仍可按需导出。' : '所选文件会在协作结束时自动生成，显示在成果面板中。至少选择一种格式。'}</p>
  </section>;
}
