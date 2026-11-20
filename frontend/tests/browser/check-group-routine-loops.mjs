import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { startNavigationFixture } from './navigation-fixture.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fixture = await startNavigationFixture();
const virtualOrigin = 'http://127.0.0.1:8001';
const project = { id: 'loop-project', name: '闭环自动化测试项目', path: '/fixture', managed: true };
const model = { id: 'fixture-llm', label: 'Fixture LLM' };
const room = {
  id: 'room-loop', project_id: project.id, title: '闭环测试群',
  team_snapshot: { members: [{ id: 'reviewer', name: '独立审阅', kind: 'model', model: model.id }] },
};
const reads = {
  '/api/workbench/status': { gateway: { online: true }, providers: [], models: [model], tools: {} },
  '/api/workbench/projects': [project],
  '/api/groups': [room],
  '/api/group-tasks/engines': { engines: [] },
};

let savedRoutine = null;
const errors = [];
let browser;

async function transport(route) {
  const request = route.request();
  const url = new URL(request.url());
  const method = request.method();
  if (url.origin !== virtualOrigin) return route.abort();
  if (!url.pathname.startsWith('/api/')) {
    if (method !== 'GET') return route.abort();
    return route.fulfill({ response: await route.fetch({ url: `${fixture.origin}${url.pathname}${url.search}` }) });
  }
  const reply = (json, status = 200) => route.fulfill({ status, json });
  const parts = url.pathname.split('/');
  const [, , namespace, roomId, resource, id, sub, subId, subAction] = parts;
  if (namespace !== 'groups' || roomId !== room.id || resource !== 'routines') {
    if (method === 'GET') return reply(roomId === room.id ? resource === 'messages' ? { jobs: [] } : room : reads[url.pathname] ?? []);
    return route.abort();
  }
  if (method === 'GET' && !id) {
    return reply({ routines: savedRoutine ? [savedRoutine] : [] });
  }
  if (method === 'POST' && id === 'design') {
    return reply({
      goal_clarity_note: '目标清晰：每两天产出一份带风险闭环的项目简报。',
      restatement: '每隔两天汇总项目看板和群聊进展，输出简报并核对来源。',
      next_question: '当聊天进度与看板状态不一致时，应以哪份为准？',
      question_focus: 'judgment',
      suggestions: ['直接读取看板状态，避免人工重复整理'],
      draft_spec: {
        name: '隔天风险简报',
        goal: '每两天产出一页项目进度与风险简报',
        process: '汇总看板和近期讨论，按风险排序',
        judgment: '',
        checks: '每条风险注明来源与负责人',
        examples: '',
        correlation_field: '事项编号',
        trigger: { kind: 'interval', every_hours: 48 },
        document_formats: ['html'],
        allow_native: false,
        auto_reflect: true,
      },
      readiness: { goal: true, process: true, judgment: false, checks: true, examples: false, trigger: true, score: 4, total: 6 },
    });
  }
  if (method === 'POST' && id === 'walkthrough') {
    return reply({
      overall_assessment: '主流程清楚，但缺少多来源冲突裁决规则。',
      cases: [{
        title: '聊天与看板状态冲突',
        scenario: '群聊里说已完成，看板仍处于受阻列。',
        predicted_steps: '对比两处状态并检查阻塞原因。',
        decision: 'ask_human',
        applied_rule: '当前未约定冲突优先级',
        gap_detected: '缺少冲突信任优先级',
        suggested_judgment_rule: '聊天与看板状态冲突时，一律以看板状态为准并标注差异',
      }],
    });
  }
  if (method === 'POST' && !id) {
    const body = request.postDataJSON();
    savedRoutine = {
      ...body,
      id: 'routine-loop-1',
      room_id: room.id,
      project_id: project.id,
      revision: 1,
      enabled: false,
      readiness: { goal: true, process: true, judgment: true, checks: true, examples: true, trigger: true, score: 6, total: 6 },
      telemetry: {
        total_runs: 1, completed: 1, needs_attention: 0, skipped: 0, active: 0, cancelled: 0,
        success_rate: 1, avg_queue_seconds: 2.0, avg_run_seconds: 38.0,
        feedback_counts: { good: 0, needs_improvement: 0, wrong_judgment: 0 },
        open_proposals: 0, waiting_cases: [], needs_reflection: false,
      },
      open_proposals: [],
      recent_runs: [{
        id: 'run-loop-1',
        client_request_id: 'req-1',
        job_id: null,
        state: 'complete',
        reason: '成果已校验完成',
        created_at: '2026-11-20T09:00:00Z',
        scheduled_at: '2026-11-20T09:00:00Z',
        queue_seconds: 2.0,
        run_seconds: 38.0,
        trigger_kind: 'interval',
        correlation_key: 'ITEM-7',
        case_state: null,
        feedback: [],
      }],
    };
    return reply(savedRoutine, 201);
  }
  if (method === 'POST' && id === 'routine-loop-1' && sub === 'runs' && subAction === 'feedback') {
    const body = request.postDataJSON();
    const fb = { id: 'fb-1', ...body, created_at: '2026-11-20T09:10:00Z' };
    savedRoutine = {
      ...savedRoutine,
      telemetry: {
        ...savedRoutine.telemetry,
        feedback_counts: { good: 0, needs_improvement: 0, wrong_judgment: 1 },
        needs_reflection: true,
      },
      recent_runs: savedRoutine.recent_runs.map((run) => (
        run.id === subId ? { ...run, feedback: [fb, ...(run.feedback || [])] } : run
      )),
    };
    return reply({ feedback: fb, routine: savedRoutine }, 201);
  }
  if (method === 'POST' && id === 'routine-loop-1' && sub === 'reflect') {
    const proposal = {
      id: 'prop-1',
      status: 'open',
      trigger_source: 'manual',
      diagnosis: '根据 ITEM-7 的反馈，交付前需额外核对阻塞原因是否关闭。',
      changes_summary: ['在验收标准补充阻塞项复核'],
      proposed_spec: { ...savedRoutine, checks: `${savedRoutine.checks}；核对阻塞项已关闭` },
      diff: [{ field: 'checks', label: '验收标准', before: savedRoutine.checks, after: `${savedRoutine.checks}；核对阻塞项已关闭` }],
    };
    savedRoutine = {
      ...savedRoutine,
      telemetry: { ...savedRoutine.telemetry, open_proposals: 1, needs_reflection: false },
      open_proposals: [proposal],
    };
    return reply({ changed: true, diagnosis: proposal.diagnosis, proposal, routine: savedRoutine });
  }
  if (method === 'POST' && id === 'routine-loop-1' && sub === 'proposals' && subAction === 'apply') {
    const body = request.postDataJSON();
    assert.equal(body.expected_revision, savedRoutine.revision);
    const [proposal] = savedRoutine.open_proposals;
    savedRoutine = {
      ...savedRoutine,
      checks: proposal.proposed_spec.checks,
      revision: savedRoutine.revision + 1,
      open_proposals: [],
      telemetry: { ...savedRoutine.telemetry, open_proposals: 0 },
    };
    return reply({ routine: savedRoutine, proposal: { ...proposal, status: 'applied', applied_revision: savedRoutine.revision } });
  }
  errors.push(`Unexpected ${method} ${url.pathname}`);
  return route.abort();
}

