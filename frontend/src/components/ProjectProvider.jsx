import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { ProjectContext } from '../project-context';

const array = (value, key) => Array.isArray(value) ? value : value?.[key] || [];

export default function ProjectProvider({ children }) {
  const [projects, setProjects] = useState([]);
  const [activeProjectId, setActiveProjectId] = useState(() => {
    const saved = localStorage.getItem('modeldesk.activeProject'); if (saved) return saved === 'unassigned' ? null : saved;
    try { return JSON.parse(localStorage.getItem('modeldesk.sessions.v1') || '{}')[localStorage.getItem('modeldesk.mode') || 'chat']?.context?.project_id || null; } catch { return null; }
  });
  const [assets, setAssets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);
  const [transfers, setTransfers] = useState(0);
  const selectionRef = useRef(activeProjectId);
  selectionRef.current = activeProjectId;
  const pendingProject = useRef(null);
  const selectProject = useCallback((id) => {
    const next = id || null; setActiveProjectId(next); setAssets([]);
    localStorage.setItem('modeldesk.activeProject', next || 'unassigned');
    if (pendingProject.current && next) { pendingProject.current(next); pendingProject.current = null; setCreateOpen(false); }
  }, []);
  const refreshProjects = useCallback(async () => {
    try { const list = array(await workbenchApi.listProjects(), 'projects'); setProjects(list); setError(''); }
    catch (failure) { setError(failure.message); }
    finally { setLoading(false); }
  }, []);
  const refreshAssets = useCallback(async (id = selectionRef.current) => {
    if (!id) return [];
    const list = array(await workbenchApi.listAssets(id), 'assets');
    if (id === selectionRef.current) setAssets(list);
    return list;
  }, []);
  useEffect(() => { refreshProjects(); }, [refreshProjects]);
  useEffect(() => {
    if (!activeProjectId) return;
    let disposed = false;
    refreshAssets(activeProjectId).catch((failure) => { if (!disposed) setError(failure.message); });
    return () => { disposed = true; };
  }, [activeProjectId, refreshAssets]);
  const ensureProject = useCallback(() => {
    if (selectionRef.current) return Promise.resolve(selectionRef.current);
    setCreateOpen(true);
    return new Promise((resolve) => { const previous = pendingProject.current; pendingProject.current = (id) => { previous?.(id); resolve(id); }; });
  }, []);
  const closeCreate = useCallback(() => { setCreateOpen(false); pendingProject.current?.(null); pendingProject.current = null; }, []);
  const createProject = useCallback(async (body) => {
    const created = await workbenchApi.addProject(body); await refreshProjects(); selectProject(created.id); setCreateOpen(false); return created;
  }, [refreshProjects, selectProject]);
  const beginTransfer = useCallback(() => { setTransfers((count) => count + 1); let ended = false; return () => { if (!ended) { ended = true; setTransfers((count) => Math.max(0, count - 1)); } }; }, []);
  const value = useMemo(() => ({ projects, activeProjectId, activeProject: projects.find((project) => project.id === activeProjectId), assets, loading, error, createOpen, managerOpen, transfers, selectProject, refreshProjects, refreshAssets, ensureProject, createProject, closeCreate, beginTransfer, openCreate: () => setCreateOpen(true), openManager: () => setManagerOpen(true), closeManager: () => setManagerOpen(false) }), [projects, activeProjectId, assets, loading, error, createOpen, managerOpen, transfers, selectProject, refreshProjects, refreshAssets, ensureProject, createProject, closeCreate, beginTransfer]);
  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}
