import { useState, useEffect, useCallback, useRef } from 'react';
import { todoApi } from '../todo-api';
import { COLUMNS, groupByStatus, computeReorder, filterTasks, countByStatus, validateTask } from '../todo-state';
import './TodoBoard.css';

const PRIORITY_LABELS = { urgent: '紧急', high: '高', medium: '中', low: '低' };

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

function isOverdue(dueDate) {
  if (!dueDate) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueDate + 'T00:00:00');
  return due < today;
}

function TodoCard({ task, onEdit, onDragStart }) {
  return (
    <div
      className="todo-card"
      draggable
      onDragStart={(e) => onDragStart(e, task.id)}
      onClick={() => onEdit(task)}
    >
      <div className={`todo-card-priority-bar priority-${task.priority}`} />
      <div className="todo-card-title">{task.title}</div>
      {task.description && <div className="todo-card-desc">{truncate(task.description, 100)}</div>}
      <div className="todo-card-meta">
        {task.assignee && <span className="todo-card-assignee">👤 {task.assignee}</span>}
        {task.due_date && (
          <span className={`todo-card-due ${isOverdue(task.due_date) && task.status !== 'done' ? 'overdue' : ''}`}>
            📅 {task.due_date}
          </span>
        )}
      </div>
      {task.tags && task.tags.length > 0 && (
        <div className="todo-card-tags">
          {task.tags.map((tag, i) => <span key={i} className="todo-tag">{tag}</span>)}
        </div>
      )}
    </div>
  );
}

