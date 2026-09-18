from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Match, Result
from .sources import HLTV_BASE, NIP_TEAM_URL, SourceError


def fetch_team_page(timeout: int = 25) -> str:
    request = Request(NIP_TEAM_URL, headers={
        "User-Agent": "nip-hltv-monitor/1.0 (personal, non-commercial monitor)",
        "Accept": "text/html",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SourceError(f"直连 NIP 主页失败：{exc}") from exc
    if "just a moment" in content.lower() or "cf-chl-" in content.lower():
        raise SourceError("直连 NIP 主页返回了安全验证页面")
    return content


class _TeamPageParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.box_depth = 0
        self.mode = ""
        self.event = ""
        self.seen: set[str] = set()
        self.tables: set[str] = set()
        self.errors: set[str] = set()
        self.matches: list[Match] = []
        self.results: list[Result] = []
        self.row: dict | None = None
        self.capture: tuple[str, str, str, list[str]] | None = None
        self.capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "div":
            if self.box_depth:
                self.box_depth += 1
            elif values.get("id") == "matchesBox":
                self.box_depth = 1
        if not self.box_depth:
            return
        if self.capture and tag not in self.VOID_TAGS:
            self.capture_depth += 1
        if tag == "h2":
            self._capture(tag, "heading")
        elif tag == "table" and self.mode:
            self.tables.add(self.mode)
        elif tag == "tr" and "team-row" in classes and self.mode:
            self.row = {"teams": [], "scores": [], "timestamp": "", "url": "", "live": False}
        elif tag == "a":
            href = values.get("href") or ""
            if self.row is not None:
                if "team-name" in classes:
                    self._capture(tag, "team", href)
                elif href.startswith("/matches/"):
                    self.row["url"] = HLTV_BASE + href
            elif href.startswith("/events/") and self.mode:
                self._capture(tag, "event")
        if self.row is not None:
            if "data-unix" in values:
                self.row["timestamp"] = values["data-unix"] or ""
            if classes & {"live", "match-live", "live-match"}:
                self.row["live"] = True
            if tag == "span" and "score" in classes:
                self._capture(tag, "score")

    def _capture(self, tag: str, kind: str, href: str = "") -> None:
        self.capture = (tag, kind, href, [])
        self.capture_depth = 1

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture[3].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.box_depth:
            return
        if self.capture and tag not in self.VOID_TAGS:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                _, kind, href, parts = self.capture
                text = " ".join("".join(parts).split())
                self.capture = None
                if kind == "heading":
                    self.mode = "matches" if text.startswith("Upcoming matches for") else "results" if text.startswith("Recent results for") else ""
                    if self.mode:
                        self.seen.add(self.mode)
                    self.event = ""
                elif kind == "event":
                    self.event = text
                elif self.row is not None:
                    self.row["teams" if kind == "team" else "scores"].append((href, text) if kind == "team" else text)
        if tag == "tr" and self.row is not None:
            self._finish_row()
        if tag == "div":
            self.box_depth -= 1

    def _finish_row(self) -> None:
        row, self.row = self.row, None
        assert row is not None
        link = re.fullmatch(r"https://www\.hltv\.org/matches/(\d+)/[^\s]+", row["url"])
        teams = row["teams"]
        nip_indexes = [i for i, (href, _) in enumerate(teams) if re.match(r"/team/4411(?:/|$)", href)]
        if not link or len(teams) != 2 or len(nip_indexes) != 1 or not self.event:
            self.errors.add(self.mode)
            return
        nip_index = nip_indexes[0]
        opponent = teams[1 - nip_index][1]
        if self.mode == "matches":
            if row["live"] or any(score.isdigit() for score in row["scores"]):
                return
            try:
                timestamp = int(row["timestamp"])
                start_at = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat().replace("+00:00", "Z") if timestamp > 0 else ""
            except (ValueError, TypeError, OSError, OverflowError):
                self.errors.add(self.mode)
                return
            self.matches.append(Match(link.group(1), opponent, row["url"], self.event, start_at))
        else:
            scores = row["scores"]
            if len(scores) != 2 or not all(score.isdigit() for score in scores):
                self.errors.add(self.mode)
                return
            self.results.append(Result(link.group(1), opponent, int(scores[nip_index]), int(scores[1 - nip_index]), self.event, row["url"]))


def parse_team_page(html: str) -> tuple[list[Match] | None, list[Result] | None]:
    parser = _TeamPageParser()
    parser.feed(html)
    parser.close()
    if not parser.seen:
        raise SourceError("NIP 主页没有可识别的赛程或赛果区域，可能被拦截或页面格式变化")
    values = []
    for name, items in (("matches", parser.matches), ("results", parser.results)):
        if name not in parser.seen or name not in parser.tables or name in parser.errors:
            print(f"NIP 主页 {name} 区域缺失或数据不完整，尝试原有读取路径。")
            values.append(None)
        else:
            values.append(list({item.match_id if name == "matches" else item.result_id: item for item in items}.values()))
    return values[0], values[1]
