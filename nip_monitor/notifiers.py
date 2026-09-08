from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

from .articles import Article, ArticleBlock


class NotificationError(RuntimeError):
    pass


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_ARTICLE_IMAGES = 8
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ARTICLE_IMAGE_BYTES = 4 * 1024 * 1024


def _format_published_at(value: str) -> str:
    try:
        published = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return value
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    local = published.astimezone(SHANGHAI)
    return f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"


def _download_image(url: str) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={"User-Agent": "nip-hltv-monitor/1.0 (personal, non-commercial monitor)"},
    )
    try:
        with urlopen(request, timeout=45) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise NotificationError(f"返回内容不是图片：{content_type}")
            data = response.read(MAX_IMAGE_BYTES + 1)
    except NotificationError:
        raise
    except Exception as exc:
        raise NotificationError(f"图片下载失败：{exc}") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise NotificationError("图片超过 4 MiB，已跳过")
    if not data:
        raise NotificationError("图片内容为空")
    return data, content_type


def _article_operations(label: str, article: Article) -> list[tuple[str, str]]:
    header = f"{label}\n\n{article.title}"
    if article.published_at:
        header += f"\n发布时间：{_format_published_at(article.published_at)}"
    pending = header + "\n━━━━━━━━━━━━"
    operations: list[tuple[str, str]] = []
    text_count = 0
    image_count = 0

    def append_text(value: str) -> None:
        nonlocal pending
        addition = ("\n\n" if pending else "") + value
        if pending and len(pending) + len(addition) > 2800:
            operations.append(("text", pending))
            pending = value
        else:
            pending += addition

    for block in article.blocks:
        if block.kind == "text":
            if text_count >= 10:
                continue
            text_count += 1
            append_text(block.text)
        elif block.kind == "image":
            if text_count >= 10 or image_count >= MAX_ARTICLE_IMAGES or not block.url:
                continue
            if pending:
                operations.append(("text", pending))
                pending = ""
            operations.append(("image", block.url))
            image_count += 1
        elif block.kind == "schedule":
            append_text(_format_article_schedule(block))

    append_text(f"🔗点此阅读原文：{article.original_url}")
    if pending:
        operations.append(("text", pending))
    return operations


