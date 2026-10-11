import { test } from 'node:test';
import assert from 'node:assert/strict';
import { defaultWorkflow, moveTask, orderTasks, workflowErrors } from '../src/workflow-settings.js';

const models = ['gpt-6.1-sol', 'gemini-3-flash'];

test('default CAMEL and CrewAI workflows are immediately valid with verified pair', () => {
  assert.deepEqual(workflowErrors(defaultWorkflow('camel', models), 'camel', models), []);
  assert.deepEqual(workflowErrors(defaultWorkflow('crewai', models), 'crewai', models), []);
});

test('moving a dependent task before its prerequisite removes invalid context references', () => {
  const tasks = defaultWorkflow('crewai', models).tasks;
  const moved = moveTask(tasks, 1, -1);
  assert.equal(moved[0].id, 'review');
  assert.deepEqual(moved[0].context, []);
  assert.deepEqual(tasks[1].context, ['draft']);
});

test('removing a prerequisite cleans dependency references without mutating existing tasks', () => {
  const tasks = defaultWorkflow('crewai', models).tasks;
  assert.deepEqual(orderTasks(tasks.slice(1))[0].context, []);
  assert.deepEqual(tasks[1].context, ['draft']);
});

test('invalid model assignment and hierarchical manager prevent submission', () => {
  const workflow = defaultWorkflow('crewai', models);
  workflow.agents[0].model = 'not-selected';
  workflow.process = 'hierarchical';
  const errors = workflowErrors(workflow, 'crewai', models);
  assert(errors.some((message) => message.includes('参与模型')));
  assert(errors.some((message) => message.includes('管理者')));
});

test('later or circular task context is rejected', () => {
  const workflow = defaultWorkflow('crewai', models);
  workflow.tasks[0].context = ['review'];
  assert(workflowErrors(workflow, 'crewai', models).some((message) => message.includes('此前步骤')));
});

test('conditional flow references are removed when the condition source moves later', () => {
  const workflow = defaultWorkflow('crewai_flow', models);
  workflow.tasks[1].when = { task: 'draft', operator: 'contains', value: '修正' };
  assert.deepEqual(workflowErrors(workflow, 'crewai_flow', models), []);
  const reordered = moveTask(workflow.tasks, 1, -1);
  assert.equal(reordered[0].when, null);
  assert.deepEqual(reordered[0].context, []);
});
