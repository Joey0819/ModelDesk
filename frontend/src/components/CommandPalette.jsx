import { useId, useLayoutEffect, useRef, useState } from 'react';
import { MODES } from '../workbench-modes';
import { buildCommandItems, filterCommandItems } from '../workbench-sessions';
import { Icon } from './WorkbenchResults';
import WorkspaceDialog from './WorkspaceDialog';

function CommandPaletteDialog({ onClose, ...props }) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const input = useRef(null);
  const activeOption = useRef(null);
  const listId = useId();
  const items = buildCommandItems({ ...props, modes: MODES, includeOlder: Boolean(query.trim()) });
  const filtered = filterCommandItems(items, query);
  const index = Math.max(0, Math.min(activeIndex, filtered.length - 1));
  const selected = filtered[index];
  const optionId = (i) => `${listId}-${i}`;

  useLayoutEffect(() => {
    activeOption.current?.scrollIntoView({ block: 'nearest' });
  }, [selected?.id]);

  const execute = (item) => {
    if (!item) return;
    onClose();
    item.action?.();
  };
  const navigate = (event) => {
    if (event.nativeEvent.isComposing || !selected) return;
    const next = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
    if (next) {
      event.preventDefault();
      setActiveIndex((index + next + filtered.length) % filtered.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      execute(selected);
    }
  };

  return <WorkspaceDialog className="command-palette-modal" aria-label="全局快捷命令面板"
    onDismiss={onClose} focusRef={input} dismissOutside>
    <header className="command-palette-header">
      <Icon name="search" size={19} />
      <input ref={input} role="combobox" aria-autocomplete="list" aria-expanded="true"
        aria-controls={listId} aria-activedescendant={selected ? optionId(index) : undefined}
        aria-label="搜索命令、项目或会话" placeholder="搜索命令、项目或会话"
        value={query} onKeyDown={navigate}
        onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} />
      <button type="button" className="icon-button" aria-label="关闭快捷命令" onClick={onClose}><Icon name="close" size={17} /></button>
    </header>
    <div className="command-palette-list" id={listId} role="listbox" aria-label="可执行命令列表">
      {filtered.map((item, i) => <div key={item.id} role="presentation">
        {item.category !== filtered[i - 1]?.category && <div className="command-palette-category" aria-hidden="true">{item.category}</div>}
        <button type="button" role="option" tabIndex={-1} id={optionId(i)}
          ref={i === index ? activeOption : null} aria-selected={i === index}
          className={`command-palette-item ${i === index ? 'selected' : ''}`}
          onClick={() => execute(item)}>
          <span className="command-palette-icon"><Icon name={item.icon || 'chat'} size={16} /></span>
          <span className="command-palette-text"><strong>{item.title}</strong>{item.subtitle && <small>{item.subtitle}</small>}</span>
          {item.shortcut && <kbd className="command-palette-shortcut">{item.shortcut}</kbd>}
        </button>
      </div>)}
    </div>
    {!filtered.length && <p role="status" className="command-palette-empty">没有找到匹配项。可以输入项目名、会话标题或命令。</p>}
    <footer className="command-palette-footer"><span><kbd>↑</kbd><kbd>↓</kbd>选择</span><span><kbd>Enter</kbd>打开</span><span><kbd>Esc</kbd>关闭</span></footer>
  </WorkspaceDialog>;
}

export default function CommandPalette({ open, ...props }) {
  return open ? <CommandPaletteDialog {...props} /> : null;
}
