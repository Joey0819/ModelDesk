import { useEffect, useRef, useState } from 'react';
import { groupApi } from '../group-api';
import {
  ROUTINE_DECISION_LABELS,
  ROUTINE_STARTER_GOALS,
  appendRuleText,
  routineDraft,
  routineReadiness,
  routineSpec,
  routineTriggerLabel,
} from '../group-routine-state';
import { Icon } from './WorkbenchResults';

const readinessLabels = [
  ['goal', '目标'],
  ['process', '步骤'],
  ['judgment', '判断与求助'],
  ['checks', '验收'],
  ['examples', '案例'],
  ['trigger', '自动触发'],
];

const focusLabels = {
  goal: '对齐最终目标',
  process: '梳理执行步骤',
  judgment: '挖掘隐性判断与求助边界',
  checks: '明确完成验收标准',
  examples: '补充真实案例与反例',
  trigger: '选择触发节奏',
  ready: '方案已基本就绪',
};

export function ReadinessStrip({ specification }) {
  const state = routineReadiness(specification);
  return <div className="group-routine-readiness" aria-label={`自动化就绪度 ${state.score}/${state.total}`}>
    <span className="group-routine-readiness-score">就绪度 {state.score}/{state.total}</span>
    <ul>
      {readinessLabels.map(([key, label]) => <li key={key} data-met={state[key]}>
        <Icon name={state[key] ? 'check' : 'clock'} size={12} />{label}
      </li>)}
    </ul>
  </div>;
}

