import { useState, useEffect, useCallback, useRef } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { todoApi } from '../todo-api';
import TodoGroupStandards from './TodoGroupStandards';
import { Icon } from './WorkbenchResults';
import WorkspaceDialog from './WorkspaceDialog';
import {
  COLUMNS, groupByStatus, computeMove, filterTasks, validateTask, buildTaskPayload,
  canReassignFleet, cardPreview, sourceStatus, taskLocation, todoResultRef,
} from '../todo-state';
import './TodoBoard.css';
import './TodoBoardTheme.css';

const PRIORITY_LABELS = { urgent: '紧急', high: '高', medium: '中', low: '低' };
const SOURCE_LABELS = { feishu: '飞书', workbench: '工作台', group: '协作任务', fleet: 'Fleet 节点', muse: 'Muse' };
const STATUS_LABELS = Object.fromEntries(COLUMNS.map(({ id, label }) => [id, label]));
const REFRESH_INTERVAL_MS = 8000;
const DONE_COLLAPSE_LIMIT = 20;
const ALL_SCOPE = { type: 'all' };
const PERSONAL = '__personal__';

function isOverdue(dueDate) {
  if (!dueDate) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return new Date(dueDate + 'T00:00:00') < today;
}

function roomKey(projectId, roomId) {
  return JSON.stringify([projectId || null, roomId]);
}

function projectName(projects, projectId) {
  if (!projectId) return '个人';
  return projects.find((project) => project.id === projectId)?.name || '未知项目';
}

function availableProjects(projects, projectId) {
  if (!projectId || projects.some((project) => project.id === projectId)) return projects;
  return [...projects, { id: projectId, name: '未知项目' }];
}

