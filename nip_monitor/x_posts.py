from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from .sources import SourceError

WANMEI_X_POSTS_URL = (
    "https://esports.wanmei.com/apg/eventcenter/csgo/post/getPlayerOrTeamPost"
)
WANMEI_WEB_SIGNING_KEY = (
    "828a59393861babc4a27e23f87fc77ab8dc9347dc6550c5e7e51e9b1af2de812"
)
NIP_HLTV_ID = "4411"
NIP_X_PROFILE_URL = "https://x.com/NIPCS"


@dataclass(frozen=True)
class XPost:
    post_id: str
    display_name: str
    handle: str
    text: str
    original_text: str
    published_at: str
    images: tuple[str, ...] = ()
    video_url: str = ""


def _clean_text(value: str) -> str:
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


class _PostContentParser(HTMLParser):
    _VOID_TAGS = {"br", "img", "meta", "link", "input", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.author = ""
        self.translations: list[str] = []
        self.originals: list[str] = []
        self.images: list[str] = []
        self._capture_tag = ""
        self._capture_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        if self._capture_tag:
            if tag == "br":
                self._parts.append("\n")
            elif tag == "img":
                if self._capture_tag == "blockquote":
                    source = attrs_map.get("src", "").strip()
                    if source and source not in self.images:
                        self.images.append(source)
            elif tag not in self._VOID_TAGS:
                self._capture_depth += 1
            return
        if tag in {"b", "p", "blockquote"}:
            self._capture_tag = tag
            self._capture_depth = 1
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._capture_tag or tag in self._VOID_TAGS:
            return
        self._capture_depth -= 1
        if self._capture_depth:
            return
        value = _clean_text("".join(self._parts))
        if self._capture_tag == "b" and value:
            self.author = value
        elif self._capture_tag == "p" and value.startswith("参考翻译："):
            translated = value.removeprefix("参考翻译：").strip()
            if translated:
                self.translations.append(translated)
        elif self._capture_tag == "blockquote" and value:
            self.originals.append(value)
        self._capture_tag = ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_tag:
            self._parts.append(data)


def _author_parts(author: str) -> tuple[str, str]:
    match = re.search(r"(@[^\s]+)\s*$", author)
    if not match:
        return author.strip() or "NIP CS", "@NIPCS"
    handle = match.group(1)
    display_name = author[: match.start()].strip() or "NIP CS"
    return display_name, handle


def _video_url(item: dict) -> str:
    video_info = item.get("videoInfo") or {}
    play_infos = video_info.get("playInfoList") or []
    if not isinstance(play_infos, list) or not play_infos:
        return ""
    first = play_infos[0] if isinstance(play_infos[0], dict) else {}
    return str(first.get("playURL") or "").strip()


def parse_wanmei_x_posts(payload: dict) -> list[XPost]:
    if payload.get("code") != 0:
        raise SourceError(
            f"完美电竞 NIP X 快讯接口返回错误：{payload.get('code')}: "
            f"{payload.get('message', '')}"
        )
    raw_items = payload.get("result")
    if not isinstance(raw_items, list):
        raise SourceError("完美电竞 NIP X 快讯接口没有返回列表")
    posts: list[XPost] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("postId") or "").strip()
        timestamp_ms = item.get("originPublishTime")
        content = str(item.get("content") or "")
        if not post_id or not content:
            continue
        try:
            published = datetime.fromtimestamp(
                int(timestamp_ms) / 1000, tz=timezone.utc
            )
        except (TypeError, ValueError, OSError):
            continue
        parser = _PostContentParser()
        parser.feed(content)
        translated = "\n".join(parser.translations).strip()
        original = "\n".join(parser.originals).strip()
        text = translated or original
        if not text:
            continue
        display_name, handle = _author_parts(parser.author)
        posts.append(
            XPost(
                post_id=post_id,
                display_name=display_name,
                handle=handle,
                text=text,
                original_text=original,
                published_at=published.isoformat().replace("+00:00", "Z"),
                images=tuple(parser.images),
                video_url=_video_url(item),
            )
        )
    posts.sort(key=lambda post: post.published_at, reverse=True)
    return posts


def _signed_headers(params: dict[str, str]) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical_values = {
        **{key: [str(value)] for key, value in params.items()},
        "timestamp": [timestamp],
        "nonce": [nonce],
    }
    pairs: list[str] = []
    for key in sorted(canonical_values):
        for value in sorted(canonical_values[key]):
            pairs.append(
                f"{quote_plus(key, safe='')}={quote_plus(value, safe='')}"
            )
    signature = hmac.new(
        WANMEI_WEB_SIGNING_KEY.encode("utf-8"),
        "&".join(pairs).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "Origin": "https://data.wanmei.com",
        "Referer": "https://data.wanmei.com/",
        "User-Agent": "nip-hltv-monitor/1.0 (personal, non-commercial monitor)",
        "Accept": "application/json",
    }


def get_nip_x_posts(
    attempts: int = 3, timeout: int = 30, page_size: int = 20
) -> list[XPost]:
    params = {
        "hltvId": NIP_HLTV_ID,
        "type": "1",
        "pageNum": "1",
        "pageSize": str(page_size),
    }
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(
                f"{WANMEI_X_POSTS_URL}?{urlencode(params)}",
                headers=_signed_headers(params),
            )
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return parse_wanmei_x_posts(payload)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            TypeError,
            SourceError,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise SourceError(
        f"读取完美电竞 NIP X 快讯失败（已重试 {attempts} 次）：{last_error}"
    )