export default function RoutineDesigner({
  roomId, projectId, initialDraft, initialMode = 'interview', onApply, onBack,
}) {
  const [draft, setDraft] = useState(() => initialDraft || routineDraft(null));
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState('');
  const [latest, setLatest] = useState(null);
  const [walkthrough, setWalkthrough] = useState(null);
  const [scenario, setScenario] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);
  const alive = useRef(true);
  const headingRef = useRef(null);

  const SpeechRecognition = typeof window !== 'undefined'
    ? (window.SpeechRecognition || window.webkitSpeechRecognition || null) : null;

  useEffect(() => {
    alive.current = true;
    headingRef.current?.focus({ preventScroll: true });
    return () => {
      alive.current = false;
      try { recognitionRef.current?.stop(); } catch { /* Ignore speech teardown errors. */ }
    };
  }, []);

  const spec = draft.specification;

  const mergeDraftSpec = (incoming) => {
    if (!incoming || typeof incoming !== 'object') return;
    setDraft((previous) => {
      const next = routineDraft({ ...previous.specification, ...incoming, id: previous.id, revision: previous.revision });
      // Never lose existing editor identity/request state or allow AI to elevate allow_native.
      return {
        ...previous,
        specification: {
          ...next.specification,
          allow_native: Boolean(previous.specification.allow_native),
          auto_reflect: Boolean(previous.specification.auto_reflect),
        },
      };
    });
  };

  const runDesign = async (mode, text) => {
    if (busy) return;
    const clean = String(text ?? message).trim();
    if (mode === 'interview' && !clean && history.length === 0 && !spec.goal.trim()) {
      setError('先说说你想达成的最终目标，或直接口述你平时怎么完成这件事。');
      return;
    }
    setBusy(mode);
    setError('');
    setNotice('');
    try {
      const response = await groupApi.designRoutine(roomId, projectId, {
        mode,
        message: clean,
        history,
        draft: spec.goal.trim() || spec.process.trim() || spec.name.trim() ? routineSpec(spec) : null,
      });
      if (!alive.current) return;
      const nextHistory = [
        ...history,
        ...(clean ? [{ role: 'user', content: clean }] : mode === 'optimize' ? [{ role: 'user', content: '请对照原始目标优化当前流程。' }] : []),
        {
          role: 'assistant',
          content: [response.restatement, response.next_question ? `追问：${response.next_question}` : ''].filter(Boolean).join('\n'),
        },
      ].slice(-16);
      setHistory(nextHistory);
      setLatest(response);
      mergeDraftSpec(response.draft_spec);
      setMessage('');
      setNotice(mode === 'optimize' ? '已对照目标完成流程审视，确认满意后可点击“应用到编辑器”。' : '');
    } catch (failure) {
      if (alive.current) setError(failure.message);
    } finally {
      if (alive.current) setBusy('');
    }
  };

  const runWalkthrough = async (customScenario) => {
    if (busy) return;
    const cleaned = routineSpec(spec);
    if (!cleaned.name || !cleaned.goal) {
      setError('请先让访谈生成例程名称和预期成果，再进行边界案例演练。');
      return;
    }
    setBusy('walkthrough');
    setError('');
    setNotice('');
    try {
      const response = await groupApi.walkthroughRoutine(roomId, projectId, {
        spec: cleaned,
        scenario: String(customScenario ?? scenario).trim(),
      });
      if (!alive.current) return;
      setWalkthrough(response);
      setNotice('已完成无副作用演练。你可以把发现的盲区一键加入“决策判断与求助边界”。');
    } catch (failure) {
      if (alive.current) setError(failure.message);
    } finally {
      if (alive.current) setBusy('');
    }
  };

  useEffect(() => {
    if (initialMode === 'optimize' && spec.goal.trim()) {
      void runDesign('optimize', '');
    } else if (initialMode === 'walkthrough' && spec.name.trim() && spec.goal.trim()) {
      void runWalkthrough('');
    }
    // Intentionally triggers once on initial open for direct optimize/walkthrough shortcuts.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reason=intentional one-shot launch when opened from editor shortcut expires=2027-06-01
  }, []);

  const toggleVoice = () => {
    if (!SpeechRecognition) return;
    if (listening) {
      try { recognitionRef.current?.stop(); } catch { /* Ignore stop error. */ }
      setListening(false);
      return;
    }
    try {
      const recognition = new SpeechRecognition();
      recognition.lang = 'zh-CN';
      recognition.continuous = true;
      recognition.interimResults = false;
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results)
          .slice(event.resultIndex)
          .map((item) => item[0]?.transcript || '')
          .join('');
        if (transcript.trim()) {
          setMessage((previous) => (previous ? `${previous}\n${transcript.trim()}` : transcript.trim()));
        }
      };
      recognition.onerror = () => { if (alive.current) setListening(false); };
      recognition.onend = () => { if (alive.current) setListening(false); };
      recognitionRef.current = recognition;
      recognition.start();
      setListening(true);
    } catch {
      setListening(false);
      setError('当前浏览器暂无法启动语音输入，请直接打字描述。');
    }
  };

  const adoptCaseRule = (item) => {
    const rule = item.suggested_judgment_rule || item.gap_detected;
    if (!rule) return;
    setDraft((previous) => ({
      ...previous,
      specification: {
        ...previous.specification,
        judgment: appendRuleText(previous.specification.judgment, rule, 2000),
        examples: appendRuleText(
          previous.specification.examples,
          `${item.title}：${item.scenario} → ${rule}`,
          2000,
        ),
      },
    }));
    setNotice(`已将「${item.title}」的规则加入判断边界与案例。`);
  };

  return <div className="group-routine-designer">
    <div className="group-routines-body">
      <div className="group-routines-editor-heading">
        <div>
          <h3 tabIndex={-1} ref={headingRef}>AI 访谈设计例程</h3>
          <p className="group-routine-hint">先对齐最终目标，再口述平时做法和隐性判断，最后演练边界案例。设计过程不会执行任何真实操作。</p>
        </div>
      </div>

      <ReadinessStrip specification={spec} />

      {notice && <p className="group-routine-feedback" role="status">{notice}</p>}
      {error && <p className="group-routine-error" role="alert">{error}</p>}

      {history.length === 0 && !spec.goal.trim() && <div className="group-routine-starters">
        <span>不知道怎么开头？选一个常见目标试试：</span>
        <div>
          {ROUTINE_STARTER_GOALS.map((starter) => <button
            key={starter.label}
            type="button"
            className="subtle-button"
            disabled={Boolean(busy)}
            onClick={() => void runDesign('interview', starter.prompt)}
          >{starter.label}</button>)}
        </div>
      </div>}

      {latest && <section className="group-routine-design-reply" aria-label="设计助手对齐结果">
        {latest.goal_clarity_note && <p className="group-routine-clarity"><strong>目标确认：</strong>{latest.goal_clarity_note}</p>}
        <div className="group-routine-restatement">
          <strong>当前理解（先确认，暂不执行）</strong>
          <p>{latest.restatement}</p>
        </div>
        {latest.suggestions?.length > 0 && <div className="group-routine-suggestions">
          <strong>对照目标的优化建议</strong>
          <ul>{latest.suggestions.map((item, idx) => <li key={idx}>{item}</li>)}</ul>
        </div>}
        {latest.next_question && <div className="group-routine-question">
          <span className="group-routine-chip" data-enabled="true">{focusLabels[latest.question_focus] || '追问'}</span>
          <p>{latest.next_question}</p>
        </div>}
      </section>}

      <form className="group-routine-design-input" onSubmit={(event) => { event.preventDefault(); void runDesign('interview', message); }}>
        <label>
          {latest?.next_question ? '回答上面的问题，或继续补充细节' : '说说最终想达成什么目标，以及你平时怎么做的'}
          <textarea
            rows={3}
            value={message}
            maxLength={6000}
            disabled={Boolean(busy)}
            placeholder={latest?.next_question
              ? '直接用大白话回答即可，比如：以看板状态为准，缺客户邮箱就先记录待补并问我…'
              : '不用整理格式，直接写或口述：最终想得到什么、平时看哪些信息、怎么判断对不对、什么时候该问我…'}
            onChange={(event) => setMessage(event.target.value)}
          />
        </label>
        <div className="group-routine-design-bar">
          {SpeechRecognition && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={toggleVoice}>
            <Icon name="dialogue" size={14} />{listening ? '停止语音口述' : '语音口述'}
          </button>}
          {spec.goal.trim() && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => void runDesign('optimize', '')}>
            <Icon name="refresh" size={14} />{busy === 'optimize' ? '正在审视…' : '对照目标优化流程'}
          </button>}
          {spec.name.trim() && spec.goal.trim() && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => void runWalkthrough('')}>
            <Icon name="council" size={14} />{busy === 'walkthrough' ? '正在演练…' : '案例演练（无副作用）'}
          </button>}
          <button type="submit" className="primary-button" disabled={Boolean(busy || !message.trim())}>
            <Icon name="send" size={14} />{busy === 'interview' ? '正在思考…' : latest ? '继续对齐' : '开始访谈'}
          </button>
        </div>
      </form>

      {spec.name.trim() && spec.goal.trim() && <details className="group-routine-walkthrough-panel" open={Boolean(walkthrough)}>
        <summary>边界案例模拟演练（先验证判断逻辑，再开启自动化）</summary>
        <div className="group-routine-walkthrough-form">
          <input
            aria-label="自定义演练场景"
            value={scenario}
            maxLength={2000}
            disabled={Boolean(busy)}
            placeholder="可选：输入一个容易出错的真实场景，例如“邮件和看板截止日期不一致”"
            onChange={(event) => setScenario(event.target.value)}
          />
          <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => void runWalkthrough(scenario)}>
            {busy === 'walkthrough' ? '演练中…' : '开始演练'}
          </button>
        </div>
        {walkthrough && <div className="group-routine-walkthrough-results">
          <p className="group-routine-hint">{walkthrough.overall_assessment}</p>
          {walkthrough.cases.map((item, idx) => <article key={idx} className="group-routine-case-card">
            <header>
              <strong>{item.title}</strong>
              <span className="group-routine-chip" data-enabled={item.decision === 'complete'}>
                {ROUTINE_DECISION_LABELS[item.decision] || item.decision}
              </span>
            </header>
            <p><strong>情境：</strong>{item.scenario}</p>
            <p><strong>预计做法：</strong>{item.predicted_steps}</p>
            {item.applied_rule && <p className="group-routine-hint">依据：{item.applied_rule}</p>}
            {item.gap_detected && <p className="group-routine-gap"><strong>发现盲区：</strong>{item.gap_detected}</p>}
            {item.suggested_judgment_rule && <div className="group-routine-case-adopt">
              <span>建议规则：{item.suggested_judgment_rule}</span>
              <button type="button" className="subtle-button" onClick={() => adoptCaseRule(item)}>采纳为判断规则</button>
            </div>}
          </article>)}
        </div>}
      </details>}

      {spec.goal.trim() && <details className="group-routine-preview">
        <summary>查看当前已整理的例程草案（{spec.name || '未命名'} · {routineTriggerLabel(spec)}）</summary>
        <dl>
          <dt>预期成果</dt><dd>{spec.goal}</dd>
          {spec.process && <><dt>执行步骤</dt><dd>{spec.process}</dd></>}
          {spec.judgment && <><dt>判断与求助边界</dt><dd>{spec.judgment}</dd></>}
          {spec.checks && <><dt>验收标准</dt><dd>{spec.checks}</dd></>}
          {spec.examples && <><dt>案例与反例</dt><dd>{spec.examples}</dd></>}
          {spec.correlation_field && <><dt>关联追踪编号</dt><dd>{spec.correlation_field}</dd></>}
        </dl>
      </details>}
    </div>

    <footer className="group-routine-editor-footer">
      <div>
        <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={onBack}>返回</button>
        <button type="button" className="primary-button" disabled={Boolean(busy || !spec.goal.trim())} onClick={() => onApply(draft)}>
          应用到例程编辑器
        </button>
      </div>
    </footer>
  </div>;
}