function ScopeFilters({ scope, projects, rooms, roomsLoading, onScopeChange }) {
  const { project_id, room_id } = taskLocation(scope);
  const showAll = scope.type === 'all';
  const shownRooms = showAll ? rooms : rooms.filter((room) => (room.project_id || null) === project_id);
  const selectedRoom = room_id ? roomKey(project_id, room_id) : '';
  const hasSelectedRoom = shownRooms.some((room) => roomKey(room.project_id, room.id) === selectedRoom);
  const projectScope = () => project_id ? { type: 'project', projectId: project_id } : { type: 'personal' };

  return (
    <div className="todo-scope-filters">
      <label>
        项目
        <select aria-label="筛选项目" value={showAll ? '' : project_id || PERSONAL} onChange={(event) => {
          const value = event.target.value;
          onScopeChange(value === '' ? ALL_SCOPE : value === PERSONAL ? { type: 'personal' } : { type: 'project', projectId: value });
        }}>
          <option value="">全部项目</option>
          <option value={PERSONAL}>个人（无项目）</option>
          {availableProjects(projects, project_id).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
      </label>
      <label>
        群聊
        <select aria-label="筛选群聊" value={selectedRoom} onChange={(event) => {
          if (!event.target.value) {
            onScopeChange(showAll ? ALL_SCOPE : projectScope());
            return;
          }
          const [projectId, roomId] = JSON.parse(event.target.value);
          onScopeChange({ type: 'room', projectId, roomId });
        }}>
          <option value="">全部群聊</option>
          {selectedRoom && !hasSelectedRoom && <option value={selectedRoom}>{roomsLoading ? '正在读取群聊…' : '未知群聊'}</option>}
          {shownRooms.map((room) => {
            const key = roomKey(room.project_id, room.id);
            return <option key={key} value={key}>{room.title}{showAll ? ` · ${projectName(projects, room.project_id)}` : ''}</option>;
          })}
        </select>
      </label>
    </div>
  );
}

function TodoCard({ task, project, room, busy, onEdit, onDragStart, onDragEnd, onMove, onFollow, onReassign, onOpenResult, onOpenRoom }) {
  const platform = task.external_source?.platform;
  const executionStatus = platform ? sourceStatus(task) : null;
  // A Workbench run dispatched through Feishu retains its original source label.
  const label = platform && (task.source_label || SOURCE_LABELS[platform] || platform);
  const manualPlacement = platform && task.status_mode === 'manual';
  const groupTitle = room?.title || (task.room_id ? '未知群聊' : null);
  const displayedTags = task.tags?.filter((tag) => tag !== label) || [];
  const preview = cardPreview(task);

  return (
    <article
      className={`todo-card${platform ? ' todo-card-synced' : ''}`}
      data-task-id={task.id}
      draggable={!task.archived && !busy}
      onDragStart={(event) => onDragStart(event, task.id)}
      onDragEnd={onDragEnd}
      onClick={() => onEdit(task)}
    >
      <div className={`todo-card-priority-bar priority-${task.priority}`} />
      <div className="todo-card-header-row">
        <button type="button" className="todo-card-title" title={task.title} onClick={(event) => { event.stopPropagation(); onEdit(task); }}>{task.title}</button>
        {!task.archived && <span className="todo-drag-grip" aria-hidden="true" />}
      </div>
      <div className="todo-card-badges">
        <span className="todo-location-trail">
          <span className="todo-location-badge" title={project}>{project}</span>
          {groupTitle && <span className="todo-location-sep" aria-hidden="true">/</span>}
          {groupTitle && (room && onOpenRoom ?
            <button type="button" className="todo-location-badge todo-room-link" aria-label={`打开群聊：${groupTitle}`} title={groupTitle} onClick={(event) => { event.stopPropagation(); onOpenRoom(room); }}>{groupTitle}</button> :
            <span className="todo-location-badge" title={groupTitle}>{groupTitle}</span>)}
        </span>
        {label && <span className="todo-source-badge">{label}</span>}
        {task.archived && <span className="todo-archived-badge">已归档</span>}
      </div>
      {platform && (
        <div className="todo-execution">
          <span className={`todo-execution-state state-${executionStatus}`}>
            <span className="todo-execution-dot" aria-hidden="true" />
            执行 · {STATUS_LABELS[executionStatus] || '未知'}
          </span>
          <span className="todo-execution-sep" aria-hidden="true">·</span>
          <span className={`todo-execution-mode${manualPlacement ? ' is-manual' : ''}`}>{manualPlacement ? '手动放置' : '跟随来源'}</span>
        </div>
      )}
      {preview && <div className="todo-card-desc">{preview}</div>}
      {(task.assignee || task.due_date) && <div className="todo-card-meta">
        {task.assignee && <span className="todo-card-assignee">👤 {task.assignee}</span>}
        {task.due_date && (
          <span className={`todo-card-due ${isOverdue(task.due_date) && task.status !== 'done' ? 'overdue' : ''}`}>
            📅 {task.due_date}
          </span>
        )}
      </div>}
      {displayedTags.length > 0 && (
        <div className="todo-card-tags">
          {displayedTags.map((tag, index) => <span key={index} className="todo-tag">{tag}</span>)}
        </div>
      )}
      <div className="todo-card-actions" onClick={(event) => event.stopPropagation()}>
        {!task.archived && <label className="todo-card-status-label">
          看板
          <select
            className="todo-card-status"
            data-status-control=""
            aria-label={`看板状态：${task.title}`}
            value={task.status}
            aria-disabled={busy}
            onChange={(event) => { if (!busy) onMove(task, event.target.value); }}
          >
            {COLUMNS.map((column) => <option key={column.id} value={column.id}>{column.label}</option>)}
          </select>
        </label>}
        {onOpenResult && todoResultRef(task) && <button type="button" className="todo-card-action" onClick={() => onOpenResult(task)}>
          {executionStatus === 'blocked' ? '查看记录' : '查看成果'}
        </button>}
        {!task.archived && manualPlacement && <button type="button" className="todo-card-action" onClick={() => onFollow(task)} disabled={busy}>恢复跟随</button>}
        {onReassign && canReassignFleet(task) && <button type="button" className="todo-card-action" onClick={() => onReassign(task)} disabled={busy}>重新派发</button>}
      </div>
    </article>
  );
}

function TaskModal({ task, projects, rooms, onClose, onSave, onDelete, saving, error }) {
  const titleRef = useRef(null);
  const [fields, setFields] = useState(task);
  const [tags, setTags] = useState((task.tags || []).join(', '));
  const [errors, setErrors] = useState([]);
  const isNew = !task.id;
  const synced = Boolean(task.external_source);
  const shownRooms = rooms.filter((room) => (room.project_id || null) === (fields.project_id || null));
  const hasSelectedRoom = shownRooms.some((room) => room.id === fields.room_id);

  const handleChange = (key, value) => {
    setFields((prev) => ({ ...prev, [key]: value, ...(key === 'project_id' ? { room_id: null } : {}) }));
    setErrors([]);
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (saving) return;
    const draft = { ...fields, tags: tags.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean) };
    if (draft.tags.length === task.tags?.length && draft.tags.every((tag, index) => task.tags[index] === tag)) draft.tags = task.tags;
    const errs = validateTask(draft);
    if (errs.length > 0) {
      setErrors(errs);
      return;
    }
    onSave(buildTaskPayload(draft, task), isNew);
  };

  return (
    <WorkspaceDialog className="todo-modal" aria-labelledby="todo-modal-title" onDismiss={onClose} focusRef={titleRef} dismissOutside>
      <form onSubmit={handleSubmit}>
        <h3 id="todo-modal-title">{isNew ? '新建任务' : '编辑任务'}</h3>
        <div className="todo-field">
          <label htmlFor="todo-title">标题 *</label>
          <input id="todo-title" ref={titleRef} value={fields.title} onChange={(event) => handleChange('title', event.target.value)} placeholder="任务标题" />
        </div>
        <div className="todo-field">
          <label htmlFor="todo-description">描述</label>
          <textarea id="todo-description" value={fields.description || ''} onChange={(event) => handleChange('description', event.target.value)} placeholder="任务详情（可选）" />
        </div>
        <div className="todo-field-pair">
          <div className="todo-field">
            <label htmlFor="todo-project">项目</label>
            <select id="todo-project" value={fields.project_id || ''} onChange={(event) => handleChange('project_id', event.target.value || null)} disabled={synced}>
              <option value="">个人（无项目）</option>
              {availableProjects(projects, fields.project_id).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </div>
          <div className="todo-field">
            <label htmlFor="todo-room">群聊</label>
            <select id="todo-room" value={fields.room_id || ''} onChange={(event) => handleChange('room_id', event.target.value || null)} disabled={synced}>
              <option value="">无群聊</option>
              {fields.room_id && !hasSelectedRoom && <option value={fields.room_id}>未知群聊</option>}
              {shownRooms.map((room) => <option key={room.id} value={room.id}>{room.title}</option>)}
            </select>
          </div>
        </div>
        <div className="todo-field-pair">
          <div className="todo-field">
            <label htmlFor="todo-status">看板状态</label>
            <select id="todo-status" value={fields.status} onChange={(event) => handleChange('status', event.target.value)} disabled={task.archived}>
              {COLUMNS.map((column) => <option key={column.id} value={column.id}>{column.label}</option>)}
            </select>
            {synced && <small className="todo-field-hint">执行状态 · {STATUS_LABELS[sourceStatus(task)] || '未知'}</small>}
          </div>
          <div className="todo-field">
            <label htmlFor="todo-priority">优先级</label>
            <select id="todo-priority" value={fields.priority} onChange={(event) => handleChange('priority', event.target.value)}>
              {Object.entries(PRIORITY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </div>
        </div>
        <div className="todo-field-pair">
          <div className="todo-field">
            <label htmlFor="todo-assignee">负责人</label>
            <input id="todo-assignee" value={fields.assignee || ''} onChange={(event) => handleChange('assignee', event.target.value)} placeholder="负责人姓名（可选）" />
          </div>
          <div className="todo-field">
            <label htmlFor="todo-due">截止日期</label>
            <input id="todo-due" type="date" value={fields.due_date || ''} onChange={(event) => handleChange('due_date', event.target.value)} />
          </div>
        </div>
        <div className="todo-field">
          <label htmlFor="todo-tags">标签（逗号分隔）</label>
          <input id="todo-tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="标签1, 标签2" />
        </div>
        {(errors.length > 0 || error) && <div className="todo-field-error" role="alert">{errors.join('；') || error}</div>}
        <div className="todo-modal-actions">
          {!isNew && onDelete && <button type="button" className="todo-btn-danger" onClick={onDelete} disabled={saving}>删除</button>}
          <button type="button" className="todo-btn-secondary" onClick={onClose} disabled={saving}>取消</button>
          <button type="submit" className="todo-btn-primary" disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

export default function TodoBoard({
  onOpenResult, onOpenNavigation, onOpenRoom, onOpenProject, projects = [], rooms = [], scope = ALL_SCOPE,
  onScopeChange, roomsLoading = false, roomsError, onRefreshRooms,
}) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [showDone, setShowDone] = useState(true);
  const [showAllDone, setShowAllDone] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [editTask, setEditTask] = useState(null);
  const [modalError, setModalError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dropTarget, setDropTarget] = useState(null);
  const [mobileColumn, setMobileColumn] = useState(null);
  const dragIdRef = useRef(null);
  const listRequest = useRef(null);
  const mutationPending = useRef(false);
  const boardRef = useRef(null);

  const loadTasks = useCallback(async ({ background = false } = {}) => {
    if (background && listRequest.current) return;
    listRequest.current?.abort();
    const request = new AbortController();
    listRequest.current = request;
    try {
      const data = await todoApi.list(showArchived, request.signal);
      if (!request.signal.aborted) {
        setTasks(data);
        setLoadError('');
      }
    } catch (failure) {
      if (!request.signal.aborted && !background) setLoadError(failure.message);
    } finally {
      if (listRequest.current === request) listRequest.current = null;
      if (!request.signal.aborted && !background) setLoading(false);
    }
  }, [showArchived]);

  useEffect(() => {
    loadTasks();
    return () => { listRequest.current?.abort(); };
  }, [loadTasks]);

  useEffect(() => {
    const refresh = () => {
      if (!document.hidden && !dragIdRef.current && !mutationPending.current && !editTask) loadTasks({ background: true });
    };
    const timer = setInterval(refresh, REFRESH_INTERVAL_MS);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [loadTasks, editTask]);

  const filtered = filterTasks(tasks, { search, priority: priorityFilter, showDone, showArchived, scope });
  const grouped = groupByStatus(filtered);
  const shownColumns = COLUMNS.filter((column) => showDone || column.id !== 'done');
  const activeMobileColumn = shownColumns.find((column) => column.id === mobileColumn)?.id ||
    shownColumns.find((column) => grouped[column.id].length)?.id || 'todo';
  const roomsByScope = new Map(rooms.map((room) => [roomKey(room.project_id, room.id), room]));

  const runMutation = async (request, { message, report = setError, refreshOnFailure = false } = {}) => {
    if (mutationPending.current) return false;
    mutationPending.current = true;
    listRequest.current?.abort();
    setError('');
    setNotice('');
    report('');
    setBusy(true);
    try {
      await request();
      await loadTasks();
      if (message) setNotice(message);
      return true;
    } catch (failure) {
      if (refreshOnFailure) await loadTasks();
      report(failure.message);
      return false;
    } finally {
      mutationPending.current = false;
      setBusy(false);
    }
  };

  const openContext = async (action, target) => {
    setError('');
    try { await action(target); } catch (failure) { setError(failure.message); }
  };

  const startEdit = (task) => {
    setModalError('');
    setEditTask(task);
  };

  const closeEdit = () => {
    if (!mutationPending.current) setEditTask(null);
  };

  const handleSave = async (fields, isNew) => {
    const saved = await runMutation(() => isNew ? todoApi.create(fields) : todoApi.update(editTask.id, fields), { report: setModalError });
    if (saved) setEditTask(null);
  };

  const handleDelete = async () => {
    const removed = await runMutation(() => todoApi.remove(editTask.id), { report: setModalError });
    if (removed) setEditTask(null);
  };

  const handleInlineCreate = async (status, title) => {
    const errors = validateTask({ title, status });
    if (errors.length > 0) throw new Error(errors.join('；'));
    let errorMessage;
    const created = await runMutation(() => todoApi.create({ title: title.trim(), status, ...taskLocation(scope) }), {
      report: (message) => { errorMessage = message; },
    });
    if (errorMessage) throw new Error(errorMessage);
    return created;
  };

  const moveCard = async (task, status, index = grouped[status].filter((card) => !card.archived).length, keepFocus = false) => {
    const move = computeMove(task, status, grouped[status], index);
    if (!move) return;
    const moved = await runMutation(() => todoApi.move(move), { message: `${task.title} 已移至${STATUS_LABELS[status]}`, refreshOnFailure: true });
    if (moved && keepFocus) {
      if (showDone || status !== 'done') setMobileColumn(status);
      requestAnimationFrame(() => {
        const control = boardRef.current?.querySelector(`[data-task-id="${CSS.escape(task.id)}"] [data-status-control]`);
        (control || boardRef.current?.querySelector('.todo-scope-filters select'))?.focus();
      });
    }
  };

  const handleDragStart = (event, taskId) => {
    if (mutationPending.current || event.target.closest('select')) {
      event.preventDefault();
      return;
    }
    listRequest.current?.abort();
    dragIdRef.current = taskId;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', taskId);
    event.currentTarget.classList.add('dragging');
  };

  const handleDragEnd = (event) => {
    dragIdRef.current = null;
    setDropTarget(null);
    event.currentTarget.classList.remove('dragging');
  };

  const readDropTarget = (event, status) => {
    const cards = [...event.currentTarget.querySelectorAll('.todo-card[draggable="true"]')]
      .filter((card) => card.dataset.taskId !== dragIdRef.current);
    const index = cards.findIndex((card) => {
      const rect = card.getBoundingClientRect();
      return event.clientY < rect.top + rect.height / 2;
    });
    return { status, index: index < 0 ? cards.length : index, beforeId: cards[index]?.dataset.taskId };
  };

  const handleDragOver = (event, status) => {
    if (!dragIdRef.current || mutationPending.current) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const target = readDropTarget(event, status);
    setDropTarget((previous) => previous?.status === target.status && previous.index === target.index ? previous : target);
  };

  const handleDrop = (event, status) => {
    if (!dragIdRef.current) return;
    event.preventDefault();
    const task = tasks.find((card) => card.id === dragIdRef.current);
    const target = readDropTarget(event, status);
    dragIdRef.current = null;
    setDropTarget(null);
    if (task) moveCard(task, status, target.index);
  };

  const openNewTask = () => startEdit({
    status: 'todo', title: '', description: '', priority: 'medium', assignee: '', due_date: '', tags: [],
    ...taskLocation(scope),
  });
  const selectedRoom = scope.type === 'room' ? roomsByScope.get(roomKey(scope.projectId, scope.roomId)) : null;
  const selectedProject = scope.type === 'project' && projects.find((project) => project.id === scope.projectId);

  return (
    <main className="workspace todo-board" ref={boardRef}>
      <header className="workspace-header">
        <div className="header-title">
          {onOpenNavigation && <button type="button" className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button>}
          <span className="mode-header-icon"><Icon name="dashboard" size={22} /></span>
          <h1>任务看板</h1>
        </div>
      </header>
      <div className="todo-toolbar">
        <div className="todo-toolbar-main">
          <ScopeFilters scope={scope} projects={projects} rooms={rooms} roomsLoading={roomsLoading} onScopeChange={(next) => { setMobileColumn(null); onScopeChange(next); }} />
          {selectedProject && onOpenProject && <button type="button" className="todo-card-action" onClick={() => openContext(onOpenProject, selectedProject.id)}>打开项目</button>}
          {selectedRoom && onOpenRoom && <button type="button" className="todo-card-action" onClick={() => openContext(onOpenRoom, selectedRoom)}>打开群聊</button>}
          {selectedRoom && <TodoGroupStandards key={roomKey(scope.projectId, scope.roomId)} roomId={scope.roomId} projectId={scope.projectId} />}
          <button type="button" className="todo-new-task" onClick={openNewTask} disabled={busy}>+ 新建任务</button>
        </div>
        <div className="todo-toolbar-filters">
          <input type="text" aria-label="搜索任务" placeholder="搜索任务…" value={search} onChange={(event) => { setMobileColumn(null); setSearch(event.target.value); }} />
          <select aria-label="筛选优先级" value={priorityFilter} onChange={(event) => { setMobileColumn(null); setPriorityFilter(event.target.value); }}>
            <option value="">全部优先级</option>
            {Object.entries(PRIORITY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <label><input type="checkbox" checked={showDone} onChange={(event) => setShowDone(event.target.checked)} />显示已完成</label>
          <label><input type="checkbox" checked={showArchived} disabled={busy} onChange={(event) => setShowArchived(event.target.checked)} />显示归档</label>
          <span className="todo-stats">共 {filtered.length} 个任务</span>
        </div>
      </div>
      {roomsLoading && <div className="todo-board-feedback" role="status">正在读取群聊…</div>}
      {roomsError && <div className="todo-board-feedback todo-error" role="alert"><span>{roomsError}</span>{onRefreshRooms && <button type="button" onClick={onRefreshRooms}>重试读取群聊</button>}</div>}
      {loadError && <div className="todo-board-feedback todo-error" role="alert"><span>{loadError}</span><button type="button" onClick={() => loadTasks()}>重试读取任务</button></div>}
      {error && <div className="todo-board-feedback todo-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError('')}>关闭</button></div>}
      <div className="todo-status-announcement" role="status">{notice}</div>
      {!loading && <nav className="todo-mobile-columns" aria-label="切换看板列">
        {shownColumns.map((column) => <button type="button" key={column.id} className="todo-card-action"
          aria-pressed={column.id === activeMobileColumn} onClick={() => setMobileColumn(column.id)}>
          {column.label} <span>{grouped[column.id].length}</span>
        </button>)}
      </nav>}
      {loading ? <div className="todo-loading"><div className="todo-spinner" /><span>加载任务看板…</span></div> : (
        <div className="todo-columns">
          {shownColumns.map((column) => {
            const target = dropTarget?.status === column.id ? dropTarget : null;
            const columnTasks = grouped[column.id];
            const collapseEligible = column.id === 'done' && !search.trim() && columnTasks.length > DONE_COLLAPSE_LIMIT;
            const shouldCollapseDone = collapseEligible && !showAllDone;
            const visibleTasks = shouldCollapseDone ? columnTasks.slice(0, DONE_COLLAPSE_LIMIT) : columnTasks;
            return (
              <section
                key={column.id}
                className={`todo-column${target ? ' drag-over' : ''}${column.id === activeMobileColumn ? ' todo-column-mobile-active' : ''}`}
                aria-labelledby={`todo-column-${column.id}`}
                onDragOver={(event) => handleDragOver(event, column.id)}
                onDragLeave={(event) => {
                  if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
                    setDropTarget((previous) => previous?.status === column.id ? null : previous);
                  }
                }}
                onDrop={(event) => handleDrop(event, column.id)}
              >
                <h2 className="todo-column-header" id={`todo-column-${column.id}`}>
                  <span className={`todo-lane-title lane-${column.id}`}>{column.label}</span><span className="todo-column-count">{columnTasks.length}</span>
                </h2>
                <div className="todo-column-body">
                  {!columnTasks.length && <div className="todo-empty">暂无任务</div>}
                  {visibleTasks.map((task) => (
                    <div className={target?.beforeId === task.id ? 'todo-card-slot drop-before' : 'todo-card-slot'} key={task.id}>
                      <TodoCard task={task} project={projectName(projects, task.project_id)} room={roomsByScope.get(roomKey(task.project_id, task.room_id))}
                        busy={busy} onEdit={startEdit} onDragStart={handleDragStart} onDragEnd={handleDragEnd}
                        onMove={(card, status) => moveCard(card, status, undefined, true)}
                        onFollow={(card) => runMutation(() => todoApi.followSource(card.id), { message: `${card.title} 已恢复跟随来源` })}
                        onReassign={REMOTE_CLIENT ? null : (card) => runMutation(() => todoApi.reassignFleetTask(card.external_source.task_id), { message: `${card.title} 已重新派发` })}
                        onOpenResult={onOpenResult && ((card) => openContext(onOpenResult, card))}
                        onOpenRoom={onOpenRoom && ((room) => openContext(onOpenRoom, room))} />
                    </div>
                  ))}
                  {target && !target.beforeId && <div className="todo-drop-end" aria-hidden="true" />}
                  {shouldCollapseDone && (
                    <button type="button" className="todo-show-more" onClick={() => setShowAllDone(true)}>
                      展开其余 {columnTasks.length - DONE_COLLAPSE_LIMIT} 项已完成
                    </button>
                  )}
                  {collapseEligible && showAllDone && (
                    <button type="button" className="todo-show-more" onClick={() => setShowAllDone(false)}>
                      收起多余已完成项
                    </button>
                  )}
                </div>
                <InlineAddForm status={column.id} onCreate={handleInlineCreate} busy={busy} />
              </section>
            );
          })}
        </div>
      )}
      {editTask && <TaskModal task={editTask} projects={projects} rooms={rooms} onClose={closeEdit} onSave={handleSave}
        onDelete={editTask.id && !editTask.archived && !(REMOTE_CLIENT && editTask.external_source?.platform === 'fleet') ? handleDelete : null}
        saving={busy} error={modalError} />}
    </main>
  );
}

function InlineAddForm({ status, onCreate, busy }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!value.trim() || busy) return;
    setError('');
    try {
      if (await onCreate(status, value)) setValue((current) => current === value ? '' : current);
    } catch (failure) {
      setError(failure.message);
    }
  };

  return (
    <form className="todo-add-form" onSubmit={handleSubmit}>
      <input className="todo-add-input" aria-label={`快速添加到${STATUS_LABELS[status]}`} placeholder="+ 添加任务" value={value}
        onChange={(event) => { setValue(event.target.value); setError(''); }} />
      {value.trim() && <button type="submit" className="todo-card-action" disabled={busy}>添加</button>}
      {error && <div className="todo-field-error" role="alert">{error}</div>}
    </form>
  );
}
