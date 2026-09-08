from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .notifiers import NotificationError, OneBotNotifier


ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 6 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
_seen_nonces: dict[str, int] = {}
_nonce_lock = threading.Lock()


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key.replace("_", "").isalnum() and not key[0].isdigit():
            os.environ.setdefault(key, value)


def _consume_nonce(nonce: str, timestamp: int, max_age: int) -> bool:
    now = int(time.time())
    with _nonce_lock:
        expired = [key for key, value in _seen_nonces.items() if now - value > max_age]
        for key in expired:
            del _seen_nonces[key]
        if nonce in _seen_nonces:
            return False
        _seen_nonces[nonce] = timestamp
        return True


def _forward_payload(payload: dict[str, object], notifier: OneBotNotifier) -> None:
    kind = payload.get("kind", "notification")
    if kind == "notification":
        title = payload["title"]
        message = payload["message"]
        if not isinstance(title, str) or not isinstance(message, str):
            raise ValueError("title and message must be strings")
        if not title.strip() or len(title) > 200 or len(message) > 20_000:
            raise ValueError("message size is invalid")
        notifier.send(title, message)
        return
    if kind == "text":
        text = payload["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            raise ValueError("message size is invalid")
        notifier._send_text(text)
        return
    if kind == "image":
        encoded = payload["data"]
        if not isinstance(encoded, str):
            raise ValueError("image data must be a base64 string")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("invalid image data") from exc
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise ValueError("image size is invalid")
        notifier._send_image_data(image)
        return
    raise ValueError("unsupported message kind")


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "NIPRelay/1.0"

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        self._reply(200, {"ok": True, "service": "nip-hltv-relay"})

    def do_POST(self) -> None:
        if self.path != "/notify":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"ok": False, "error": "invalid content length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(413, {"ok": False, "error": "invalid body size"})
            return
        body = self.rfile.read(length)
        timestamp_text = self.headers.get("X-Relay-Timestamp", "")
        nonce = self.headers.get("X-Relay-Nonce", "")
        supplied_signature = self.headers.get("X-Relay-Signature", "")
        try:
            timestamp = int(timestamp_text)
        except ValueError:
            self._reply(401, {"ok": False, "error": "invalid timestamp"})
            return
        max_age = int(os.getenv("RELAY_MAX_AGE_SECONDS", "300"))
        if abs(int(time.time()) - timestamp) > max_age:
            self._reply(401, {"ok": False, "error": "expired request"})
            return
        if not nonce or len(nonce) > 100:
            self._reply(401, {"ok": False, "error": "invalid nonce"})
            return
        secret = os.environ["RELAY_SECRET"].encode("utf-8")
        signed = timestamp_text.encode() + b"\n" + nonce.encode() + b"\n" + body
        expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied_signature):
            self._reply(401, {"ok": False, "error": "bad signature"})
            return
        if not _consume_nonce(nonce, timestamp, max_age):
            self._reply(409, {"ok": False, "error": "replayed request"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            notifier = OneBotNotifier(
                base_url=os.getenv("ONEBOT_HTTP_URL", "http://127.0.0.1:3000"),
                group_id=int(os.environ["QQ_GROUP_ID"]),
                access_token=os.getenv("ONEBOT_ACCESS_TOKEN", ""),
            )
            _forward_payload(payload, notifier)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._reply(400, {"ok": False, "error": str(exc)})
            return
        except NotificationError as exc:
            self._reply(502, {"ok": False, "error": str(exc)})
            return
        self._reply(200, {"ok": True})

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def run_relay() -> None:
    _load_local_env(ROOT / ".env")
    required = ["RELAY_SECRET", "QQ_GROUP_ID"]
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit(f"缺少配置：{', '.join(missing)}")
    if len(os.environ["RELAY_SECRET"]) < 20:
        raise SystemExit("RELAY_SECRET 至少需要 20 个字符")
    host = os.getenv("RELAY_LISTEN_HOST", "0.0.0.0")
    port = int(os.getenv("RELAY_LISTEN_PORT", "8787"))
    print(f"NIP/HLTV QQ 中继已启动：http://{host}:{port}")
    print("健康检查：GET /health；消息入口：POST /notify")
    ThreadingHTTPServer((host, port), RelayHandler).serve_forever()