function TaskModal({ task, onClose, onSave, onDelete, saving }) {
  const [fields, setFields] = useState(
    task
      ? { ...task }
      : { title: '', description: '', status: 'todo', priority: 'medium', assignee: '', due_date: '', tags: [], project_id: '' },
  );
  const [errors, setErrors] = useState([]);
  const isNew = !task?.id;

  const handleChange = (key, value) => {
    setFields((prev) => ({ ...prev, [key]: value }));
    setErrors([]);
  };

  const handleSubmit = () => {
    const errs = validateTask(fields);
    if (errs.length > 0) {
      setErrors(errs);
      return;
    }
    const payload = { ...fields };
    if (!payload.due_date) payload.due_date = null;
    if (!payload.project_id) payload.project_id = null;
    onSave(payload, isNew);
  };

  return (
    <div className="todo-modal-overlay" onClick={onClose}>
      <div className="todo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{isNew ? '新建任务' : '编辑任务'}</h3>
        <div className="todo-field">
          <label>标题 *</label>
          <input value={fields.title} onChange={(e) => handleChange('title', e.target.value)} placeholder="任务标题" autoFocus />
        </div>
        <div className="todo-field">
          <label>描述</label>
          <textarea value={fields.description} onChange={(e) => handleChange('description', e.target.value)} placeholder="任务详情（可选）" />
        </div>
        <div className="todo-field">
          <label>状态</label>
          <select value={fields.status} onChange={(e) => handleChange('status', e.target.value)}>
            {COLUMNS.map((col) => <option key={col.id} value={col.id}>{col.label}</option>)}
          </select>
        </div>
        <div className="todo-field">
          <label>优先级</label>
          <select value={fields.priority} onChange={(e) => handleChange('priority', e.target.value)}>
            {Object.entries(PRIORITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="todo-field">
          <label>负责人</label>
          <input value={fields.assignee} onChange={(e) => handleChange('assignee', e.target.value)} placeholder="负责人姓名（可选）" />
        </div>
        <div className="todo-field">
          <label>截止日期</label>
          <input type="date" value={fields.due_date || ''} onChange={(e) => handleChange('due_date', e.target.value)} />
        </div>
        <div className="todo-field">
          <label>标签（逗号分隔）</label>
          <input
            value={(fields.tags || []).join(', ')}
            onChange={(e) => handleChange('tags', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
            placeholder="标签1, 标签2"
          />
        </div>
        {errors.length > 0 && (
          <div className="todo-field-error">{errors.join('；')}</div>
        )}
        <div className="todo-modal-actions">
          {!isNew && onDelete && (
            <button className="todo-btn-danger" onClick={onDelete} disabled={saving}>删除</button>
          )}
          <button className="todo-btn-secondary" onClick={onClose} disabled={saving}>取消</button>
          <button className="todo-btn-primary" onClick={handleSubmit} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function TodoBoard() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [showDone, setShowDone] = useState(true);
  const [showArchived, setShowArchived] = useState(false);
  const [editTask, setEditTask] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dragOverColumn, setDragOverColumn] = useState(null);
  const dragIdRef = useRef(null);

  const loadTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await todoApi.list(showArchived);
      setTasks(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [showArchived]);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  const filtered = filterTasks(tasks, { search, priority: priorityFilter, showDone, showArchived });
  const grouped = groupByStatus(filtered);
  const counts = countByStatus(filtered);

  const handleCreate = async (fields) => {
    setSaving(true);
    try {
      await todoApi.create(fields);
      setModalOpen(false);
      setEditTask(null);
      await loadTasks();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async (fields) => {
    setSaving(true);
    try {
      await todoApi.update(editTask.id, fields);
      setModalOpen(false);
      setEditTask(null);
      await loadTasks();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!editTask) return;
    setSaving(true);
    try {
      await todoApi.remove(editTask.id);
      setModalOpen(false);
      setEditTask(null);
      await loadTasks();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = (fields, isNew) => {
    if (isNew) handleCreate(fields);
    else handleUpdate(fields);
  };

  const handleInlineCreate = async (status, title) => {
    if (!title.trim()) return;
    try {
      await todoApi.create({ title: title.trim(), status });
      await loadTasks();
    } catch (err) {
      setError(err.message);
    }
  };

  // Drag & Drop
  const handleDragStart = (e, taskId) => {
    dragIdRef.current = taskId;
    e.dataTransfer.effectAllowed = 'move';
    e.target.classList.add('dragging');
  };

  const handleDragOver = (e, columnId) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverColumn(columnId);
  };

  const handleDragLeave = () => {
    setDragOverColumn(null);
  };

  const handleDrop = async (e, columnId) => {
    e.preventDefault();
    setDragOverColumn(null);
    const draggedId = dragIdRef.current;
    if (!draggedId) return;
    dragIdRef.current = null;

    // Remove dragging class
    document.querySelectorAll('.todo-card.dragging').forEach((el) => el.classList.remove('dragging'));

    // Find drop index within column
    const columnBody = e.currentTarget.querySelector('.todo-column-body');
    const cards = columnBody ? [...columnBody.querySelectorAll('.todo-card')] : [];
    let targetIndex = cards.length;
    for (let i = 0; i < cards.length; i++) {
      const rect = cards[i].getBoundingClientRect();
      if (e.clientY < rect.top + rect.height / 2) {
        targetIndex = i;
        break;
      }
    }

    const { tasks: newTasks, reorderPayload } = computeReorder(tasks, draggedId, columnId, targetIndex);
    if (!reorderPayload) return;

    // Optimistic update
    setTasks(newTasks);
    try {
      await todoApi.reorder(reorderPayload.ids, reorderPayload.status, reorderPayload.position_start);
    } catch (err) {
      setError(err.message);
      await loadTasks();
    }
  };

  const openNewTask = (status = 'todo') => {
    setEditTask({ status, title: '', description: '', priority: 'medium', assignee: '', due_date: '', tags: [] });
    setModalOpen(true);
  };

  const openEditTask = (task) => {
    setEditTask({ ...task });
    setModalOpen(true);
  };

  if (loading) {
    return (
      <main className="workspace todo-board">
        <div className="todo-loading"><div className="todo-spinner" /><span>加载任务看板…</span></div>
      </main>
    );
  }

  if (error && tasks.length === 0) {
    return (
      <main className="workspace todo-board">
        <div className="todo-error">
          <span>{error}</span>
          <button onClick={loadTasks}>重试</button>
        </div>
      </main>
    );
  }

  return (
    <main className="workspace todo-board">
      <div className="todo-toolbar">
        <input type="text" placeholder="搜索任务…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
          <option value="">全部优先级</option>
          {Object.entries(PRIORITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} />显示已完成</label>
        <label><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />显示归档</label>
        <span className="todo-stats">共 {filtered.length} 个任务</span>
      </div>
      {error && <div className="todo-error" style={{ padding: '12px' }}><span>{error}</span><button onClick={loadTasks}>重试</button></div>}
      <div className="todo-columns">
        {COLUMNS.map((col) => (
          <div
            key={col.id}
            className={`todo-column ${dragOverColumn === col.id ? 'drag-over' : ''}`}
            onDragOver={(e) => handleDragOver(e, col.id)}
            onDragLeave={handleDragLeave}
            onDrop={(e) => handleDrop(e, col.id)}
          >
            <div className="todo-column-header">
              <span>{col.label}</span>
              <span className="todo-column-count">{counts[col.id] || 0}</span>
            </div>
            <div className="todo-column-body">
              {(grouped[col.id] || []).length === 0 ? (
                <div className="todo-empty">暂无任务</div>
              ) : (
                (grouped[col.id] || []).map((task) => (
                  <TodoCard key={task.id} task={task} onEdit={openEditTask} onDragStart={handleDragStart} />
                ))
              )}
            </div>
            <InlineAddForm status={col.id} onCreate={handleInlineCreate} />
          </div>
        ))}
      </div>
      {modalOpen && (
        <TaskModal
          task={editTask?.id ? editTask : null}
          onClose={() => { setModalOpen(false); setEditTask(null); }}
          onSave={handleSave}
          onDelete={editTask?.id ? handleDelete : null}
          saving={saving}
        />
      )}
    </main>
  );
}

function InlineAddForm({ status, onCreate }) {
  const [value, setValue] = useState('');
  const inputRef = useRef(null);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (value.trim()) {
      onCreate(status, value);
      setValue('');
    }
  };

  return (
    <form className="todo-add-form" onSubmit={handleSubmit}>
      <input
        ref={inputRef}
        className="todo-add-input"
        placeholder="+ 添加任务，回车确认"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
    </form>
  );
}
