import { useCallback, useEffect, useRef, useState } from 'react';
import { groupTaskApi } from '../group-task-api';
import { museApi } from '../muse-api';
import { createGroupDispatch, groupDispatchKey, groupDispatchReceiptMatches, readGroupDispatch } from '../group-state';

export default function useGroupDispatch({ projectId, roomId, channel, agentTasks, museTasks, onRefresh }) {
  const [pending, setPending] = useState(() => readGroupDispatch(localStorage, projectId, roomId, channel));
  const [receipt, setReceipt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pendingRef = useRef(pending);
  const sending = useRef(false);
  const alive = useRef(true);
  const tasks = pending?.target === 'muse' ? museTasks : agentTasks;
  const warning = typeof pending?.receiptWarning === 'string' ? pending.receiptWarning : '';
  const accepted = groupDispatchReceiptMatches(pending, receipt) ? receipt : !warning && tasks?.find((task) => groupDispatchReceiptMatches(pending, task));

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const remember = useCallback((next) => {
    const previous = pendingRef.current;
    pendingRef.current = next;
    setPending(next);
    try {
      const key = groupDispatchKey(projectId, roomId, channel);
      if (next) localStorage.setItem(key, JSON.stringify(next));
      else if (readGroupDispatch(localStorage, projectId, roomId, channel)?.id === previous?.id) localStorage.removeItem(key);
    } catch { /* The current window can still retry the same attempt. */ }
  }, [projectId, roomId, channel]);

  const acknowledge = useCallback((id) => {
    if (pendingRef.current?.id !== id) return;
    remember(null);
    setError('');
  }, [remember]);

  const send = async (request) => {
    if (sending.current || accepted) return;
    const saved = pendingRef.current || readGroupDispatch(localStorage, projectId, roomId, channel);
    if (saved && request) {
      if (pendingRef.current !== saved) remember(saved);
      return;
    }
    if (!saved && !request) return;
    const attempt = saved || createGroupDispatch(request);
    sending.current = true;
    setBusy(true);
    setError('');
    if (pendingRef.current !== attempt) remember(attempt);
    try {
      const api = attempt.target === 'muse' ? museApi : groupTaskApi;
      const task = await api.create({ ...attempt.body, client_request_id: attempt.id });
      if (!groupDispatchReceiptMatches(attempt, task)) throw new Error('服务未返回本次派发的确认。');
      if (alive.current) setReceipt(task);
    } catch (failure) {
      if (!alive.current || pendingRef.current?.id !== attempt.id) return;
      if (failure.status === 409 && failure.code === 'dispatch_receipt_unverified') {
        remember({ ...attempt, receiptWarning: failure.message });
      } else if (failure.status >= 400 && failure.status < 500 && ![401, 408, 425, 429].includes(failure.status)) {
        acknowledge(attempt.id);
        setError(failure.message);
      } else {
        setError('暂时无法确认上次派发。重试会沿用原任务和同一次派发记录。');
      }
    } finally {
      sending.current = false;
      if (alive.current) {
        setBusy(false);
        void onRefresh();
      }
    }
  };

  return { pending, accepted, busy, error, warning, send, acknowledge };
}
