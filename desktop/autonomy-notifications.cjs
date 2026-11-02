const fs = require('node:fs');
const path = require('node:path');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ENDPOINT = 'http://127.0.0.1:8001/api/autonomy/inbox';

function eligible(row) {
  return row && UUID.test(row.id) && (row.project_id == null || UUID.test(row.project_id)) &&
    typeof row.notification_id === 'string' && row.notification_id.length <= 160 && row.notification_id.length > 0 &&
    !row.read && ['complete', 'needs_attention'].includes(row.state);
}

class AutonomyNotifications {
  constructor({ filename, fetchInbox, notify, openTask, now = Date.now }) {
    Object.assign(this, { filename, fetchInbox, notify, openTask, now });
    this.seen = new Set(); this.busy = false; this.stopped = false;
    try { const values = JSON.parse(fs.readFileSync(filename, 'utf8')); if (Array.isArray(values)) this.seen = new Set(values.filter(x => typeof x === 'string').slice(-1000)); } catch { /* First launch or damaged local receipt: inbox stays authoritative. */ }
  }
  async tick() {
    if (this.busy || this.stopped) return;
    this.busy = true;
    try {
      const result = await this.fetchInbox();
      if (this.stopped || !Array.isArray(result?.tasks)) return;
      let count = 0;
      for (const row of result.tasks) {
        if (!eligible(row) || this.seen.has(row.notification_id)) continue;
        // Old unread results remain in the inbox, not a burst of historical alerts.
        const recent = Number.isFinite(Date.parse(row.updated_at)) && this.now() - Date.parse(row.updated_at) < 86400000;
        if (recent && count >= 3) continue;
        this.seen.add(row.notification_id);
        fs.mkdirSync(path.dirname(this.filename), { recursive: true });
        const pending = this.filename + '.tmp';
        fs.writeFileSync(pending, JSON.stringify([...this.seen].slice(-1000)), { mode: 0o600 });
        fs.renameSync(pending, this.filename);
        if (recent) {
          const target = { task_id: row.id, project_id: row.project_id || null };
          this.notify({ title: row.state === 'complete' ? 'ModelDesk · 成果已就绪' : 'ModelDesk · 任务需要你处理',
            body: row.state === 'complete' ? '后台协作已完成，点击查看成果与文件。' : '已保留现有成果，点击查看需要处理的事项。', onClick: () => this.openTask(target) });
          count++;
        }
      }
    } catch { /* Offline service or denied OS notifications must not disrupt execution. */ }
    finally { this.busy = false; }
  }
  start() { this.stopped = false; void this.tick(); this.timer = setInterval(() => void this.tick(), 10000); this.timer.unref?.(); }
  stop() { this.stopped = true; clearInterval(this.timer); }
}

function installAutonomyNotifications({ app, Notification, openTask }) {
  const observer = new AutonomyNotifications({ filename: path.join(app.getPath('userData'), 'autonomy-notifications.json'), openTask,
    fetchInbox: async () => {
      const response = await fetch(ENDPOINT, { headers: { Origin: 'http://localhost:5173' }, redirect: 'error', signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error('inbox unavailable');
      const text = await response.text(); if (text.length > 500000) throw new Error('inbox oversized');
      return JSON.parse(text);
    },
    notify: ({ title, body, onClick }) => {
      if (!Notification.isSupported()) return;
      const notification = new Notification({ title, body });
      notification.on('click', onClick); notification.show();
    },
  });
  observer.start(); app.on('before-quit', () => observer.stop()); return observer;
}
module.exports = { AutonomyNotifications, eligible, installAutonomyNotifications, ENDPOINT };