try {
  const screenshots = process.env.SCREENSHOT_DIR;
  if (screenshots) await mkdir(screenshots, { recursive: true });
  const executablePath = process.env.CHROMIUM_EXECUTABLE;
  browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1370, height: 885 }, timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', transport);
  await page.addInitScript(({ projectId, roomId }) => {
    localStorage.setItem('modeldesk.activeProject', projectId);
    localStorage.setItem('modeldesk.view', 'groups');
    localStorage.setItem(`modeldesk.groups.v1:${projectId}`, JSON.stringify({ roomId }));
  }, { projectId: project.id, roomId: room.id });
  await page.goto(virtualOrigin);

  const dialog = page.getByRole('dialog', { name: '群聊自动化', exact: true });
  const button = (name, scope = dialog) => scope.getByRole('button', { name, exact: true });
  await button('群聊自动化', page).click();

  // 1. Interview -> Walkthrough -> Adopt judgment rule -> Apply to editor
  await button('AI 访谈设计').click();
  await button('每周项目进度简报').click();
  await dialog.getByText('当聊天进度与看板状态不一致时，应以哪份为准？').waitFor();
  await button('案例演练（无副作用）').click();
  await dialog.getByText('聊天与看板状态冲突', { exact: true }).waitFor();
  await button('采纳为判断规则').click();
  await button('应用到例程编辑器').click();

  // 2. Editor has adopted judgment rule, interval trigger, and saves cleanly
  const judgmentValue = await dialog.getByLabel('决策判断与求助边界').inputValue();
  assert.match(judgmentValue, /一律以看板状态为准/);
  assert.equal(await dialog.getByLabel('触发方式').inputValue(), 'interval');
  await button('保存例程').click();
  await dialog.getByText('每 2 天运行').waitFor();
  await dialog.getByText('成功率 100%').waitFor();

  // 3. Record feedback on run -> Reflect -> Review diff -> Apply improvement
  await dialog.getByText('最近运行').click();
  await button('记录反馈').click();
  await dialog.getByLabel('判断有偏差').check();
  await dialog.getByPlaceholder(/具体说明哪里不满意/).fill('阻塞项未关闭就被标记完成');
  await button('提交反馈').click();
  await dialog.getByText('待改进反馈 1').waitFor();

  await button('复盘改进').click();
  await dialog.getByText('待确认改进建议').waitFor();
  await dialog.getByText(/核对阻塞项已关闭/).first().waitFor();
  await button('采纳改进').click();
  await dialog.getByText(/已采纳改进建议/).waitFor();
  assert.equal(savedRoutine.revision, 2);

  // 4. Verify 390px phone viewport layout keeps footer inside dialog
  await page.setViewportSize({ width: 390, height: 844 });
  await button('编辑').click();
  const save = button('保存例程');
  assert.equal(await save.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const modal = element.closest('dialog').getBoundingClientRect();
    return rect.bottom <= modal.bottom && modal.right <= innerWidth && modal.bottom <= innerHeight;
  }), true);

  assert.deepEqual(errors, []);
  console.log('Group routine loops passed AI interview, dry-run walkthrough, rule adoption, interval trigger, feedback, reflection diff, proposal apply, and 390px layout.');
} finally {
  await browser?.close();
  await fixture.close();
}
