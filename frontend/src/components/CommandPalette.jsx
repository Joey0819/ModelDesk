import { useEffect, useMemo, useRef, useState } from 'react';
import { MODES } from '../workbench-modes';
import { buildCommandItems, filterCommandItems } from '../workbench-sessions';
import { Icon } from './WorkbenchResults';

function CommandPaletteDialog({ onClose, ...props }) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef(null);

  const allItems = useMemo(() => buildCommandItems({ ...props, modes: MODES }), [props]);
  const filtered = useMemo(() => filterCommandItems(allItems, query), [allItems, query]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);

  const safeIndex = filtered.length ? Math.min(activeIndex, filtered.length - 1) : 0;

  const execute = (item) => {
    if (!item) return;
    onClose?.();
    item.action?.();
  };

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose?.();
      return;
    }
    if (!filtered.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % filtered.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + filtered.length) % filtered.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      execute(filtered[safeIndex]);
    }
  };

  return (
    <div className="command-palette-backdrop" role="presentation" onClick={onClose}>
      <section
        className="command-palette-modal"
        role="dialog"
        aria-modal="true"
        aria-label="全局快捷命令面板"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <header className="command-palette-header">
          <Icon name="search" size={17} />
          <input
            ref={inputRef}
            type="text"
            aria-label="搜索命令、群聊、工作区或历史会话"
            placeholder="输入命令、群聊、协作模式或历史记录（↑↓ 选择，Enter 跳转）…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
          />
          <kbd className="command-palette-kbd">ESC</kbd>
        </header>
        <div className="command-palette-list" role="listbox" aria-label="可执行命令列表">
          {filtered.length ? (
            filtered.map((item, index) => {
              const selected = index === safeIndex;
              const prevCategory = index > 0 ? filtered[index - 1].category : null;
              return (
                <div key={item.id}>
                  {item.category !== prevCategory && (
                    <div className="command-palette-category">{item.category}</div>
                  )}
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`command-palette-item ${selected ? 'selected' : ''}`}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => execute(item)}
                  >
                    <span className="command-palette-icon">
                      <Icon name={item.icon || 'chat'} size={16} />
                    </span>
                    <span className="command-palette-text">
                      <strong>{item.title}</strong>
                      {item.subtitle && <small>{item.subtitle}</small>}
                    </span>
                    {item.shortcut && <kbd className="command-palette-shortcut">{item.shortcut}</kbd>}
                  </button>
                </div>
              );
            })
          ) : (
            <p className="command-palette-empty">没有找到匹配的命令或会话，试试搜索“群聊”、“Muse”或“深色”。</p>
          )}
        </div>
        <footer className="command-palette-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> 移动</span>
          <span><kbd>Enter</kbd> 执行</span>
          <span><kbd>⌘B</kbd> 折叠侧栏</span>
          <span><kbd>⌘.</kbd> 成果预览栏</span>
        </footer>
      </section>
    </div>
  );
}

export default function CommandPalette({ open, ...props }) {
  if (!open) return null;
  return <CommandPaletteDialog {...props} />;
}
