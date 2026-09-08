from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.request import Request, urlopen

from .articles import Article, ArticleBlock


class NotificationError(RuntimeError):
    pass


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
        # Download every editorial image before the first QQ message. If the
        # network is temporarily unavailable, the whole article can retry next
        # round without having already sent half of it.
        prepared_images = {
            block.url: self._download_image(block.url, article.original_url)
            for block in article.blocks
            if block.kind == "image"
        }
        header = f"{label}\n\n{article.title}"
        if article.published_at:
            header += f"\n发布时间：{article.published_at}"
        header += "\n━━━━━━━━━━━━"
        pending = header

        for block in article.blocks:
            if block.kind == "text":
                addition = f"\n\n{block.text}"
                if len(pending) + len(addition) > 2800:
                    self._send_text(pending)
                    pending = block.text
                else:
                    pending += addition
            elif block.kind == "image":
                if pending.strip():
                    self._send_text(pending)
                    pending = ""
                self._send_image(prepared_images[block.url])
            elif block.kind == "schedule":
                addition = "\n\n" + _format_article_schedule(block)
                if len(pending) + len(addition) > 2800:
                    self._send_text(pending)
                    pending = addition.strip()
                else:
                    pending += addition

        footer = f"\n\n🔗 原文：{article.original_url}"
        if len(pending) + len(footer) > 2800:
            if pending.strip():
                self._send_text(pending)
            pending = footer.strip()
        else:
            pending += footer
        if pending.strip():
            self._send_text(pending)

    def _send_text(self, text: str) -> None:
        for start in range(0, len(text), 3000):
            self._post_message([{"type": "text", "data": {"text": text[start:start + 3000]}}])

    def _download_image(self, image_url: str, referer: str = "https://www.hltv.org/") -> str:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            request = Request(
                image_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
            )
            try:
                with urlopen(request, timeout=45) as response:
                    content_type = response.headers.get_content_type()
                    data = response.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise NotificationError("正文图片超过 8 MB，已拒绝发送")
                if not content_type.startswith("image/"):
                    raise NotificationError(f"正文图片类型异常：{content_type}")
                return base64.b64encode(data).decode("ascii")
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
        raise NotificationError(f"下载正文图片失败：{last_error}") from last_error

    def _send_image(self, encoded: str) -> None:
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
            if isinstance(notifier, OneBotNotifier):
                notifier.send_article(label, article)
            else:
                notifier.send(label, plain)
            print(f"已通过 {notifier.name} 发送")
        except Exception as exc:
            errors.append(f"{notifier.name}: {exc}")
    if errors:
        raise NotificationError("；".join(errors))


def _format_article_schedule(block: ArticleBlock) -> str:
    lines = ["🎮【文中赛程】"]
    last_event = ""
    shanghai = timezone(timedelta(hours=8), name="Asia/Shanghai")
    for match in block.matches:
        if match.event and match.event != last_event:
            lines.append(f"赛事：{match.event}")
            last_event = match.event
        if match.start_at:
            local = datetime.fromisoformat(match.start_at.replace("Z", "+00:00")).astimezone(shanghai)
            lines.append(f"⏰ 北京时间 {local:%Y-%m-%d %H:%M}")
        lines.append(f"⚔️ {match.team1} vs {match.team2}")
        if match.url:
            lines.append(match.url)
        lines.append("")
    return "\n".join(lines).rstrip()


def _article_as_text(article: Article) -> str:
    parts = [article.title]
    if article.published_at:
        parts.append(f"发布时间：{article.published_at}")
    for block in article.blocks:
        if block.kind == "text":
            parts.append(block.text)
        elif block.kind == "schedule":
            parts.append(_format_article_schedule(block))
    parts.append(f"原文：{article.original_url}")
    return "\n\n".join(parts)


def _open_notification(request: Request) -> None:
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except Exception as exc:
        raise NotificationError(str(exc)) from exc
