import { useEffect, useState } from 'react';
import { API_BASE, apiFetch } from '../api-base';
import { taskAnswer, taskInputs } from '../mcp-task-state';
import './McpTaskControls.css';

async function taskRequest(id, body) {
  const response = await apiFetch(`${API_BASE}/api/mcp-agent-tasks/${encodeURIComponent(id)}${body ? '/actions' : ''}`, body ? {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
  } : {});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'MCP 任务请求未成功，请刷新查看原任务状态');
  return value;
}

function TaskInput({ entryKey, request, hash, sent, busy, act }) {
  const [values, setValues] = useState({});
  const [error, setError] = useState('');
  const fields = taskInputs(request);
  function answer(event) {
    event.preventDefault(); setError('');
    try { act({action:'accept',request_key:entryKey,request_sha256:hash,content:taskAnswer(fields,values)}); }
    catch (failure) { setError(failure.message); }
  }
  return <form onSubmit={answer} className="mcp-task-input">
    <strong>Agent 请求你确认</strong><p>{typeof request?.params?.message === 'string' ? request.params.message : '外部服务请求输入'}</p>
    <small>仅发送给此任务已配置的 MCP 服务；请核对内容，不要填写密码或 API Key。</small>
    {sent ? <p role="status">{sent.state === 'acknowledged' ? '答复已接收，等待远端更新。' : '答复已发送，结果尚未确认；不会重复发送。'}</p> : fields ? <>
      {fields.map((field) => <label key={field.name}>{field.title || field.name}{field.required ? ' *' : ''}{field.description && <small>{field.description}</small>}
        {field.type === 'boolean' || Array.isArray(field.enum) ? <select required={field.required} disabled={busy} value={values[field.name] ?? ''} onChange={(event) => setValues({...values,[field.name]:event.target.value})}>
          <option value="">请选择</option>{(field.type === 'boolean' ? ['true','false'] : field.enum).map((value) => <option key={String(value)} value={String(value)}>{field.type === 'boolean' ? value === 'true' ? '是' : '否' : String(value)}</option>)}
        </select> : <input required={field.required} disabled={busy} type={field.type === 'string' ? 'text' : 'number'} step={field.type === 'integer' ? 1 : 'any'} min={field.minimum} max={field.maximum} maxLength={field.maxLength || 4000} value={values[field.name] ?? ''} onChange={(event) => setValues({...values,[field.name]:event.target.value})} />}
      </label>)}
      <div className="mcp-task-actions"><button type="submit" disabled={busy}>提交答复并继续</button><button type="button" disabled={busy} onClick={() => act({action:'decline',request_key:entryKey,request_sha256:hash})}>拒绝此请求</button></div>
    </> : <p>此输入类型需要在原服务处理。平台不会自动批准、打开链接或调用其他工具。</p>}
    {error && <p role="alert">{error}</p>}
  </form>;
}

export default function McpTaskControls({ step }) {
  const [receipt, setReceipt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    async function refresh() {
      try { const value = await taskRequest(step.task_id); if (live) setReceipt(value); }
      catch (failure) { if (live) setError(failure.message); }
    }
    refresh(); const timer = setInterval(refresh,5000);
    return () => { live = false; clearInterval(timer); };
  }, [step.task_id]);
  const observation = receipt?.task_observation || step.task_observation;
  const task = observation?.remote_task;
  async function act(body) {
    setBusy(true); setError('');
    try { setReceipt(await taskRequest(step.task_id, body)); }
    catch (failure) { setError(failure.message + '；请查看已保存状态，不要重复发送。'); }
    finally { setBusy(false); }
  }
  if (!task) return error ? <p role="alert">{error}</p> : null;
  const active = ['working','input_required'].includes(task.status);
  return <section className="mcp-task-controls" aria-label="外部 Agent 任务进度">
    <header><strong>{{working:'远端执行中',input_required:'等待你的答复',completed:'远端已结束',failed:'远端失败',cancelled:'远端已取消'}[task.status] || task.status}</strong><small>已派发 {receipt?.dispatch_count ?? 1} 次</small></header>
    {task.statusMessage && <p>{task.statusMessage}</p>}
    <small>原任务 {task.taskId} · {observation.last_observed_at ? `最后查询 ${new Date(observation.last_observed_at).toLocaleTimeString()}` : '等待首次查询'}</small>
    {observation.observation_error && <p>{observation.observation_error}</p>}
    {task.status === 'input_required' && Object.entries(task.inputRequests || {}).map(([key,request]) => <TaskInput key={`${key}-${task.input_request_hashes?.[key]}`} entryKey={key} request={request} hash={task.input_request_hashes?.[key]} sent={observation.input_responses?.[key]} busy={busy} act={act} />)}
    {active && <div className="mcp-task-actions">{observation.cancel_requested ? <p>取消请求已发出，仍需以远端实际状态为准。</p> : <button type="button" disabled={busy} onClick={() => act({action:'cancel_task'})}>请求远端取消</button>}</div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
