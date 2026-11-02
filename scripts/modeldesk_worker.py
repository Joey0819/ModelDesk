"""Supervise local services independently of the ModelDesk window.

Uses the existing service controller and never kills or replaces a live service.
Recovery is bounded; an unavailable dependency becomes visible, not a spawn loop.
No cloud exposure, login changes, or new native permissions are introduced.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.execution_store import WorkerOwnership, save_checkpoint

STATE_ID = "dc688b53-abdc-4d53-bb3b-c45418b9a620"
DEFAULT_STATE = ROOT / "data/background-worker"
SERVICES = {
    "gateway": "http://127.0.0.1:8317/v1/models",
    "council-api": "http://127.0.0.1:8001/",
    "council-ui": "http://127.0.0.1:5173/",
    "platform-mcp": "http://127.0.0.1:8371/health",
}


def healthy(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def read_status(directory=DEFAULT_STATE):
    try:
        value = json.loads((Path(directory) / (STATE_ID + ".json")).read_text())
        age = time.time() - value["heartbeat_epoch"]
        # A PID alone is not liveness: old PID files and a hung supervisor must
        # never show a green worker. The heartbeat is written after each sweep.
        pid = value.get("pid")
        alive = isinstance(pid, int) and pid > 0
        if alive:
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
        value["online"] = alive and 0 <= age < 45 and value.get("state") != "stopped"
        value["heartbeat_age_seconds"] = max(0, round(age))
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {"online": False, "state": "not_started", "services": {}}


class Supervisor:
    def __init__(self, workspace, directory=DEFAULT_STATE, *, probe=healthy, runner=subprocess.run, clock=time.time):
        self.workspace = Path(workspace).resolve()
        self.directory = Path(directory)
        self.probe, self.runner, self.clock = probe, runner, clock
        self.services = {}
        self.stopping = False
        previous = read_status(directory)
        # Persist restart windows across supervisor restarts; a crashing service
        # does not gain a fresh restart budget by restarting its watchdog.
        for name in SERVICES:
            services = previous.get("services")
            saved = services.get(name) if isinstance(services, dict) else None
            self.services[name] = saved if isinstance(saved, dict) else {}

    def publish(self, state="running"):
        snapshot = {"id": STATE_ID, "pid": os.getpid(), "state": state,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "heartbeat_epoch": self.clock(), "services": self.services,
                    "scope": "awake_local_mac", "max_restarts_per_15_minutes": 3}
        save_checkpoint(self.directory, STATE_ID, snapshot)
        return snapshot

    def tick(self):
        controller = self.workspace / "scripts/services.py"
        for name, url in SERVICES.items():
            if self.stopping:
                break
            current = self.clock()
            record = self.services[name]
            saved_attempts = record.get("restart_times")
            attempts = [stamp for stamp in saved_attempts if isinstance(stamp, (int, float)) and current - 900 < stamp <= current] if isinstance(saved_attempts, list) else []
            if not isinstance(record.get("next_attempt_at"), (int, float)):
                record.pop("next_attempt_at", None)
            record.update(restart_times=attempts, checked_at=datetime.now(timezone.utc).isoformat())
            if self.probe(url):
                record.update(state="ready", reason="服务可访问")
                record.pop("next_attempt_at", None)
                continue
            if current < record.get("next_attempt_at", 0):
                record.update(state="retry_wait", reason="等待重启退避窗口")
                continue
            if len(attempts) >= 3 or not controller.is_file():
                record.update(state="needs_attention", reason="自动恢复次数已用完" if controller.is_file() else "服务控制器不存在")
                continue
            attempts.append(current)
            record.update(state="restarting", next_attempt_at=current + 10 * 2 ** (len(attempts) - 1))
            self.publish()
            try:
                result = self.runner([sys.executable, str(controller), "start", "--service", name],
                                     cwd=self.workspace, capture_output=True, timeout=30)
                ready = result.returncode == 0 and self.probe(url)
                record.update(state="ready" if ready else "retry_wait", reason="已恢复服务" if ready else "启动未成功，将按预算重试")
            except (OSError, subprocess.TimeoutExpired):
                record.update(state="retry_wait", reason="服务启动未确认，将重新检查现有进程")
        return self.publish("degraded" if any(item.get("state") != "ready" for item in self.services.values()) else "running")

    def run(self):
        claim = WorkerOwnership(self.directory)
        claim.acquire()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        try:
            self.publish("starting")
            while not self.stopping:
                self.tick()
                for _ in range(10):
                    if self.stopping:
                        break
                    time.sleep(1)
        finally:
            self.publish("stopped")
            claim.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "run", "status", "stop"])
    parser.add_argument("--workspace", default=str(ROOT.parent / "llm-tools"))
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(read_status(), ensure_ascii=False))
    elif args.action == "run":
        Supervisor(args.workspace).run()
    elif args.action == "stop":
        status = read_status()
        if not status.get("online"):
            print("No live supervisor; services and tasks were not changed")
            return
        pid = status["pid"]
        command = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=True).stdout
        if str(Path(__file__).resolve()) + " run" not in command:
            raise SystemExit("Supervisor identity could not be verified; no process was stopped")
        os.kill(pid, signal.SIGTERM)
        print("Supervisor stopping; existing services and tasks continue")
    else:
        status = read_status()
        if status["online"]:
            print("Background supervisor already running")
            return
        DEFAULT_STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = DEFAULT_STATE / "worker.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run", "--workspace", args.workspace],
                                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
        # Bounded startup acknowledgement; the supervisor owns further recovery.
        for _ in range(30):
            if read_status()["online"]:
                print("Background supervisor started")
                return
            if child.poll() is not None:
                break
            time.sleep(.1)
        raise SystemExit("Background supervisor did not acknowledge startup; inspect its local log")


if __name__ == "__main__":
    main()
