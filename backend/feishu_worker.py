"""Isolated official SDK long connection. No incoming HTTP port is opened."""
import json
import logging
import os
import sys
import threading
import time

from .feishu_store import FeishuStore


def emit(state):
    # A fixed vocabulary is the only stdout protocol. Never emit SDK logs.
    print(json.dumps({"state": state}), flush=True)


def normalize_event(data, app_id, bot_open_id):
    event = data.event
    message, sender = event.message, event.sender
    header_app = getattr(getattr(data, "header", None), "app_id", None)
    if header_app and header_app != app_id:
        return None
    content = json.loads(message.content) if message.message_type == "text" else {}
    text = content.get("text", "")
    # Feishu's group mentions use placeholders; remove only actual mention
    # keys supplied by this authenticated event, rather than arbitrary text.
    bot_mentioned = False
    for mention in message.mentions or []:
        key = getattr(mention, "key", None)
        if key and isinstance(text, str):
            is_bot = getattr(getattr(mention, "id", None), "open_id", None) == bot_open_id
            bot_mentioned = bot_mentioned or is_bot
            if is_bot and text.lstrip().startswith(key):
                text = text.lstrip()[len(key):].lstrip()
            else:
                text = text.replace(key, "@" + (getattr(mention, "name", None) or "成员"))
    if message.chat_type == "group" and not bot_mentioned:
        return None
    return {"app_id": app_id, "message_id": message.message_id, "chat_id": message.chat_id,
            "create_time": message.create_time,
            "chat_type": message.chat_type, "message_type": message.message_type, "text": text,
            "sender_type": sender.sender_type, "sender_id": sender.sender_id.open_id}


class SanitizedConnectionLog(logging.Handler):
    """The pinned SDK reports initial connection only through its logger.

    Inspect the known lifecycle text but discard signed URLs and all raw errors.
    Reconnection hooks supplement these reports; task delivery is separate.
    """
    def emit(self, record):
        text = record.getMessage()
        if "disconnected to " in text or "trying to reconnect" in text or "receive message loop exit" in text:
            emit("connecting")
        elif "connected to " in text:
            emit("connected")
        elif "connect failed" in text:
            emit("error")


def main():
    parent_pid = int(sys.argv[2])
    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(2)
        # The blocking SDK has no public stop API. An orphan must not keep a
        # hidden subscription alive after the owning ModelDesk service exits.
        os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()
    import lark_oapi as lark
    store = FeishuStore(sys.argv[1])
    config = store.config()
    if not config["enabled"] or not config["app_id"] or not config["app_secret"]:
        return
    from .feishu_sdk import FeishuSDK
    try:
        bot_open_id = FeishuSDK(config).bot_info()["open_id"]
    except Exception:
        emit("error")
        return
    logger = logging.getLogger("Lark")
    logger.handlers = [SanitizedConnectionLog()]
    logger.propagate = False

    def receive(data):
        normalized = normalize_event(data, config["app_id"], bot_open_id)
        if normalized:
            store.ingest(normalized)  # SQLite commits before SDK acknowledges.
            emit("event")

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(receive).build()
    client = lark.ws.Client(config["app_id"], config["app_secret"], event_handler=handler,
                            domain="https://open.feishu.cn", log_level=lark.LogLevel.INFO)
    client.on_reconnecting = lambda: emit("connecting")
    client.on_reconnected = lambda: emit("connected")
    emit("connecting")
    try:
        client.start()
    except Exception:
        emit("error")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
