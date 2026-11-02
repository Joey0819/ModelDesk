"""Bounded reconnection of the Feishu receiver process, not message replay."""
import json
from datetime import datetime, timedelta, timezone

from .execution_store import save_json

WINDOW_SECONDS = 900
MAX_RESTARTS = 3


def reserve(directory, config_revision, *, instant=None):
    current = instant or datetime.now(timezone.utc)
    path = directory / "worker-supervision.json"
    if path.is_symlink():
        raise ValueError("Invalid Feishu supervision record")
    state = json.loads(path.read_text()) if path.exists() else {}
    attempts = [datetime.fromisoformat(value) for value in state.get("attempts", [])] if state.get("config_revision") == config_revision else []
    attempts = [value for value in attempts if (current - value).total_seconds() < WINDOW_SECONDS]
    next_at = None
    if len(attempts) >= MAX_RESTARTS:
        next_at = attempts[0] + timedelta(seconds=WINDOW_SECONDS)
    elif attempts:
        next_at = attempts[-1] + timedelta(seconds=[10,30,120][min(len(attempts)-1,2)])
    if next_at and current < next_at:
        return {"allowed":False,"attempts":len(attempts),"next_at":next_at.isoformat()}
    attempts.append(current)
    save_json(path,{"config_revision":config_revision,"attempts":[value.isoformat() for value in attempts]})
    return {"allowed":True,"attempts":len(attempts),"next_at":None}
