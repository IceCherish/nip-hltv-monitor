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
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
GROQ_SYSTEM_PROMPT = (
    "你是专业的 CS2 电竞新闻翻译引擎。请把用户提供的英文完整翻译成自然、通顺、准确的简体中文。"
    "不得总结、删减、解释或添加原文不存在的信息。选手 ID、战队名、比分、地图名、日期和时间必须保持准确。"
    "必须原样保留 [[NIPHLTVSEGMENT0000]] 这类分段标记以及原有段落结构。"
    "只采用以下固定术语：force-buy=强起局；anti-eco=反ECO局；troll=犯病；"
    "Mirage=荒漠迷城；Ancient=远古遗迹；Cache=叉车；Anubis=阿努比斯；"
    "Inferno=炼狱小镇；Nuke=核子危机；stavn=蛇；xKacpersky=卡爹斯基；"
    "sjuush=术士；Krimbo=坤宝；Ninjas in Pyjamas=废物NIP；Major=Major；IGL=指挥。"
    "不要自行添加其他固定术语。只输出翻译结果。"
)


def translate_text(text: str, attempts: int = 3, timeout: int = 30) -> str:
    if not text.strip() or os.getenv("TRANSLATE_ENABLED", "true").lower() in {"0", "false", "no"}:
        return text
    provider = os.getenv("TRANSLATE_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "groq", "google", "tencent"}:
        raise TranslationError("TRANSLATE_PROVIDER 只能是 auto、groq、tencent 或 google")
    if provider == "auto":
        providers = []
        if _has_groq_credentials():
            providers.append("groq")
        if _has_tencent_credentials():
            providers.append("tencent")
        providers.append("google")
    else:
        providers = [provider]
    chunk_limit = 1800 if "tencent" in providers else 3500
    translated: list[str] = []
    used_providers: list[str] = []
    for chunk in _chunks(text, limit=chunk_limit):
        errors: list[str] = []
        translated_chunk: str | None = None
        for current_provider in providers:
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    if current_provider == "groq":
                        translated_chunk = _translate_groq(chunk, timeout)
                    elif current_provider == "tencent":
                        translated_chunk = _translate_tencent(chunk, timeout)
                    else:
                        translated_chunk = _translate_google(chunk, timeout)
                    break
                except (
                    HTTPError,
                    URLError,
                    TimeoutError,
                    ValueError,
                    TypeError,
                    IndexError,
                    TranslationError,
                ) as exc:
                    last_error = exc
                    if isinstance(exc, TranslationError) or (
                        isinstance(exc, HTTPError) and exc.code == 429
                    ):
                        break
                    if attempt < attempts:
                        time.sleep(2 ** (attempt - 1))
            if translated_chunk is not None:
                if current_provider not in used_providers:
                    used_providers.append(current_provider)
                break
            errors.append(f"{current_provider}: {last_error}")
            if current_provider != providers[-1]:
                print(
                    f"{_provider_label(current_provider)}翻译失败，"
                    f"本段改用{_provider_label(providers[providers.index(current_provider) + 1])}："
                    f"{last_error}"
                )
        if translated_chunk is None:
            raise TranslationError("All translation providers failed: " + "; ".join(errors))
        translated.append(translated_chunk)
    print("本次翻译服务：" + "、".join(_provider_label(item) for item in used_providers))
    return "".join(translated).strip()


def _provider_label(provider: str) -> str:
    if provider == "groq":
        return "Groq Qwen"
    return "腾讯云" if provider == "tencent" else "Google"


def _has_groq_credentials() -> bool:
    return bool(os.getenv("GROQ_API_KEY", "").strip())


def _translate_groq(text: str, timeout: int) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise TranslationError("使用 Groq 翻译时需要 GROQ_API_KEY")
    payload = json.dumps(
        {
            "model": os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL).strip()
            or GROQ_DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": GROQ_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_completion_tokens": 4096,
            "stream": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        os.getenv("GROQ_API_URL", GROQ_CHAT_URL),
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "nip-hltv-monitor/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    result = data["choices"][0]["message"]["content"].strip()
    if not result:
        raise TranslationError("Groq Qwen 返回了空翻译")
    return result


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
    term_repo_id = os.getenv("TENCENT_TERM_REPO_ID", "").strip()
    payload_data: dict[str, object] = {
        "SourceText": text,
        "Source": "en" if term_repo_id else "auto",
        "Target": "zh",
        "ProjectId": 0,
    }
    if term_repo_id:
        payload_data["TermRepoIDList"] = [term_repo_id]
    payload = json.dumps(
        payload_data,
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
