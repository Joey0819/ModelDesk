import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchMcpDiscovery, mcpAllowedTools, mcpConfigurationIssue, mcpConfigurationKey, mcpSaveIssues, mcpStringFields } from '../src/mcp-agent-state.js';

const server = { id: 'server', enabled: true, allowed_tools: ['old-tool'] };
const member = { id: 'agent', name: 'Analyst', kind: 'mcp', mcp_server_id: 'server', mcp_tool: 'current-tool', mcp_prompt_field: 'task' };
const schema = { type: 'object', properties: { task: { type: 'string' }, count: { type: 'integer' } }, required: ['task'] };
const discovery = { id: server.id, status: 'ready', tools: [{ name: 'current-tool', allowed: true, inputSchema: schema }, { name: 'old-tool', allowed: false, inputSchema: schema }] };

test('current discovery controls selectable tools and rejects stale revoked permissions', async () => {
  const fetched = await fetchMcpDiscovery({ discoverMcp: async () => discovery }, server.id);
  assert.deepEqual(mcpAllowedTools(fetched, server.id).map((tool) => tool.name), ['current-tool']);
  assert.equal(mcpConfigurationIssue(member, server, fetched), '');
  assert.match(mcpConfigurationIssue({ ...member, mcp_tool: 'old-tool' }, server, fetched), /撤销权限/);
  assert.deepEqual(mcpAllowedTools(fetched, 'other-server'), []);
  const failed = await fetchMcpDiscovery({ discoverMcp: async () => { throw new Error('offline'); } }, server.id);
  assert.equal(failed.status, 'error'); assert.match(mcpConfigurationIssue(member, server, failed), /offline/);
});

test('invalid task parameter and extra mandatory inputs fail before team save', () => {
  assert.deepEqual(mcpStringFields(schema), ['task']);
  assert.match(mcpConfigurationIssue({ ...member, mcp_prompt_field: 'count' }, server, discovery), /字符串任务参数/);
  assert.match(mcpConfigurationIssue({ ...member, mcp_prompt_field: 'prompt' }, server, discovery), /字符串任务参数/);
  for (const required of [['task', 'count'], ['count']]) {
    const complex = { ...discovery, tools: [{ name: member.mcp_tool, allowed: true, inputSchema: { ...schema, required } }] };
    assert.match(mcpConfigurationIssue(member, server, complex), /还要求参数 count/);
  }
  assert.match(mcpConfigurationIssue(member, { ...server, enabled: false }, discovery), /未启用/);
  assert.match(mcpConfigurationIssue(member, server, { id: server.id, status: 'loading' }), /正在检查/);
});

test('save validation is bound to project/server/tool/field and cannot reuse a prior configuration check', () => {
  const checks = { agent: { key: mcpConfigurationKey(member, 'p'), issue: '' } };
  assert.deepEqual(mcpSaveIssues([member], checks, 'p'), []);
  assert.ok(mcpSaveIssues([member], checks, 'other-project').length);
  for (const patch of [{ mcp_server_id: 'other' }, { mcp_tool: 'other' }, { mcp_prompt_field: 'other' }]) assert.ok(mcpSaveIssues([{ ...member, ...patch }], checks, 'p').length);
  assert.ok(mcpSaveIssues([member], {}, 'p').length);
  assert.match(mcpSaveIssues([member], { agent: { ...checks.agent, issue: 'Required parameter missing' } }, 'p')[0], /Required parameter missing/);
  assert.deepEqual(mcpSaveIssues([{ ...member, kind: 'model' }], {}, 'p'), []);
});
