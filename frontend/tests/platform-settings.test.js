import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EMPTY_CONTEXT, contextErrors, jobContext } from '../src/platform-settings.js';

test('a new platform task attaches no knowledge, memory, project, MCP or callable tools implicitly', () => {
  const context = jobContext();
  for (const key of ['knowledge_ids', 'memory_ids', 'mcp_server_ids', 'tools']) assert.deepEqual(context[key], []);
  assert.equal(context.project_id, null);
  assert.equal(context.snapshot_id, null);
  assert.equal(context.remember, false);
});

test('only an explicitly paired project and snapshot may be submitted', () => {
  assert(contextErrors({ project_id: 'project' }).length);
  assert(contextErrors({ snapshot_id: 'snapshot' }).length);
  assert.deepEqual(contextErrors({ project_id: 'project', snapshot_id: 'snapshot' }), []);
});

test('explicit resources and tool scope survive request assembly without modifying defaults', () => {
  const context = jobContext({ knowledge_ids: ['document'], memory_ids: ['memory'], memory_scope: 'research', mcp_server_ids: ['server'], tools: ['statistics'], remember: true });
  assert.deepEqual(context.knowledge_ids, ['document']);
  assert.deepEqual(context.tools, ['statistics']);
  assert.deepEqual(context.mcp_server_ids, ['server']);
  assert.deepEqual(EMPTY_CONTEXT.knowledge_ids, []);
  assert.deepEqual(EMPTY_CONTEXT.tools, []);
});
