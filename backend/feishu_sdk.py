"""Small typed adapter for the official lark-oapi SDK; no provider-call retries."""
import io
import json
import logging


class FeishuAPIError(RuntimeError):
    pass


class FeishuSDK:
    def __init__(self, config):
        import lark_oapi as lark
        # SDK diagnostics can contain signed URLs or response bodies. They are
        # never forwarded into ModelDesk's logs or API error messages.
        logger = logging.getLogger("Lark")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        self.client = (lark.Client.builder().app_id(config["app_id"]).app_secret(config["app_secret"])
                       .domain("https://open.feishu.cn").timeout(30).log_level(lark.LogLevel.ERROR).build())

    @staticmethod
    def checked(response):
        if not response.success():
            code = response.code if isinstance(response.code, int) else "unknown"
            raise FeishuAPIError(f"飞书 API 返回错误代码 {code}；请检查应用凭据、权限和发布状态")
        return response.data

    def chats(self, page_token=None):
        from lark_oapi.api.im.v1 import ListChatRequest
        builder = ListChatRequest.builder().page_size(50)
        if page_token:
            builder.page_token(page_token)
        data = self.checked(self.client.im.v1.chat.list(builder.build()))
        return {"items": [{"chat_id": item.chat_id, "name": item.name or item.chat_id} for item in data.items or []],
                "has_more": bool(data.has_more), "page_token": data.page_token}

    def bot_info(self):
        import lark_oapi as lark
        request = (lark.BaseRequest.builder().http_method(lark.HttpMethod.GET).uri("/open-apis/bot/v3/info")
                   .token_types({lark.AccessTokenType.TENANT}).build())
        response = self.client.request(request)
        if not response.success():
            self.checked(response)
        data = json.loads(response.raw.content).get("bot") or {}
        if not isinstance(data.get("open_id"), str) or not data["open_id"].startswith("ou_"):
            raise FeishuAPIError("未取得应用机器人身份，请启用机器人能力并发布应用")
        return data

    def reply(self, message_id, msg_type, content, dispatch_id):
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        request = (ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type(msg_type).content(json.dumps(content, ensure_ascii=False))
            .uuid(dispatch_id).build()).build())
        data = self.checked(self.client.im.v1.message.reply(request))
        if not data or not data.message_id:
            raise FeishuAPIError("飞书未返回可确认的消息回执")
        return data.message_id

    def upload(self, entry, raw):
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
        request = CreateFileRequest.builder().request_body(CreateFileRequestBody.builder()
            .file_type(entry["format"] if entry["format"] in {"pdf", "mp4"} else "stream").file_name(entry["filename"]).file(io.BytesIO(raw)).build()).build()
        data = self.checked(self.client.im.v1.file.create(request))
        if not data or not data.file_key:
            raise FeishuAPIError("飞书未返回可确认的文件回执")
        return data.file_key


def safe_error(error):
    return str(error) if isinstance(error, FeishuAPIError) else "飞书连接或发送未能确认；请检查本机网络、应用凭据及权限，不会自动重复发送"
