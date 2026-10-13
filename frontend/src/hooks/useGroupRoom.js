import { useCallback, useEffect, useRef, useState } from 'react';
import { groupApi } from '../group-api';
import { workbenchApi } from '../api';
import { applyEvent } from '../workbench-state';
import { groupActive, mergeRoomJobs } from '../group-state';

/** A room owns its history. Engine jobs remain the source of truth for execution. */
export default function useGroupRoom(projectId, roomId) {
  const [room, setRoom] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [before, setBefore] = useState(null);
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [streamError, setStreamError] = useState(false);
  const alive = useRef(true);
  const olderLoaded = useRef(false);
  const activeJob = jobs.find(groupActive);
  const activeId = activeJob?.id;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const refresh = useCallback(async () => {
    try {
      const [loaded, page] = await Promise.all([groupApi.get(roomId, projectId), groupApi.messages(roomId, projectId)]);
      if (!alive.current) return;
      if ((loaded.project_id || null) !== (projectId || null) || loaded.id !== roomId) throw new Error('这个群聊不属于当前项目。');
      setRoom(loaded); setJobs((previous) => mergeRoomJobs(previous, page.jobs, projectId, roomId));
      if (!olderLoaded.current) setBefore(page.next_before || null);
      setError('');
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setLoading(false); }
  }, [roomId, projectId]);
  useEffect(() => { refresh(); const timer = setInterval(refresh, activeId ? 4000 : 12000); return () => clearInterval(timer); }, [refresh, activeId]);
  useEffect(() => {
    if (!activeId) return;
    let disposed = false;
    const source = workbenchApi.events(activeId);
    source.onmessage = (message) => {
      if (disposed) return;
      try {
        const event = JSON.parse(message.data);
        setStreamError(false);
        setJobs((previous) => previous.map((job) => job.id === activeId ? applyEvent(job, event) : job));
        if (['complete', 'error', 'cancelled'].includes(event.type)) { source.close(); refresh(); }
      } catch { setStreamError(true); }
    };
    source.onerror = () => { if (!disposed) setStreamError(true); };
    return () => { disposed = true; source.close(); };
  }, [activeId, refresh]);
  const loadOlder = async () => {
    if (!before || olderLoading) return;
    setOlderLoading(true);
    try {
      const page = await groupApi.messages(roomId, projectId, before);
      if (!alive.current) return;
      olderLoaded.current = true;
      setJobs((previous) => mergeRoomJobs(previous, page.jobs, projectId, roomId)); setBefore(page.next_before || null); setError(''); return true;
    } catch (failure) { if (alive.current) setError(failure.message); return false; }
    finally { if (alive.current) setOlderLoading(false); }
  };
  const send = async (body) => {
    if (busy || activeJob || !room) return null;
    setBusy(true); setError('');
    try {
      const job = await groupApi.send(roomId, projectId, body);
      if (!alive.current) return null;
      setJobs((previous) => mergeRoomJobs(previous, [job], projectId, roomId));
      return job;
    } catch (failure) { if (alive.current) setError(failure.message); return null; }
    finally { if (alive.current) setBusy(false); }
  };
  const cancel = async () => {
    if (!activeId || busy) return;
    setBusy(true);
    try { await workbenchApi.cancelJob(activeId); await refresh(); }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setBusy(false); }
  };
  return { room, jobs, activeJob, loading, olderLoading, busy, error, streamError, before, send, cancel, loadOlder, refresh };
}
