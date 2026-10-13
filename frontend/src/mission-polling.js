import { missionActive } from './mission-state.js';

export function pollMission({ runId, projectId, getRun, onRun, onError, generation, currentGeneration, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let disposed = false, timer;
  const current = () => !disposed && generation === currentGeneration();
  const poll = async () => {
    try {
      const loaded = await getRun(runId);
      if (!current()) return;
      if ((loaded.project_id || null) !== (projectId || null)) {
        onError('这项任务属于另一个项目，请在顶部切换项目。'); return;
      }
      onRun(loaded);
      if (missionActive(loaded) || loaded.status === 'waiting_review') timer = setTimer(poll, 1500);
    } catch (failure) {
      if (current()) { onError(failure.message); timer = setTimer(poll, 5000); }
    }
  };
  poll();
  return () => { disposed = true; clearTimer(timer); };
}
