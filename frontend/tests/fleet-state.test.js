import test from 'node:test';
import assert from 'node:assert/strict';
import {
  STATUS_LABELS, PLATFORM_LABELS, nodeStatus, fleetSummary,
  relativeTime, formatPercent, formatLoad, barPercent, topologyPositions,
} from '../src/fleet-state.js';

const NOW = new Date('2026-11-13T12:00:00Z').getTime();

function node(fields = {}) {
  return {
    node_id: 'node-alpha', name: '节点 A', platform: 'linux', hostname: 'host-a',
    labels: [], status: 'online', last_seen_at: new Date(NOW - 8000).toISOString(),
    telemetry: { cpu_percent: 20, mem_percent: 50, load1: 0.5, disk_percent: 60, uptime_seconds: 1000, agent_version: '0.1.0', running_jobs: 0, queued_jobs: 0 },
    ...fields,
  };
}

// --- nodeStatus ---

test('nodeStatus derives online from a fresh heartbeat', () => {
  assert.equal(nodeStatus(node(), NOW), 'online');
});

test('nodeStatus marks offline after 90 seconds without heartbeat', () => {
  assert.equal(nodeStatus(node({ last_seen_at: new Date(NOW - 89_000).toISOString() }), NOW), 'online');
  assert.equal(nodeStatus(node({ last_seen_at: new Date(NOW - 91_000).toISOString() }), NOW), 'offline');
  assert.equal(nodeStatus(node({ last_seen_at: new Date(NOW - 90_001).toISOString() }), NOW), 'offline');
});

test('nodeStatus returns never when there was no heartbeat', () => {
  assert.equal(nodeStatus(node({ last_seen_at: null, status: 'never' }), NOW), 'never');
  assert.equal(nodeStatus(null), 'never');
});

test('nodeStatus tolerates invalid timestamps', () => {
  assert.equal(nodeStatus(node({ last_seen_at: 'not-a-date', status: 'offline' }), NOW), 'offline');
});

// --- fleetSummary ---

test('fleetSummary counts online, offline and never nodes', () => {
  const rows = [
    node({ node_id: 'a', last_seen_at: new Date(NOW - 1000).toISOString() }),
    node({ node_id: 'b', last_seen_at: new Date(NOW - 200_000).toISOString() }),
    node({ node_id: 'c', last_seen_at: null, status: 'never' }),
  ];
  const summary = fleetSummary(rows, NOW);
  assert.equal(summary.total, 3);
  assert.equal(summary.online, 1);
  assert.equal(summary.offline, 1);
  assert.equal(summary.never, 1);
  assert.equal(summary.nodes.length, 3);
});

test('fleetSummary averages cpu and memory for online nodes only', () => {
  const rows = [
    node({ node_id: 'a', telemetry: { cpu_percent: 20, mem_percent: 40 } }),
    node({ node_id: 'b', telemetry: { cpu_percent: 30, mem_percent: 60 } }),
    node({ node_id: 'offline', last_seen_at: new Date(NOW - 200_000).toISOString(), telemetry: { cpu_percent: 99, mem_percent: 99 } }),
    node({ node_id: 'never', last_seen_at: null, status: 'never' }),
  ];
  const summary = fleetSummary(rows, NOW);
  assert.equal(summary.cpu_percent_avg, 25);
  assert.equal(summary.mem_percent_avg, 50);
});

test('fleetSummary returns null averages when no online telemetry', () => {
  const summary = fleetSummary([node({ node_id: 'never', last_seen_at: null, status: 'never' })], NOW);
  assert.equal(summary.cpu_percent_avg, null);
  assert.equal(summary.mem_percent_avg, null);
  assert.deepEqual(fleetSummary([]), { total: 0, online: 0, offline: 0, never: 0, cpu_percent_avg: null, mem_percent_avg: null, nodes: [] });
});

// --- relativeTime ---

test('relativeTime formats seconds, minutes, hours and days', () => {
  const at = (seconds) => new Date(NOW - seconds * 1000).toISOString();
  assert.equal(relativeTime(null), '从未连接');
  assert.equal(relativeTime(undefined), '从未连接');
  assert.equal(relativeTime(at(2), NOW), '刚刚');
  assert.equal(relativeTime(at(5), NOW), '5 秒前');
  assert.equal(relativeTime(at(58), NOW), '58 秒前');
  assert.equal(relativeTime(at(125), NOW), '2 分钟前');
  assert.equal(relativeTime(at(3700), NOW), '1 小时前');
  assert.equal(relativeTime(at(90000), NOW), '1 天前');
});

test('relativeTime handles invalid stamps and future values', () => {
  assert.equal(relativeTime('garbage', NOW), '时间无效');
  assert.equal(relativeTime(new Date(NOW + 5000).toISOString(), NOW), '刚刚');
});

// --- formatting helpers ---

test('formatPercent and formatLoad render null as dash', () => {
  assert.equal(formatPercent(null), '—');
  assert.equal(formatPercent(undefined), '—');
  assert.equal(formatPercent(12.34), '12.3%');
  assert.equal(formatPercent(12), '12%');
  assert.equal(formatLoad(null), '—');
  assert.equal(formatLoad(0.4167), '0.42');
});

test('barPercent clamps values into 0..100', () => {
  assert.equal(barPercent(47.6), 48);
  assert.equal(barPercent(-5), 0);
  assert.equal(barPercent(140), 100);
  assert.equal(barPercent(null), 0);
  assert.equal(barPercent('x'), 0);
});

// --- topology ---

test('topologyPositions distributes nodes evenly around the hub', () => {
  assert.deepEqual(topologyPositions(0), []);
  const one = topologyPositions(1);
  assert.equal(one.length, 1);
  assert.ok(Math.abs(one[0].x - 0.5) < 1e-9);
  assert.ok(Math.abs(one[0].y - (0.5 - 0.38)) < 1e-9);
  const four = topologyPositions(4);
  const angles = four.map(({ x, y }) => Math.atan2(y - 0.5, x - 0.5));
  for (let index = 1; index < 4; index++) {
    assert.ok(angles[index] > angles[index - 1]);
  }
});

test('status and platform label maps cover all server values', () => {
  assert.deepEqual(Object.keys(STATUS_LABELS).sort(), ['never', 'offline', 'online']);
  assert.deepEqual(Object.keys(PLATFORM_LABELS).sort(), ['darwin', 'linux', 'windows']);
});
