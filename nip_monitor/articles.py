from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import NewsItem
from .sources import HLTV_BASE, SourceError, fetch_via_reader


@dataclass(frozen=True)
class ArticleMatch:
    team1: str
    team2: str
    start_at: str = ""
    event: str = ""
    url: str = ""


@dataclass(frozen=True)
class ArticleBlock:
    kind: str
    text: str = ""
    url: str = ""
    matches: tuple[ArticleMatch, ...] = ()


@dataclass(frozen=True)
class Article:
    title: str
    original_url: str
    published_at: str
    blocks: tuple[ArticleBlock, ...]


class _ArticleHTMLParser(HTMLParser):
    TEXT_TAGS = {"p", "h2", "h3", "li", "blockquote"}
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ArticleBlock] = []
        self._text_depth = 0
        self._text_parts: list[str] = []
        self._in_table = False
        self._table_depth = 0
        self._table_event_parts: list[str] = []
        self._table_matches: list[ArticleMatch] = []
        self._row: dict[str, str] | None = None
        self._cell = ""
        self._image_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())

        if tag == "table" and "event-matches-table" in classes:
            self._in_table = True
            self._table_depth = 1
            self._table_event_parts = []
            self._table_matches = []
            return

        if self._in_table:
            if tag == "table":
                self._table_depth += 1
            if tag == "tr" and "team-row" in classes:
                self._row = {}
            if self._row is not None:
                if value := attrs_map.get("data-unix"):
                    self._row["unix"] = value
                if tag == "a" and "/matches/" in attrs_map.get("href", ""):
                    self._row["url"] = urljoin(HLTV_BASE, attrs_map["href"])
            if "event-header-cell" in classes:
                self._cell = "event"
            elif "team-1" in classes:
                self._cell = "team1"
            elif "team-2" in classes:
                self._cell = "team2"
            return

        if tag == "img":
            source = attrs_map.get("src", "")
            if "/gallerypicture/" in source:
                source = urljoin(HLTV_BASE, source)
                if source not in self._image_urls:
                    self._image_urls.add(source)
                    self.blocks.append(
                        ArticleBlock(
                            kind="image",
                            text=_clean_text(attrs_map.get("alt", "")),
                            url=source,
                        )
                    )
            return

        if self._text_depth:
            if tag == "br":
                self._text_parts.append("\n")
            elif tag not in self.VOID_TAGS:
                self._text_depth += 1
            return

        if tag in self.TEXT_TAGS:
            self._text_depth = 1
            self._text_parts = []
            return

    def handle_endtag(self, tag: str) -> None:
        if self._in_table:
            if tag == "a" and self._cell in {"event", "team1", "team2"}:
                self._cell = ""
            if tag in {"td", "th"}:
                self._cell = ""
            if tag == "tr" and self._row is not None:
                team1 = _clean_text(self._row.get("team1", ""))
                team2 = _clean_text(self._row.get("team2", ""))
                if team1 and team2:
                    timestamp = ""
                    if self._row.get("unix", "").isdigit():
                        timestamp = datetime.fromtimestamp(
                            int(self._row["unix"]) / 1000, tz=timezone.utc
                        ).isoformat().replace("+00:00", "Z")
                    self._table_matches.append(
                        ArticleMatch(
                            team1=team1,
                            team2=team2,
                            start_at=timestamp,
                            event=_clean_text(" ".join(self._table_event_parts)),
                            url=self._row.get("url", ""),
                        )
                    )
                self._row = None
            if tag == "table":
                self._table_depth -= 1
                if self._table_depth == 0:
                    if self._table_matches:
                        self.blocks.append(
                            ArticleBlock(kind="schedule", matches=tuple(self._table_matches))
                        )
                    self._in_table = False
                    self._cell = ""
            return

        if self._text_depth and tag not in self.VOID_TAGS:
            self._text_depth -= 1
            if self._text_depth == 0:
                text = _clean_text("".join(self._text_parts))
                if text:
                    self.blocks.append(ArticleBlock(kind="text", text=text))
                self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_table:
            if self._cell == "event":
                self._table_event_parts.append(data)
            elif self._row is not None and self._cell in {"team1", "team2"}:
                self._row[self._cell] = self._row.get(self._cell, "") + data
            return
        if self._text_depth:
            self._text_parts.append(data)


def _clean_text(value: str) -> str:
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


class _ArticleBodyExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.found = False
        self.complete = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if not self.depth:
            if self.found or tag != "div" or "newstext-con" not in classes:
                return
            self.depth = 1
            self.found = True
        elif tag == "div":
            self.depth += 1
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        self.parts.append(f"</{tag}>")
        if tag == "div":
            self.depth -= 1
            self.complete = self.depth == 0

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.depth:
            self.parts.append(f"&#{name};")


def parse_article_html(raw_html: str, item: NewsItem, *, require_body_container: bool = False) -> Article:
    extractor = _ArticleBodyExtractor()
    extractor.feed(raw_html)
    extractor.close()
    if extractor.found:
        if not extractor.complete:
            raise SourceError(f"新闻 {item.news_id} 的正文区域不完整")
        raw_html = "".join(extractor.parts)
    else:
        if any(marker in raw_html.lower() for marker in ("just a moment", "performing security verification", "cf-chl-")):
            raise SourceError(f"新闻 {item.news_id} 返回了安全验证页面，未获取到正文")
        if require_body_container:
            raise SourceError(f"新闻 {item.news_id} 缺少可识别的正文区域，可能被拦截或页面格式变化")
    parser = _ArticleHTMLParser()
    parser.feed(raw_html)
    parser.close()
    if not any(block.kind == "text" for block in parser.blocks):
        raise SourceError(f"没有从新闻 {item.news_id} 中解析到正文，页面格式可能已变化")
    return Article(item.title, item.url, item.published_at, tuple(parser.blocks))


def _get_article_direct(item: NewsItem, attempts: int = 2, timeout: int = 25) -> Article:
    request = Request(item.url, headers={
        "User-Agent": "nip-hltv-monitor/1.0 (personal, non-commercial monitor)",
        "Accept": "text/html",
    })
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                content = response.read().decode("utf-8", errors="replace")
            return parse_article_html(content, item, require_body_container=True)
        except SourceError:
            raise
        except HTTPError as exc:
            last_error = exc
            if exc.code in {401, 403, 404, 429}:
                break
        except (URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))
    raise SourceError(f"新闻 {item.news_id} 直连读取失败：{last_error}")


def get_article(item: NewsItem) -> Article:
    try:
        article = _get_article_direct(item)
    except SourceError as direct_error:
        print(f"新闻 {item.news_id} 正文直连失败，尝试阅读服务备用路径：{direct_error}")
        try:
            raw_html = fetch_via_reader(item.url, response_format="html", selector=".newstext-con")
            article = parse_article_html(raw_html, item, require_body_container=True)
        except SourceError as reader_error:
            raise SourceError(
                f"新闻 {item.news_id} 正文两条读取路径均失败；直连：{direct_error}；阅读服务：{reader_error}"
            ) from reader_error
        print(f"新闻 {item.news_id} 正文读取来源：阅读服务备用路径")
        return article
    print(f"新闻 {item.news_id} 正文读取来源：HLTV 直连")
    return article
