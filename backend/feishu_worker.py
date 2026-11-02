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


def message_text(message):
    """Preserve textual rich posts from mobile paste; never drop attachments silently."""
    try:
        if not isinstance(message.content, str) or len(message.content) > 100000:
            return '', 'message_too_large'
        content = json.loads(message.content)
        if not isinstance(content, dict):
            return '', 'invalid_content'
        if message.message_type == 'text':
            text = content.get('text')
            return (text, None) if isinstance(text, str) else ('', 'invalid_content')
        if message.message_type != 'post':
            return '', 'unsupported_content'
        if 'content' not in content:
            variants = [value for value in content.values() if isinstance(value, dict) and 'content' in value]
            if len(variants) != 1:
                return '', 'invalid_content'
            content = variants[0]
        lines = content.get('content')
        if not isinstance(lines, list) or not isinstance(content.get('title', ''), str):
            return '', 'invalid_content'
        result = [content['title']] if content.get('title') else []
        unsupported = False
        for line in lines:
            if not isinstance(line, list):
                return '', 'invalid_content'
            pieces = []
            for item in line:
                if not isinstance(item, dict):
                    return '', 'invalid_content'
                tag = item.get('tag')
                if tag in {'text', 'md'} and isinstance(item.get('text'), str):
                    pieces.append(item['text'])
                elif tag == 'a' and isinstance(item.get('text'), str) and isinstance(item.get('href'), str):
                    pieces.append(item['text'] if item['text'] == item['href'] else item['text'] + ' (' + item['href'] + ')')
                elif tag == 'at' and isinstance(item.get('user_id'), str):
                    # The authenticated mentions list supplies the only bot identity.
                    mention = next((m for m in message.mentions or [] if getattr(getattr(m, 'id', None), 'open_id', None) == item['user_id']), None)
                    pieces.append(getattr(mention, 'key', None) or '@' + (item.get('user_name') or item['user_id']))
                else:
                    unsupported = True
            result.append(''.join(pieces))
        return '\n'.join(result), 'unsupported_content' if unsupported else None
    except (ValueError, TypeError, AttributeError):
        return '', 'invalid_content'


def normalize_event(data, app_id, bot_open_id):
    event = data.event
    message, sender = event.message, event.sender
    header_app = getattr(getattr(data, "header", None), "app_id", None)
    if header_app and header_app != app_id:
        return None
    text, input_error = message_text(message)
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
            "chat_type": message.chat_type, "message_type": message.message_type, "text": text, "input_error": input_error,
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
