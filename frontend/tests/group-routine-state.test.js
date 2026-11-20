import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  appendRuleText,
  routineDraft,
  routineDurationLabel,
  routineReadiness,
  routineSpec,
  routineTriggerLabel,
} from '../src/group-routine-state.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({
  stdin: {
    contents: 'export { default as RoutineDesigner, ReadinessStrip } from "./src/components/RoutineDesigner.jsx";',
    resolveDir: root,
  },
  bundle: true,
  platform: 'node',
  format: 'cjs',
  jsx: 'automatic',
  write: false,
  external: ['react', 'react/jsx-runtime'],
  define: { 'import.meta.env': '{}' },
  loader: { '.css': 'empty' },
});
const filename = path.join(root, 'tests/routine-designer-virtual.cjs');
const virtualModule = new Module(filename);
virtualModule.paths = Module._nodeModulePaths(path.dirname(filename));
virtualModule._compile(compiled.outputFiles[0].text, filename);
const { RoutineDesigner, ReadinessStrip } = virtualModule.exports;

test('routineSpec emits exact trigger fields for interval, board_status, weekly, and board_card', () => {
  const intervalDraft = routineDraft({
    name: '隔天复盘',
    goal: '每两天同步一次风险',
    judgment: '冲突时以看板为准',
    examples: '上次遗漏客户确认',
    correlation_field: '工单号',
    trigger: { kind: 'interval', every_hours: 48 },
    auto_reflect: true,
  });
  const intervalSpec = routineSpec(intervalDraft.specification);
  assert.deepEqual(intervalSpec.trigger, { kind: 'interval', every_hours: 48 });
  assert.equal(intervalSpec.judgment, '冲突时以看板为准');
  assert.equal(intervalSpec.correlation_field, '工单号');
  assert.equal(intervalSpec.auto_reflect, true);
  assert.equal(routineTriggerLabel(intervalSpec), '每 2 天运行');

  const statusSpec = routineSpec(routineDraft({
    name: '待审核核查',
    goal: '核对交付标准',
    trigger: { kind: 'board_status', board_status: 'review', tag: '  ' },
  }).specification);
  assert.deepEqual(statusSpec.trigger, { kind: 'board_status', board_status: 'review' });
  assert.equal(routineTriggerLabel(statusSpec), '看板进入「待审核」');

  const taggedStatusSpec = routineSpec(routineDraft({
    name: '发布核查',
    goal: '核对发布清单',
    trigger: { kind: 'board_status', board_status: 'review', tag: 'release' },
    allow_native: true,
  }).specification);
  assert.deepEqual(taggedStatusSpec.trigger, { kind: 'board_status', board_status: 'review', tag: 'release' });
  assert.equal(routineTriggerLabel(taggedStatusSpec), '本机列变更「待审核」 · release');
});

test('routineReadiness, routineDurationLabel, and appendRuleText stay bounded and deterministic', () => {
  const partial = routineReadiness({ goal: '目标明确', process: '步骤', trigger: { kind: 'manual' } });
  assert.equal(partial.score, 2);
  assert.equal(partial.total, 6);
  assert.equal(partial.judgment, false);
  assert.equal(partial.trigger, false);

  const full = routineReadiness({
    goal: '目标',
    process: '步骤',
    judgment: '判断',
    checks: '验收',
    examples: '案例',
    trigger: { kind: 'interval', every_hours: 6 },
  });
  assert.equal(full.score, 6);
  assert.equal(routineTriggerLabel({ trigger: { kind: 'interval', every_hours: 6 } }), '每 6 小时运行');

  assert.equal(routineDurationLabel(45), '45秒');
  assert.equal(routineDurationLabel(135), '2分15秒');
  assert.equal(routineDurationLabel(3900), '1小时5分');
  assert.equal(routineDurationLabel(null), '');

  const first = appendRuleText('', '冲突时以看板为准');
  assert.equal(first, '冲突时以看板为准');
  assert.equal(appendRuleText(first, '冲突时以看板为准'), '冲突时以看板为准');
  assert.equal(appendRuleText(first, '缺邮箱先问我'), '冲突时以看板为准\n- 缺邮箱先问我');
});

test('RoutineDesigner and ReadinessStrip render starter goals and readiness without side-effect claims', () => {
  const stripHtml = renderToStaticMarkup(React.createElement(ReadinessStrip, {
    specification: { goal: '每周简报', judgment: '以看板为准', trigger: { kind: 'daily' } },
  }));
  assert.match(stripHtml, /就绪度 3\/6/);
  assert.match(stripHtml, /判断与求助/);

  const designerHtml = renderToStaticMarkup(React.createElement(RoutineDesigner, {
    roomId: 'room-1',
    projectId: 'proj-1',
    initialDraft: null,
    onApply() {},
    onBack() {},
  }));
  assert.match(designerHtml, /AI 访谈设计例程/);
  assert.match(designerHtml, /不会执行任何真实操作/);
  assert.match(designerHtml, /每周项目进度简报/);
  assert.match(designerHtml, /应用到例程编辑器/);
});
