import { createContext, useContext } from 'react';

export const ProjectContext = createContext(null);
export function useProjects() {
  const value = useContext(ProjectContext);
  if (!value) throw new Error('共享项目入口尚未初始化。');
  return value;
}

export const projectScopeKey = (projectId) => projectId || 'unassigned';
export const projectSessionKey = (projectId, mode) => `${projectScopeKey(projectId)}::${mode}`;
export const projectDraftKey = (projectId, conversationId = 'new') => `${projectScopeKey(projectId)}::${conversationId}`;

export function scopedResources(items, projectId, section = 'all') {
  return (Array.isArray(items) ? items : []).filter((item) => section === 'global' ? !item.project_id : section === 'project' ? Boolean(projectId) && item.project_id === projectId : !item.project_id || item.project_id === projectId);
}
