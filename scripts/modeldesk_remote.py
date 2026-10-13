"""Manage only ModelDesk's private Tailscale Serve listener, never Funnel.

The main API owns the gateway process. This CLI calls its loopback management
endpoints; it does not expose port 8001 or bind to a LAN address.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "stop", "revoke-all"])
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False, timeout=45) as client:
        try:
            response = client.get("/api/remote/status") if args.action == "status" else client.post(
                "/api/remote/" + args.action, headers={"Origin": "http://localhost:5173"}, json={})
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    print(exc.response.json().get("detail", "手机访问操作失败"), file=sys.stderr)
                except ValueError:
                    print("手机访问操作失败", file=sys.stderr)
            else:
                print("无法连接本机 ModelDesk API；请先打开 ModelDesk。", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
