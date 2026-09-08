from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.request import Request, urlopen


class NotificationError(RuntimeError):
    pass


class Notifier:
    name = "notifier"

    def send(self, title: str, message: str) -> None:
        raise NotImplementedError


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


def _open_notification(request: Request) -> None:
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except Exception as exc:
        raise NotificationError(str(exc)) from exc
