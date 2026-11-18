import { useCallback, useEffect, useRef, useState } from 'react';
import { groupApi } from '../group-api';
import { workbenchApi } from '../api';
import { applyEvent } from '../workbench-state';
import { createGroupSubmission, groupActive, groupSubmissionKey, mergeRoomJobs, readGroupSubmission } from '../group-state';

/** A room owns its history. Engine jobs remain the source of truth for execution. */
export default function useGroupRoom(projectId, roomId, initialJob = null) {
  const [room, setRoom] = useState(null);
  const [jobs, setJobs] = useState(() => initialJob ? mergeRoomJobs([], [initialJob], projectId, roomId) : []);
  const [before, setBefore] = useState(null);
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [submissionError, setSubmissionError] = useState('');
  const [submission, setSubmission] = useState(() => readGroupSubmission(localStorage, projectId, roomId));
  const submissionRef = useRef(submission);
  const mutating = useRef(false);
  const [streamError, setStreamError] = useState(false);
  const alive = useRef(true);
  const olderLoaded = useRef(false);
  const activeJob = jobs.find(groupActive);
  const activeId = activeJob?.id;
  const accepted = submission ? jobs.find((job) => job.group_client_request_id === submission.id) : null;
  const rememberSubmission = useCallback((value) => {
    const previousId = submissionRef.current?.id;
    submissionRef.current = value;
    setSubmission(value);
    try {
      const key = groupSubmissionKey(projectId, roomId);
      if (value) localStorage.setItem(key, JSON.stringify(value));
      else if (readGroupSubmission(localStorage, projectId, roomId)?.id === previousId) localStorage.removeItem(key);
    } catch { /* The in-memory receipt can still be retried. */ }
  }, [projectId, roomId]);
  const acknowledge = useCallback((identifier) => {
    if (submissionRef.current?.id === identifier) {
      rememberSubmission(null);
      setSubmissionError('');
    }
  }, [rememberSubmission]);
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
    const continued = (event) => {
      if (event.detail?.roomId === roomId && (event.detail?.projectId || null) === (projectId || null)) refresh();
    };
    globalThis.addEventListener?.('modeldesk-coordination-continued', continued);
    return () => globalThis.removeEventListener?.('modeldesk-coordination-continued', continued);
  }, [refresh, roomId, projectId]);
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
  const send = async (body, harnessMode) => {
    let pending = submissionRef.current;
    if (mutating.current || accepted || !room || (!pending && (activeJob || !body))) return;
    mutating.current = true;
    setBusy(true); setSubmissionError('');
    try {
      if (!pending) {
        pending = createGroupSubmission(body, harnessMode);
        rememberSubmission(pending);
      }
      const job = await groupApi.send(roomId, projectId, { ...pending.body, client_request_id: pending.id });
      if (job.group_client_request_id !== pending.id) throw new Error('群聊服务未返回发送回执');
      if (!alive.current) return;
      setJobs((previous) => mergeRoomJobs(previous, [job], projectId, roomId));
    } catch (failure) {
      if (!alive.current) return;
      if (failure.status >= 400 && failure.status < 500 && ![401, 408, 425].includes(failure.status)) {
        rememberSubmission(null);
        setSubmissionError(failure.message);
      } else {
        setSubmissionError(pending ? '暂时无法确认上次发送。可以重试，群聊会沿用同一次发送回执。' : failure.message);
        if (pending) void refresh();
      }
    } finally {
      mutating.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const cancel = async () => {
    if (!activeId || mutating.current) return;
    mutating.current = true;
    setBusy(true);
    try { await workbenchApi.cancelJob(activeId); await refresh(); }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally {
      mutating.current = false;
      if (alive.current) setBusy(false);
    }
  };
  return { room, jobs, activeJob, loading, olderLoading, busy, error, streamError, before, submission, accepted, submissionError, acknowledge, send, cancel, loadOlder, refresh };
}
