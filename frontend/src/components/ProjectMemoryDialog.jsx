import { useRef } from 'react';
import { LibraryWorkspace } from './PlatformLibraries';
import { useProjects } from '../project-context';

export default function ProjectMemoryDialog() {
  const dialog = useRef(null);
  const { activeProjectId } = useProjects();
  return <>
    <button type="button" className="subtle-button" disabled={!activeProjectId} title={activeProjectId ? '编辑自动带入任务的项目约束和决策' : '先选择项目'} onClick={() => dialog.current?.showModal()}>项目记忆</button>
    <dialog ref={dialog} className="project-memory-dialog" aria-label="项目记忆管理">
      <header><strong>项目记忆</strong><button type="button" onClick={() => dialog.current?.close()}>返回任务</button></header>
      <LibraryWorkspace key={activeProjectId || 'none'} kind="memories" models={[]} />
    </dialog>
  </>;
}
