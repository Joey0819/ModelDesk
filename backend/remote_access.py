"""Private phone gateway. The unmodified local API never listens on the LAN.

Tailscale Serve terminates HTTPS in the tailnet and forwards only to this gateway.
Pairing credentials are single-use, stored as hashes, and never put in a URL.
"""
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/remote-access"
GATEWAY_PORT = 8766
HTTPS_PORT = 8443
COOKIE = "__Host-modeldesk-session"
LOCAL_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CAPABILITIES = {"native_desktop": False, "uploads": True, "group_control": True}
MAX_BODY = 64 * 1024 * 1024


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def checked_origin(origin):
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".ts.net")
            or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname) or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in {None, HTTPS_PORT}):
        raise ValueError("手机入口必须是本机 Tailscale HTTPS 域名")
    return origin


class RemoteStore:
    def __init__(self, directory=STATE, clock=time.time):
        self.directory, self.clock = Path(directory), clock

    @contextmanager
    def db(self):
        if self.directory.is_symlink():
            raise RuntimeError("Invalid remote access state directory")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        path = self.directory / "auth.sqlite3"
        if path.is_symlink():
            raise RuntimeError("Invalid remote access state path")
        conn = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pairing (hash TEXT PRIMARY KEY, label TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, last_used REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (at REAL NOT NULL);
            """)
            with conn:
                yield conn
        finally:
            conn.close()

    def configure(self, origin):
        checked_origin(origin)
        with self.db() as db:
            old = db.execute("SELECT value FROM config WHERE key='origin'").fetchone()
            if old and old[0] != origin:
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM pairing")
            db.execute("INSERT OR REPLACE INTO config VALUES ('origin', ?)", (origin,))

    @property
    def origin(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM config WHERE key='origin'").fetchone()
            return row[0] if row else None

    def create_pairing(self, label):
        if not self.origin:
            raise HTTPException(409, "请先在 Mac 启用手机访问入口")
        code = "-".join(secrets.token_hex(3).upper() for _ in range(2))
        expires = self.clock() + 300
        with self.db() as db:
            # Only the most recently displayed code can pair a device.
            db.execute("DELETE FROM pairing")
            db.execute("INSERT INTO pairing VALUES (?, ?, ?)", (digest(code.replace("-", "")), label, expires))
        return {"code": code, "expires_at": expires, "origin": self.origin}

    def pair(self, code, label=None):
        now = self.clock()
        # iOS smart punctuation can turn the displayed hyphen into an en dash;
        # pasted codes may also contain nonbreaking spaces or full-width text.
        normalized = re.sub(r"[\s\-\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]", "",
                            unicodedata.normalize("NFKC", code)).upper()
        valid_code = re.fullmatch(r"[0-9A-F]{12}", normalized) is not None
        token = secrets.token_urlsafe(32)
        session = {"id": str(uuid.uuid4()), "label": label, "created_at": now,
                   "expires_at": now + 43200, "last_used": now}
        error = None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE at < ?", (now - 300,))
            db.execute("DELETE FROM pairing WHERE expires <= ?", (now,))
            if db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= 10:
                error = HTTPException(429, "配对尝试过多，请五分钟后重试")
            else:
                row = db.execute("SELECT * FROM pairing WHERE hash=?", (digest(normalized),)).fetchone() if valid_code else None
                if row is None:
                    db.execute("INSERT INTO attempts VALUES (?)", (now,))
                    error = HTTPException(401, "配对码无效或已过期，请在 Mac 重新生成")
                else:
                    session["label"] = label or row["label"]
                    db.execute("DELETE FROM pairing WHERE hash=?", (row["hash"],))
                    db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                               (session["id"], digest(token), session["label"], now, session["expires_at"], now))
        if error:
            raise error
        return token, session

    def session(self, token):
        if not token or len(token) > 256:
            return None
        now = self.clock()
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = db.execute("SELECT * FROM sessions WHERE hash=?", (digest(token),)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result.pop("hash")
            if now - row["last_used"] > 60:
                db.execute("UPDATE sessions SET last_used=? WHERE id=?", (now, row["id"]))
            return result

    def sessions(self):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,label,created_at,expires_at,last_used FROM sessions WHERE expires_at > ? ORDER BY created_at DESC",
                (self.clock(),))]

    def revoke(self, identifier=None):
        with self.db() as db:
            if identifier:
                db.execute("DELETE FROM sessions WHERE id=?", (identifier,))
            else:
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM pairing")


def tailscale_binary():
    native = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    # Some installations wrap this binary in a shell without exec. Calling the
    # native CLI directly ensures timeout cancellation reaps the real process.
    return native if Path(native).is_file() else shutil.which("tailscale")


def tailscale_status():
    binary = tailscale_binary()
    if not binary:
        return {"installed": False, "state": "Missing", "dns_name": None}
    try:
        result = subprocess.run([binary, "status", "--json"], capture_output=True, timeout=8, check=True)
        raw = json.loads(result.stdout)
        return {"installed": True, "state": raw.get("BackendState", "Unknown"),
                "dns_name": raw.get("Self", {}).get("DNSName", "").rstrip(".") or None}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"installed": True, "state": "Unavailable", "dns_name": None}


def gateway_running():
    try:
        with socket.create_connection(("127.0.0.1", GATEWAY_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def require_local(request: Request):
    try:
        is_local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        is_local = False
    # Host and Origin are both checked: a web page cannot configure via DNS rebinding.
    if not is_local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "手机配对和设备管理只能在这台 Mac 操作")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不接受跨站配置请求")
    origin = request.headers.get("origin")
    if origin not in LOCAL_ORIGINS and (origin or request.method not in SAFE_METHODS):
        raise HTTPException(403, "请从本机 ModelDesk 配置手机访问")


store = RemoteStore()
router = APIRouter(prefix="/api/remote", tags=["remote-access"], dependencies=[Depends(require_local)])


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="我的手机", min_length=1, max_length=80)


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, min_length=1, max_length=80)


@router.get("/status")
async def remote_status():
    return {"configured": bool(store.origin), "origin": store.origin,
            "gateway_running": controller.running, "enabled": controller.enabled(),
            "tailscale": await asyncio.to_thread(tailscale_status), "sessions": store.sessions(),
            "capabilities": CAPABILITIES, "error": controller.last_error}


@router.post("/pairings")
async def new_pairing(body: PairingRequest):
    return store.create_pairing(body.label)


@router.post("/sessions/{identifier}/revoke")
async def revoke_session(identifier: uuid.UUID):
    store.revoke(str(identifier))
    return {"revoked": True}


@router.post("/revoke-all")
async def revoke_all():
    store.revoke()
    return {"revoked": True}


def api_allowed(path, method):
    if path.startswith(("/api/remote", "/api/model-connections", "/api/model-runtime")):
        return False
    if method not in SAFE_METHODS:
        if path.startswith("/api/muse") or path.startswith("/api/platform/originals"):
            return False
        if re.fullmatch(r"/api/workbench/projects/[^/]+/folders", path):
            return False
    return True


def create_gateway(remote_store=None, *, frontend=None, upstream_transport=None, network_guard=None):
    """Only serve with uvicorn --host 127.0.0.1 --no-proxy-headers."""
    auth = remote_store or store
    dist = Path(frontend or ROOT / "frontend/dist").resolve()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", trust_env=False,
                follow_redirects=False, transport=upstream_transport,
                timeout=httpx.Timeout(connect=10, read=None, write=60, pool=10)) as client:
            app.state.upstream = client
            yield

    app = FastAPI(title="ModelDesk Private Phone Gateway", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.auth = auth

    @app.middleware("http")
    async def boundary(request, call_next):
        origin = auth.origin
        host = urlsplit(origin).netloc if origin else None
        if not host or request.headers.get("host", "").lower() != host.lower():
            return JSONResponse({"detail": "请使用已配置的 Tailscale HTTPS 手机入口"}, status_code=403)
        # No encoded routing separators or dot segments can reach the local API.
        raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
        if b"%" in raw_path or "\\" in request.url.path or "//" in request.url.path or any(
                part in {".", ".."} for part in request.url.path.split("/")):
            return JSONResponse({"detail": "Invalid path"}, status_code=400)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不接受跨站访问"}, status_code=403)
        if request.method not in SAFE_METHODS and request.headers.get("origin") != origin:
            return JSONResponse({"detail": "Origin 不匹配，请从手机入口重新打开"}, status_code=403)
        # Cookies are scoped to hosts, not ports. Recheck the dedicated HTTPS
        # hostname before accepting a cookie or issuing one; another Serve app
        # must not be able to reuse a cookie it received on a different port.
        if network_guard and (request.url.path == "/remote/pair" or auth.session(request.cookies.get(COOKIE))):
            try:
                await network_guard()
            except HTTPException as error:
                return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        return response

    @app.get("/remote/info")
    async def info(request: Request):
        return {"remote": True, "pairing_required": auth.session(request.cookies.get(COOKIE)) is None,
                "origin": auth.origin, "capabilities": CAPABILITIES}

    @app.get("/remote/session")
    async def session(request: Request):
        current = auth.session(request.cookies.get(COOKIE))
        return {"authenticated": current is not None, "expires_at": current["expires_at"] if current else None,
                "capabilities": CAPABILITIES}

    @app.post("/remote/pair")
    async def pair(body: RedeemRequest):
        token, current = auth.pair(body.code, body.label)
        response = JSONResponse({"authenticated": True, "expires_at": current["expires_at"], "capabilities": CAPABILITIES})
        response.set_cookie(COOKIE, token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/remote/logout")
    async def logout(request: Request):
        current = auth.session(request.cookies.get(COOKIE))
        if current:
            auth.revoke(current["id"])
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return response

    @app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request):
        token = request.cookies.get(COOKIE)
        if not auth.session(token):
            raise HTTPException(401, "请先在这台 Mac 生成配对码并连接手机")
        target = "/api/" + path
        if not api_allowed(target, request.method):
            raise HTTPException(403, "此操作需要在 Mac 的 ModelDesk 中完成")
        # A fixed origin and narrow header allowlist prevent SSRF and identity spoofing.
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() in {"content-type", "accept", "last-event-id", "range", "if-none-match", "origin"}}
        # These explicit user-control endpoints share the local API approval
        # boundary. Only an authenticated, same-origin gateway request is promoted.
        if request.method == "POST" and (request.url.path in {"/api/group-tasks", "/api/group-approvals"} or re.fullmatch(
                r"/api/(?:group-tasks/[0-9a-f-]{36}/cancel|group-approvals/[0-9a-f-]{36}/decision|groups/[0-9a-f-]{36}/messages(?:/[0-9a-f-]{36}/continue)?)", request.url.path)):
            headers["origin"] = "http://localhost:5173"
        headers["X-ModelDesk-Remote"] = "phone"
        query = request.url.query
        target += ("?" + query) if query else ""
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid content length")
        if length < 0 or length > MAX_BODY:
            raise HTTPException(413, "单次上传不能超过 64 MB")
        # Spool bounded bodies before forwarding: rejected uploads never partially mutate upstream.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "单次上传不能超过 64 MB")
        if request.method == "POST" and request.url.path == "/api/workbench/jobs":
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise HTTPException(400, "任务必须使用有效的 JSON 请求")
            if isinstance(payload, dict) and payload.get("mode") == "sdk_script":
                raise HTTPException(403, "原生 SDK 脚本只能在 Mac 上运行；手机可以查看执行结果")
        client = request.app.state.upstream
        try:
            forwarded = client.build_request(request.method, target, headers=headers, content=bytes(body))
            forwarded.headers.pop("cookie", None)
            upstream = await client.send(forwarded, stream=True)
        except httpx.HTTPError:
            raise HTTPException(502, "Mac 上的 ModelDesk 服务暂时不可用")
        response_headers = {key: value for key, value in upstream.headers.items()
                            if key.lower() in {"content-type", "content-disposition", "content-length", "content-range", "accept-ranges", "etag", "content-encoding"}}
        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    if not auth.session(token):
                        break
                    yield chunk
            finally:
                await upstream.aclose()
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def frontend_file(path: str):
        if path.startswith(("api/", "remote/")):
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if not candidate.is_relative_to(dist):
            raise HTTPException(404)
        if not candidate.is_file():
            if "." in Path(path).name:
                raise HTTPException(404)
            candidate = dist / "index.html"
        if not candidate.resolve().is_relative_to(dist):
            raise HTTPException(404)
        if not candidate.is_file():
            raise HTTPException(503, "请先构建 ModelDesk 前端")
        return FileResponse(candidate)

    return app


class RemoteController:
    """The API owns the gateway lifetime; Tailscale settings are never reset."""
    def __init__(self, auth=store):
        self.auth, self.server, self.task = auth, None, None
        self.lock = asyncio.Lock()
        self.guard_lock = asyncio.Lock()
        self.last_error = None

    def enabled(self):
        with self.auth.db() as db:
            row = db.execute("SELECT value FROM config WHERE key='enabled'").fetchone()
            return bool(row and row[0] == 'true')

    def set_enabled(self, value):
        with self.auth.db() as db:
            db.execute("INSERT OR REPLACE INTO config VALUES ('enabled', ?)", ('true' if value else 'false',))

    @property
    def running(self):
        return bool(self.server and self.server.started and not self.server.should_exit and self.task and not self.task.done())

    async def _launch(self):
        if self.running:
            return
        if self.task:
            await self.shutdown()
        import uvicorn
        class GatewayServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                # Only the main API server owns process signals.
                yield
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", GATEWAY_PORT))
            listener.listen(128)
            listener.setblocking(False)
        except OSError:
            listener.close()
            raise HTTPException(409, "手机网关端口已被占用，请检查本机服务")
        server = GatewayServer(uvicorn.Config(create_gateway(self.auth, network_guard=self.authorize_hostname), host="127.0.0.1", port=GATEWAY_PORT,
            proxy_headers=False, access_log=False, log_level="warning", timeout_graceful_shutdown=2))
        self.server = server
        self.task = asyncio.create_task(server.serve(sockets=[listener]), name="modeldesk-phone-gateway")
        for _ in range(100):
            if server.started:
                return
            if self.task.done():
                listener.close()
                await self.task
                raise HTTPException(503, "手机网关未能启动")
            await asyncio.sleep(0.02)
        await self.shutdown()
        raise HTTPException(503, "手机网关启动超时")

    async def recover(self):
        # Never alter networking or create new sessions at startup.
        if self.enabled() and self.auth.origin:
            try:
                await self.authorize_hostname()
                await self._launch()
            except (OSError, HTTPException):
                self.server = None

    async def shutdown(self):
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        self.server, self.task = None, None

    async def command(self, *args):
        binary = tailscale_binary()
        if not binary:
            raise HTTPException(409, "请先在 Mac 和手机安装并登录 Tailscale")
        process = await asyncio.create_subprocess_exec(binary, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(503, "Tailscale 操作超时；请检查连接和 HTTPS 设置")
        if process.returncode:
            raise HTTPException(409, "Tailscale Serve 未就绪；请在 Tailscale 检查连接及 HTTPS 授权")
        return stdout

    async def serve_config(self):
        try:
            return json.loads(await self.command("serve", "status", "--json"))
        except ValueError:
            raise HTTPException(503, "无法读取 Tailscale Serve 配置")

    @staticmethod
    def slot(config, origin):
        """Return absent/owned/conflict, refusing all Funnel on our listener."""
        host = urlsplit(origin).netloc
        # Secure cookies still cross HTTPS ports. All node TCP listeners may
        # terminate TLS, including raw forwards, so this node hostname must be
        # dedicated to ModelDesk. Separately named Tailscale Services are fine.
        # Foreground Serve sessions can also add node-host listeners outside
        # the background TCP/Web maps. Their configuration is not ours.
        if config.get("Foreground"):
            return "conflict"
        if any(str(port) != str(HTTPS_PORT) for port in config.get("TCP", {})):
            return "conflict"
        if any(name != host for name in config.get("Web", {})):
            return "conflict"
        if config.get("AllowFunnel", {}).get(host):
            return "conflict"
        tcp = config.get("TCP", {}).get(str(HTTPS_PORT))
        webs = {name: value for name, value in config.get("Web", {}).items() if name.endswith(f":{HTTPS_PORT}")}
        if not tcp and not webs:
            return "absent"
        expected = {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{GATEWAY_PORT}"}}}
        return "owned" if tcp == {"HTTPS": True} and webs == {host: expected} else "conflict"

    async def authorize_hostname(self):
        async with self.guard_lock:
            try:
                isolated = self.auth.origin and self.slot(await self.serve_config(), self.auth.origin) == "owned"
            except HTTPException:
                isolated = False
            if not isolated:
                self.auth.revoke()
                self.set_enabled(False)
                self.last_error = "Tailscale HTTPS 域名不再独占或配置无法核对；手机授权已撤销，请在 Mac 检查手机访问设置"
                if self.server:
                    self.server.should_exit = True
                raise HTTPException(403, self.last_error)

    async def start(self):
        async with self.lock:
            status = await asyncio.to_thread(tailscale_status)
            if status["state"] != "Running" or not status["dns_name"]:
                raise HTTPException(409, "请先在 Mac 打开 Tailscale 连接；手机登录同一账号")
            origin = checked_origin(f"https://{status['dns_name']}:{HTTPS_PORT}")
            config = await self.serve_config()
            ownership = self.slot(config, origin)
            if ownership == "conflict":
                self.auth.revoke()
                self.set_enabled(False)
                await self.shutdown()
                self.last_error = "该 Tailscale HTTPS 域名存在其他服务或公网分享；为避免跨端口 Cookie 泄露，未覆盖现有配置且已撤销手机授权"
                raise HTTPException(409, self.last_error)
            self.auth.configure(origin)
            await self._launch()
            try:
                if ownership == "absent":
                    await self.command("serve", "--bg", f"--https={HTTPS_PORT}", "--yes", f"http://127.0.0.1:{GATEWAY_PORT}")
                if self.slot(await self.serve_config(), origin) != "owned":
                    raise HTTPException(503, "手机入口配置未通过核对，网关已停止")
                self.set_enabled(True)
                self.last_error = None
            except Exception:
                self.set_enabled(False)
                await self.shutdown()
                raise
            return {"enabled": True, "origin": origin, "gateway_running": self.running}

    async def stop(self):
        async with self.lock:
            self.set_enabled(False)
            self.auth.revoke()
            await self.shutdown()
            # A user may have changed this Serve slot since start. Never remove theirs.
            route_removed = False
            try:
                if self.auth.origin and self.slot(await self.serve_config(), self.auth.origin) == "owned":
                    await self.command("serve", f"--https={HTTPS_PORT}", "off")
                    route_removed = True
            except HTTPException:
                pass  # The stopped gateway and revoked sessions are already fail-closed.
            return {"enabled": False, "gateway_running": False, "route_removed": route_removed}


controller = RemoteController()


@router.post("/start")
async def start_remote():
    return await controller.start()


@router.post("/stop")
async def stop_remote():
    return await controller.stop()
