#!/usr/bin/env python3
"""ModelDesk fleet node agent (Phase 1: enroll + heartbeat only).

Collects local telemetry with the standard library only and reports it to the
ModelDesk fleet API. No remote command execution, shell, file transfer, or SSH.
Never prints any token in clear text.

Usage:
  python scripts/fleet_agent.py --token <enroll-token> --node-id my-node --name "My Node"
  python scripts/fleet_agent.py            # heartbeat loop using saved credentials
  python scripts/fleet_agent.py --once     # single heartbeat then exit (CI/testing)
"""
import argparse
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENT_VERSION = "0.1.0"
DEFAULT_SERVER = "http://127.0.0.1:8001"
CREDENTIAL_FILE = Path.home() / ".modeldesk/fleet-node.json"
HEARTBEAT_INTERVAL = 15


def _opener():
    # Never route through ambient proxy settings.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_json(url, payload, token=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with _opener().open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")), response.status


def read_proc_stat():
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        fields = handle.readline().split()
    total = sum(int(value) for value in fields[1:])
    idle = int(fields[4]) + (int(fields[5]) if len(fields) > 5 else 0)
    return total, idle


def cpu_percent():
    """Two /proc/stat samples (delta-based); load-based fallback; then None."""
    if os.path.exists("/proc/stat"):
        try:
            total_a, idle_a = read_proc_stat()
            time.sleep(0.5)
            total_b, idle_b = read_proc_stat()
            total_delta, idle_delta = total_b - total_a, idle_b - idle_a
            if total_delta > 0:
                return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
        except (OSError, ValueError, IndexError):
            pass
    try:
        cores = os.cpu_count() or 1
        return round(min(100.0, os.getloadavg()[0] / cores * 100.0), 1)
    except (OSError, AttributeError):
        return None


def mem_percent():
    if os.path.exists("/proc/meminfo"):
        try:
            values = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 2:
                        values[parts[0].rstrip(":")] = int(parts[1])
            total, available = values.get("MemTotal"), values.get("MemAvailable")
            if total and available is not None:
                return round((total - available) / total * 100.0, 1)
        except (OSError, ValueError):
            pass
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            available = os.sysconf("SC_AVPHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return round((pages - available) / pages * 100.0, 1)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def load1():
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def disk_percent(path="/"):
    try:
        usage = shutil.disk_usage(path)
        return round(usage.used / usage.total * 100.0, 1)
    except (OSError, ValueError):
        return None


def uptime_seconds():
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as handle:
                return round(float(handle.read().split()[0]), 1)
        except (OSError, ValueError, IndexError):
            pass
    return None


def collect_telemetry():
    return {
        "cpu_percent": cpu_percent(),
        "mem_percent": mem_percent(),
        "load1": load1(),
        "disk_percent": disk_percent(),
        "uptime_seconds": uptime_seconds(),
        "agent_version": AGENT_VERSION,
        "running_jobs": 0,
        "queued_jobs": 0,
    }


def normalize_platform():
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "darwin":
        return "darwin"
    if system == "windows":
        return "windows"
    return "linux"


def save_credentials(node_id, node_token, server):
    path = CREDENTIAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    payload = {"node_id": node_id, "node_token": node_token, "server": server}
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_credentials(server):
    try:
        payload = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
        node_id, token = payload.get("node_id"), payload.get("node_token")
        if node_id and token:
            return node_id, token, payload.get("server") or server
    except (OSError, ValueError):
        pass
    return None, None, server


def enroll(server, node_id, name, token, labels, agent_version):
    payload = {
        "token": token,
        "node_id": node_id,
        "name": name or node_id,
        "platform": normalize_platform(),
        "hostname": socket.gethostname() or "unknown",
        "labels": labels,
        "agent_version": agent_version,
    }
    response, _ = post_json(server.rstrip("/") + "/api/fleet/enroll", payload)
    node_token = response.get("node_token")
    if not node_token or not response.get("node_id"):
        raise RuntimeError("注册响应缺少节点令牌")
    save_credentials(response["node_id"], node_token, server)
    print(f"registered node {response['node_id']} at {server}; credentials saved (token not shown)")
    return response


def send_heartbeat(server, node_token, telemetry):
    payload = {"telemetry": telemetry}
    response, status = post_json(server.rstrip("/") + "/api/fleet/heartbeat", payload, token=node_token)
    if status != 200:
        raise RuntimeError(f"heartbeat failed with HTTP {status}")
    return response


def heartbeat_once(server, node_token):
    telemetry = collect_telemetry()
    response = send_heartbeat(server, node_token, telemetry)
    fields = {key: value for key, value in telemetry.items() if key != "agent_version"}
    print(f"heartbeat ok: {json.dumps(fields, ensure_ascii=False, default=str)} interval={response.get('heartbeat_interval')}s")
    return response


def parse_arguments():
    parser = argparse.ArgumentParser(description="ModelDesk 节点 agent（仅注册与心跳）")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="ModelDesk 服务地址")
    parser.add_argument("--node-id", help="节点 ID（小写字母、数字、-、_）")
    parser.add_argument("--name", help="节点显示名称")
    parser.add_argument("--token", help="一次性注册令牌（仅在首次注册使用）")
    parser.add_argument("--labels", default="", help="逗号分隔的标签（最多 8 个）")
    parser.add_argument("--once", action="store_true", help="只发送一次心跳后退出")
    parser.add_argument("--interval", type=int, default=HEARTBEAT_INTERVAL, help="心跳间隔秒数")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    server = arguments.server
    labels = [label.strip() for label in arguments.labels.split(",") if label.strip()][:8]

    node_id, node_token, server = load_credentials(server)
    if arguments.node_id and arguments.node_id != node_id and not arguments.token:
        print(f"warning: saved credentials are for {node_id or '(none)'}, not {arguments.node_id}; pass --token to re-register",
              file=sys.stderr)
    if arguments.token:
        if not arguments.node_id:
            print("error: --node-id is required with --token", file=sys.stderr)
            return 2
        try:
            response = enroll(server, arguments.node_id, arguments.name, arguments.token, labels, AGENT_VERSION)
        except (urllib.error.HTTPError, OSError, ValueError, RuntimeError) as error:
            # Never echo the token back, even in failure paths.
            message = str(error)
            if arguments.token in message:
                message = message.replace(arguments.token, "***")
            print(f"enroll failed: {message}", file=sys.stderr)
            return 1
        node_id, node_token = response["node_id"], response["node_token"]

    if not node_token:
        print("error: no saved node token; first run needs --token and --node-id", file=sys.stderr)
        return 2

    if arguments.once:
        try:
            heartbeat_once(server, node_token)
            return 0
        except (urllib.error.HTTPError, OSError, ValueError, RuntimeError) as error:
            print(f"heartbeat failed: {error}", file=sys.stderr)
            return 1

    print(f"heartbeat loop started for {node_id} every {arguments.interval}s (Ctrl-C to stop)")
    while True:
        try:
            heartbeat_once(server, node_token)
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except (urllib.error.HTTPError, OSError, ValueError, RuntimeError) as error:
            print(f"heartbeat error: {error}", file=sys.stderr)
        try:
            time.sleep(arguments.interval)
        except KeyboardInterrupt:
            print("stopped")
            return 0


if __name__ == "__main__":
    sys.exit(main())
