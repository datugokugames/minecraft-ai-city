"""Private LAN HTTP adapter. No uploads, shell execution or arbitrary path routes."""

from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .web_state import Conflict, Workspace


def validate_bind(host, lan=False):
    address = ipaddress.ip_address(host)
    if address.version != 4:
        raise ValueError("IPv4アドレスを指定してください。")
    local = address.is_loopback
    private = any(
        address in ipaddress.ip_network(n)
        for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if not local and not (lan and private):
        raise ValueError(
            "既定は127.0.0.1です。LAN利用は --lan --host <PCのプライベートIPv4> を指定してください。"
        )


class CityServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, workspace, host="127.0.0.1", port=8765, lan=False):
        validate_bind(host, lan)
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__((host, port), Handler)
        port = self.server_address[1]
        self.authorities = {f"{host}:{port}"}
        if host == "127.0.0.1":
            self.authorities.add(f"localhost:{port}")

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "CityLocal"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Do not log instructions, tokens, request headers or query strings.

    def send(self, status, body, mime="application/json; charset=utf-8", filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, post=False):
        if self.headers.get("Host") not in self.server.authorities:
            return self.send(403, {"error": "Host not allowed"})
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers["Host"]:
            return self.send(403, {"error": "Origin not allowed"})
        path = urlsplit(self.path).path
        if path in {"/", "/app.js", "/style.css"} and not post:
            file = Path(__file__).with_name("web_static") / (
                "index.html" if path == "/" else path[1:]
            )
            return self.send(
                200, file.read_bytes(), mimetypes.guess_type(file.name)[0] + "; charset=utf-8"
            )
        if not path.startswith("/api/"):
            return self.send(404, {"error": "Not found"})
        if not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.token
        ):
            return self.send(401, {"error": "起動時の接続URLから開いてください。"})
        workspace = self.server.workspace
        if not workspace.lock.acquire(blocking=False):
            return self.send(409, {"error": "他の処理を実行中です。完了後に再試行してください。"})
        try:
            if not post:
                if path == "/api/state":
                    return self.send(200, workspace.state())
                if path.startswith("/api/files/"):
                    file = workspace.artifact(path[len("/api/files/") :])
                    return self.send(
                        200,
                        file.read_bytes(),
                        mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                        file.name if file.suffix in {".json", ".schem"} else None,
                    )
                return self.send(404, {"error": "Not found"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "JSON required"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384 or self.headers.get("Transfer-Encoding"):
                return self.send(413, {"error": "Request must be 1..16384 bytes"})
            data = json.loads(self.rfile.read(length))
            action = {
                "/api/plan": workspace.plan,
                "/api/apply": workspace.apply,
                "/api/undo": workspace.undo,
            }.get(path)
            if action is None:
                return self.send(404, {"error": "Not found"})
            return self.send(200, action(data))
        except Conflict as exc:
            self.send(409, {"error": str(exc)})
        except FileNotFoundError:
            self.send(404, {"error": "保存ファイルが見つかりません。"})
        except ValueError as exc:
            self.send(422, {"error": str(exc), "code": getattr(exc, "code", "invalid_request")})
        except (OSError, KeyError, TypeError):
            self.send(
                500, {"error": "保存または処理に失敗しました。サーバーの保存先を確認してください。"}
            )
        finally:
            workspace.lock.release()

    def do_GET(self):
        try:
            self.dispatch()
        except (ConnectionError, TimeoutError):
            pass  # Client left; completed snapshots and the manifest remain authoritative.

    def do_POST(self):
        try:
            self.dispatch(post=True)
        except (ConnectionError, TimeoutError):
            pass


def serve(args):
    validate_bind(args.host, args.lan)
    workspace = Workspace(args.state_dir, args.project, args.parser)
    try:
        with CityServer(workspace, args.host, args.port, args.lan) as server:
            print(
                f"接続URL（このPCと信頼するLAN端末だけで使用）: http://{args.host}:{server.server_port}/#token={server.token}",
                flush=True,
            )
            print("終了: Ctrl+C。接続トークンは再起動時に更新されます。", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        workspace.close()
