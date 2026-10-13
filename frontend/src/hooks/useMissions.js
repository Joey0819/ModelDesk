import { useCallback, useEffect, useRef, useState } from 'react';
import { harnessApi } from '../harness-api';
import { scopedMissions } from '../mission-state';
import { pollMission } from '../mission-polling';

/** Keep parent-run polling separate from engine chat streams and project drafts. */
export default function useMissions(projectId, initialId) {
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState(initialId);
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const request = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current += 1; }; }, []);
  const refresh = useCallback(async () => {
    try {
      const result = await harnessApi.list(projectId);
      if (alive.current) setRuns(scopedMissions(Array.isArray(result) ? result : result.runs, projectId));
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setLoading(false); }
  }, [projectId]);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 10000); return () => clearInterval(timer); }, [refresh]);
  useEffect(() => {
    if (!runId) return;
    return pollMission({ runId, projectId, getRun: harnessApi.get, generation: request.current, currentGeneration: () => request.current,
      onRun: (loaded) => {
        setRun(loaded); setError('');
        setRuns((previous) => scopedMissions([loaded, ...previous.filter((item) => item.id !== loaded.id)], projectId));
      }, onError: setError,
    });
  }, [runId, projectId, revision]);
  const select = (id) => { request.current += 1; setRunId(id); setRun(null); setError(''); };
  const mutate = async (action) => {
    if (busy) return null;
    const generation = ++request.current;
    setBusy(true); setError('');
    try {
      const next = await action();
      if (!alive.current || generation !== request.current) return null;
      setRun(next); setRunId(next.id);
      setRevision((previous) => previous + 1);
      setRuns((previous) => scopedMissions([next, ...previous.filter((item) => item.id !== next.id)], projectId));
      return next;
    } catch (failure) { if (alive.current && generation === request.current) { setError(failure.message); setRevision((previous) => previous + 1); } return null; }
    finally { if (alive.current) setBusy(false); }
  };
  return { runs, runId, run, loading, busy, error, select, refresh,
    create: (body) => mutate(() => harnessApi.create(body)),
    cancel: () => mutate(() => harnessApi.cancel(runId)),
    resume: () => mutate(() => harnessApi.resume(runId)),
    approve: () => mutate(() => harnessApi.approve(runId)),
  };
}
