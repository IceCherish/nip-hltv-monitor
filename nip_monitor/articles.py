from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

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

        if tag == "img":
            src = html.unescape(attrs_map.get("src", ""))
            if "gallerypicture" in src:
                url = urljoin(HLTV_BASE, src)
                if not any(block.kind == "image" and block.url == url for block in self.blocks):
                    self.blocks.append(ArticleBlock(kind="image", url=url))

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


def parse_article_html(raw_html: str, item: NewsItem) -> Article:
    parser = _ArticleHTMLParser()
    parser.feed(raw_html)
    if not any(block.kind == "text" for block in parser.blocks):
        raise SourceError(f"没有从新闻 {item.news_id} 中解析到正文，页面格式可能已变化")
    return Article(item.title, item.url, item.published_at, tuple(parser.blocks))


def get_article(item: NewsItem) -> Article:
    raw_html = fetch_via_reader(
        item.url,
        response_format="html",
        selector=".newstext-con",
    )
    return parse_article_html(raw_html, item)