def _article_message_parts(label: str, article: Article) -> list[dict[str, str]]:
    parts: list[dict[str, str]] = []
    image_bytes = 0
    for kind, value in _article_operations(label, article):
        if kind == "text":
            if parts and parts[-1]["kind"] == "text":
                value = "\n\n" + value
            parts.append({"kind": "text", "text": value})
            continue
        try:
            data, content_type = _download_image(value)
        except NotificationError as exc:
            print(f"图片处理失败，已跳过且保留整篇文字：{exc}")
            continue
        if image_bytes + len(data) > MAX_ARTICLE_IMAGE_BYTES:
            print("文章图片累计超过 4 MiB，本张已跳过且保留整篇文字。")
            continue
        image_bytes += len(data)
        parts.append(
            {
                "kind": "image",
                "content_type": content_type,
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
    return parts


def _parts_to_onebot(parts: list[dict[str, str]]) -> list[dict[str, object]]:
    message: list[dict[str, object]] = []
    for part in parts:
        if part["kind"] == "text":
            message.append({"type": "text", "data": {"text": part["text"]}})
        else:
            message.append(
                {
                    "type": "image",
                    "data": {"file": f"base64://{part['data']}"},
                }
            )
    return message


class Notifier:
    name = "notifier"

    def send(self, title: str, message: str) -> None:
        raise NotImplementedError


@dataclass
class OneBotNotifier(Notifier):
    base_url: str
    group_id: int
    access_token: str = ""
    name = "QQ 群"

    def send(self, title: str, message: str) -> None:
        self._send_text(f"{title}\n\n{message}")

    def send_article(self, label: str, article: Article) -> None:
        parts = _article_message_parts(label, article)
        try:
            self._post_message(_parts_to_onebot(parts))
        except NotificationError as exc:
            text_parts = [part for part in parts if part["kind"] == "text"]
            if len(text_parts) == len(parts):
                raise
            print(f"整条图文发送失败，正在降级为一条纯文字消息：{exc}")
            self._post_message(_parts_to_onebot(text_parts))

    def _send_text(self, text: str) -> None:
        for start in range(0, len(text), 3000):
            self._post_message([{"type": "text", "data": {"text": text[start:start + 3000]}}])

    def _send_image_data(self, data: bytes) -> None:
        encoded = base64.b64encode(data).decode("ascii")
        self._post_message(
            [{"type": "image", "data": {"file": f"base64://{encoded}"}}]
        )

    def _post_message(self, message: list[dict[str, object]]) -> None:
        payload = json.dumps(
            {"group_id": self.group_id, "message": message},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = Request(
            f"{self.base_url.rstrip('/')}/send_group_msg",
            data=payload,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("retcode", 0) != 0:
                raise NotificationError(f"OneBot 返回错误：{result}")
        except NotificationError:
            raise
        except Exception as exc:
            raise NotificationError(f"连接 NapCat/OneBot 失败：{exc}") from exc


@dataclass
class RelayNotifier(Notifier):
    url: str
    secret: str
    name = "国内 QQ 中继"

    def send(self, title: str, message: str) -> None:
        self._send_payload({"kind": "notification", "title": title, "message": message})

    def send_article(self, label: str, article: Article) -> None:
        parts = _article_message_parts(label, article)
        try:
            self._send_payload({"kind": "rich_message", "parts": parts})
        except NotificationError as exc:
            text_parts = [part for part in parts if part["kind"] == "text"]
            if len(text_parts) == len(parts):
                raise
            print(f"整条图文中继失败，正在降级为一条纯文字消息：{exc}")
            self._send_payload({"kind": "rich_message", "parts": text_parts})

    def _send_payload(self, payload: dict[str, object]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
        signature = hmac.new(
            self.secret.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Relay-Timestamp": timestamp,
                "X-Relay-Nonce": nonce,
                "X-Relay-Signature": signature,
            },
        )
        try:
            with urlopen(request, timeout=90) as response:
                response.read()
        except Exception as exc:
            raise NotificationError(f"发送到国内中继失败：{exc}") from exc


@dataclass
class NtfyNotifier(Notifier):
    topic: str
    server: str = "https://ntfy.sh"
    name = "ntfy"

    def send(self, title: str, message: str) -> None:
        payload = json.dumps(
            {"topic": self.topic, "title": title, "message": message},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.server.rstrip("/"),
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        _open_notification(request)


@dataclass
class TelegramNotifier(Notifier):
    token: str
    chat_id: str
    name = "Telegram"

    def send(self, title: str, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps(
            {"chat_id": self.chat_id, "text": f"{title}\n\n{message}", "disable_web_page_preview": True}
        ).encode("utf-8")
        request = Request(url, data=payload, method="POST", headers={"Content-Type": "application/json"})
        _open_notification(request)


@dataclass
class EmailNotifier(Notifier):
    host: str
    port: int
    username: str
    password: str
    recipient: str
    sender: str
    use_ssl: bool = True
    name = "邮箱"

    def send(self, title: str, message: str) -> None:
        mail = EmailMessage()
        mail["Subject"] = title
        mail["From"] = self.sender
        mail["To"] = self.recipient
        mail.set_content(message)
        context = ssl.create_default_context()
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=context) as client:
                client.login(self.username, self.password)
                client.send_message(mail)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as client:
                client.starttls(context=context)
                client.login(self.username, self.password)
                client.send_message(mail)


def configured_notifiers() -> list[Notifier]:
    result: list[Notifier] = []
    qq_group = os.getenv("QQ_GROUP_ID", "").strip()
    if qq_group:
        result.append(
            OneBotNotifier(
                base_url=os.getenv("ONEBOT_HTTP_URL", "http://127.0.0.1:3000").strip(),
                group_id=int(qq_group),
                access_token=os.getenv("ONEBOT_ACCESS_TOKEN", "").strip(),
            )
        )

    if topic := os.getenv("NTFY_TOPIC", "").strip():
        result.append(NtfyNotifier(topic=topic, server=os.getenv("NTFY_SERVER", "https://ntfy.sh")))

    relay_url = os.getenv("RELAY_URL", "").strip()
    relay_secret = os.getenv("RELAY_SECRET", "").strip()
    if relay_url and relay_secret:
        result.append(RelayNotifier(url=relay_url, secret=relay_secret))

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if telegram_token and telegram_chat:
        result.append(TelegramNotifier(telegram_token, telegram_chat))

    required_email = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "NOTIFY_EMAIL"]
    if all(os.getenv(key, "").strip() for key in required_email):
        username = os.environ["SMTP_USERNAME"].strip()
        result.append(
            EmailNotifier(
                host=os.environ["SMTP_HOST"].strip(),
                port=int(os.getenv("SMTP_PORT") or "465"),
                username=username,
                password=os.environ["SMTP_PASSWORD"],
                recipient=os.environ["NOTIFY_EMAIL"].strip(),
                sender=os.getenv("SMTP_FROM", username).strip(),
                use_ssl=os.getenv("SMTP_USE_SSL", "true").lower() not in {"0", "false", "no"},
            )
        )
    return result


def deliver(title: str, message: str, notifiers: list[Notifier]) -> None:
    print(f"\n=== {title} ===\n{message}\n")
    errors: list[str] = []
    for notifier in notifiers:
        try:
            notifier.send(title, message)
            print(f"已通过 {notifier.name} 发送")
        except Exception as exc:  # Keep other configured channels working.
            errors.append(f"{notifier.name}: {exc}")
    if errors:
        raise NotificationError("；".join(errors))


def deliver_article(label: str, article: Article, notifiers: list[Notifier]) -> None:
    plain = _article_as_text(article)
    print(f"\n=== {label} ===\n{plain}\n")
    errors: list[str] = []
    for notifier in notifiers:
        try:
            if isinstance(notifier, (OneBotNotifier, RelayNotifier)):
                notifier.send_article(label, article)
            else:
                notifier.send(label, plain)
            print(f"已通过 {notifier.name} 发送")
        except Exception as exc:
            errors.append(f"{notifier.name}: {exc}")
    if errors:
        raise NotificationError("；".join(errors))


def _format_article_schedule(block: ArticleBlock) -> str:
    groups: dict[str, list] = {}
    for match in block.matches:
        groups.setdefault(match.event or "待定", []).append(match)
    sections: list[str] = []
    for event, matches in groups.items():
        lines = [f"🎮 赛事: {event}"]
        for match in matches:
            when = ""
            if match.start_at:
                local = datetime.fromisoformat(
                    match.start_at.replace("Z", "+00:00")
                ).astimezone(SHANGHAI)
                when = f"{local:%d/%m/%Y %H:%M} "
            lines.append(f"⚔️ {when}{match.team1} vs {match.team2}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _article_as_text(article: Article) -> str:
    parts = [article.title]
    if article.published_at:
        parts.append(f"发布时间：{_format_published_at(article.published_at)}")
    text_count = 0
    for block in article.blocks:
        if block.kind == "text":
            if text_count >= 10:
                continue
            text_count += 1
            parts.append(block.text)
        elif block.kind == "schedule":
            parts.append(_format_article_schedule(block))
    parts.append(f"🔗点此阅读原文：{article.original_url}")
    return "\n\n".join(parts)


def _open_notification(request: Request) -> None:
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except Exception as exc:
        raise NotificationError(str(exc)) from exc
