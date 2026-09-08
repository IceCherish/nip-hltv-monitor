from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .articles import Article


class TranslationError(RuntimeError):
    pass


GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TENCENT_TRANSLATE_URL = "https://tmt.tencentcloudapi.com"


def translate_text(text: str, attempts: int = 3, timeout: int = 30) -> str:
    if not text.strip() or os.getenv("TRANSLATE_ENABLED", "true").lower() in {"0", "false", "no"}:
        return text
    provider = os.getenv("TRANSLATE_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "google", "tencent"}:
        raise TranslationError("TRANSLATE_PROVIDER 只能是 auto、google 或 tencent")
    providers = (
        ["google", "tencent"]
        if provider == "auto" and _has_tencent_credentials()
        else ["google"]
        if provider == "auto"
        else [provider]
    )
    translated: list[str] = []
    for chunk in _chunks(text):
        errors: list[str] = []
        for current_provider in providers:
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    translated.append(
                        _translate_tencent(chunk, timeout)
                        if current_provider == "tencent"
                        else _translate_google(chunk, timeout)
                    )
                    break
                except (HTTPError, URLError, TimeoutError, ValueError, TypeError, IndexError) as exc:
                    last_error = exc
                    if attempt < attempts:
                        time.sleep(2 ** (attempt - 1))
            else:
                errors.append(f"{current_provider}: {last_error}")
                continue
            break
        else:
            raise TranslationError("All translation providers failed: " + "; ".join(errors))
    return "".join(translated).strip()


def _translate_google(text: str, timeout: int) -> str:
    query = urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t"})
    payload = urlencode({"q": text}).encode("utf-8")
    request = Request(
        f"{os.getenv('TRANSLATE_ENDPOINT', GOOGLE_TRANSLATE_URL)}?{query}",
        data=payload,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return "".join(part[0] for part in data[0] if part and part[0])


def _has_tencent_credentials() -> bool:
    return bool(
        os.getenv("TENCENT_SECRET_ID", "").strip()
        and os.getenv("TENCENT_SECRET_KEY", "").strip()
    )


def _translate_tencent(text: str, timeout: int) -> str:
    secret_id = os.getenv("TENCENT_SECRET_ID", "").strip()
    secret_key = os.getenv("TENCENT_SECRET_KEY", "").strip()
    if not secret_id or not secret_key:
        raise TranslationError("使用腾讯翻译时需要 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")

    host = "tmt.tencentcloudapi.com"
    service = "tmt"
    action = "TextTranslate"
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    payload = json.dumps(
        {"SourceText": text, "Source": "auto", "Target": "zh", "ProjectId": 0},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    content_type = "application/json; charset=utf-8"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload).hexdigest()
    canonical_request = (
        "POST\n/\n\n"
        f"{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    secret_date = hmac.new(
        ("TC3" + secret_key).encode("utf-8"), date.encode("utf-8"), hashlib.sha256
    ).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    request = Request(
        TENCENT_TRANSLATE_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2018-03-21",
            "X-TC-Region": os.getenv("TENCENT_REGION", "ap-beijing"),
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))["Response"]
    if error := data.get("Error"):
        raise TranslationError(f"腾讯翻译返回错误：{error.get('Code')}: {error.get('Message')}")
    return data["TargetText"]


def _chunks(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = max(remaining.rfind(mark, 0, limit) for mark in ("\n", ". ", "! ", "? ", "; "))
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def translate_article(article: Article) -> Article:
    segment_keys: list[tuple[str, object]] = [("title", 0)]
    segment_values = [article.title]
    text_count = 0
    for index, block in enumerate(article.blocks):
        if block.kind == "text":
            if text_count < 10:
                segment_keys.append(("text", index))
                segment_values.append(block.text)
            text_count += 1
        elif block.kind == "schedule":
            for match in block.matches:
                key = ("event", match.event)
                if match.event and key not in segment_keys:
                    segment_keys.append(key)
                    segment_values.append(match.event)

    translated_values = _translate_segments(segment_values)
    translated = dict(zip(segment_keys, translated_values))
    translated_blocks = []
    for index, block in enumerate(article.blocks):
        if block.kind == "text":
            translated_blocks.append(
                replace(block, text=translated.get(("text", index), block.text))
            )
        elif block.kind == "schedule":
            translated_matches = []
            for match in block.matches:
                translated_matches.append(
                    replace(
                        match,
                        event=translated.get(("event", match.event), match.event),
                    )
                )
            translated_blocks.append(replace(block, matches=tuple(translated_matches)))
        else:
            translated_blocks.append(block)
    return replace(
        article,
        title=translated[("title", 0)],
        blocks=tuple(translated_blocks),
    )


_SEGMENT_MARKER = re.compile(r"\[\[NIPHLTVSEGMENT(\d{4})\]\]")


def _translate_segments(values: list[str]) -> list[str]:
    if len(values) == 1:
        return [translate_text(values[0])]
    payload = "".join(
        f"\n\n[[NIPHLTVSEGMENT{index:04d}]]\n\n{value}"
        for index, value in enumerate(values)
    )
    translated = translate_text(payload)
    markers = list(_SEGMENT_MARKER.finditer(translated))
    if [int(marker.group(1)) for marker in markers] != list(range(len(values))):
        raise TranslationError("批量翻译返回的分段格式无法识别，已留到下次重试")
    return [
        translated[marker.end(): markers[index + 1].start() if index + 1 < len(markers) else None].strip()
        for index, marker in enumerate(markers)
    ]
