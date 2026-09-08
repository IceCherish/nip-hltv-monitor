from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Match, NewsItem, Transfer

HLTV_BASE = "https://www.hltv.org"
HLTV_NEWS_RSS = f"{HLTV_BASE}/rss/news"
NIP_TEAM_URL = f"{HLTV_BASE}/team/4411/ninjas-in-pyjamas"
HLTV_MATCHES_URL = f"{HLTV_BASE}/matches"
HLTV_TRANSFERS_URL = f"{HLTV_BASE}/transfers"
READER_BASE = "https://r.jina.ai/"


class SourceError(RuntimeError):
    """Raised when fresh monitoring data cannot be retrieved or parsed."""


def fetch_via_reader(url: str, attempts: int = 3, timeout: int = 60) -> str:
    request = Request(
        READER_BASE + url,
        headers={
            "User-Agent": "nip-hltv-monitor/1.0 (personal, non-commercial monitor)",
            "Accept": "text/plain",
            "X-Respond-With": "markdown",
            "X-Cache-Tolerance": "300",
            "X-Timeout": "45",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            if "Markdown Content:" not in text:
                raise SourceError(f"阅读服务返回了无法识别的内容：{url}")
            return text
        except (HTTPError, URLError, TimeoutError, SourceError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise SourceError(f"读取失败（已重试 {attempts} 次）：{url}: {last_error}")


def parse_news(markdown: str) -> list[NewsItem]:
    header = re.compile(
        r"^### \[(?P<title>.+?)\]\((?P<url>https://www\.hltv\.org/news/(?P<id>\d+)/[^)]+)\)\s*$",
        re.MULTILINE,
    )
    matches = list(header.finditer(markdown))
    items: list[NewsItem] = []
    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block = markdown[match.end():block_end].strip()
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        description = next(
            (
                line
                for line in lines
                if not line.startswith("[")
                and not re.match(r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}", line)
            ),
            "",
        )
        published = next(
            (line for line in lines if re.match(r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}", line)),
            "",
        )
        items.append(
            NewsItem(
                news_id=match.group("id"),
                title=match.group("title").strip(),
                description=description,
                url=match.group("url"),
                published_at=published,
            )
        )
    if not items:
        raise SourceError("没有从 HLTV 官方 RSS 中解析到新闻，页面格式可能已变化")
    return items


def parse_upcoming_match_links(markdown: str) -> list[Match]:
    start = markdown.find("Upcoming matches for Ninjas in Pyjamas?")
    if start < 0:
        raise SourceError("没有找到 NIP 的赛程区域，页面格式可能已变化")
    end = markdown.find("## Coach of Ninjas in Pyjamas", start)
    section = markdown[start:end if end >= 0 else start + 20_000]
    pattern = re.compile(
        r"(?P<date>\d{1,2}\s+[A-Z][a-z]{2})\s+vs\.\s+"
        r"\[(?P<opponent>[^]]+)\]\("
        r"(?P<url>https://www\.hltv\.org/matches/(?P<id>\d+)/[^)]+)\)"
    )
    seen: set[str] = set()
    matches: list[Match] = []
    for found in pattern.finditer(section):
        match_id = found.group("id")
        if match_id in seen:
            continue
        seen.add(match_id)
        matches.append(
            Match(
                match_id=match_id,
                opponent=found.group("opponent").strip(),
                url=found.group("url"),
            )
        )
    return matches


def parse_match_details(markdown: str, basic: Match) -> Match:
    detail_pattern = re.compile(
        r"Ninjas in Pyjamas\]\(https://www\.hltv\.org/team/4411/ninjas-in-pyjamas\)"
        r"\s+(?P<time>\d{1,2}:\d{2})\s+"
        r"(?P<date>\d{1,2}(?:st|nd|rd|th) of [A-Z][a-z]+ \d{4})\s+"
        r"\[(?P<event>[^]]+)\]\(https://www\.hltv\.org/events/\d+/[^)]+",
        re.DOTALL,
    )
    found = detail_pattern.search(markdown)
    if not found:
        raise SourceError(f"无法解析比赛 {basic.match_id} 的开赛时间")

    clean_date = re.sub(r"(\d{1,2})(?:st|nd|rd|th)", r"\1", found.group("date"), count=1)
    parsed = datetime.strptime(
        f"{clean_date} {found.group('time')}", "%d of %B %Y %H:%M"
    ).replace(tzinfo=timezone.utc)
    return Match(
        match_id=basic.match_id,
        opponent=basic.opponent,
        url=basic.url,
        event=found.group("event").strip(),
        start_at=parsed.isoformat().replace("+00:00", "Z"),
    )


def parse_matches_page(markdown: str) -> list[Match]:
    day_pattern = re.compile(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+-\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})\s*$",
        re.MULTILINE,
    )
    days = list(day_pattern.finditer(markdown))
    if not days:
        raise SourceError("没有从比赛列表中找到日期分段，页面格式可能已变化")

    match_url_pattern = re.compile(
        r"(?P<url>https://www\.hltv\.org/matches/(?P<id>\d+)/(?P<slug>[^)\s\"]+))"
    )
    time_pattern = re.compile(r"\[(?P<time>\d{1,2}:\d{2})\s+bo\d+\]")
    found_matches: list[Match] = []
    seen_ids: set[str] = set()

    for index, day in enumerate(days):
        section_end = days[index + 1].start() if index + 1 < len(days) else len(markdown)
        lines = [line.strip() for line in markdown[day.end():section_end].splitlines() if line.strip()]
        for line_index, event_line in enumerate(lines[:-1]):
            url_match = match_url_pattern.search(event_line)
            if not url_match or url_match.group("id") in seen_ids:
                continue
            details_line = lines[line_index + 1]
            clock = time_pattern.search(details_line)
            if not clock or url_match.group("url") not in details_line:
                continue
            if "Ninjas in Pyjamas" not in details_line:
                continue

            opponent = _opponent_from_match_line(details_line)
            if not opponent:
                raise SourceError(f"无法从比赛 {url_match.group('id')} 中识别 NIP 的对手")
            event = _strip_markdown_links(re.sub(r"!\[[^]]*\]\([^)]+\)", "", event_line))
            event = event.strip(" []") or "待定"
            starts = datetime.strptime(
                f"{day.group('date')} {clock.group('time')}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
            seen_ids.add(url_match.group("id"))
            found_matches.append(
                Match(
                    match_id=url_match.group("id"),
                    opponent=opponent,
                    url=url_match.group("url"),
                    event=event,
                    start_at=starts.isoformat().replace("+00:00", "Z"),
                )
            )
    return found_matches


def _opponent_from_match_line(line: str) -> str:
    image_names = re.findall(r"!\[Image \d+: ([^]]+)\]", line)
    ignored = {"Ninjas in Pyjamas", "Teamlogo"}
    for name in image_names:
        if name not in ignored:
            return name.strip()

    plain = _strip_markdown_links(re.sub(r"!\[[^]]*\]\([^)]+\)", "", line))
    plain = re.sub(r"^\d{1,2}:\d{2}\s+bo\d+", "", plain).strip()
    plain = plain.replace("Ninjas in Pyjamas", "").strip()
    return plain


def parse_transfers(markdown: str) -> list[Transfer]:
    section = markdown
    date_pattern = re.compile(r"^(?P<date>[A-Z][a-z]{2} \d{1,2}(?:st|nd|rd|th) \d{4})$", re.MULTILINE)
    dates = list(date_pattern.finditer(section))
    transfers: list[Transfer] = []
    for index, date_match in enumerate(dates):
        block_start = dates[index - 1].end() if index else 0
        prior = section[block_start:date_match.start()]
        lines = [line.strip() for line in prior.splitlines() if line.strip()]
        action = next(
            (
                line for line in reversed(lines)
                if "Ninjas in Pyjamas" in line
                and any(word in line.lower() for word in ("transfers", "joins", "benched", "parts ways", "moved"))
            ),
            "",
        )
        if not action:
            continue
        plain = _strip_markdown_links(action)
        digest = hashlib.sha256(f"{plain}|{date_match.group('date')}".encode()).hexdigest()[:16]
        if all(item.transfer_id != digest for item in transfers):
            transfers.append(Transfer(digest, plain, date_match.group("date")))
    return transfers


def _strip_markdown_links(text: str) -> str:
    text = re.sub(r"!\[[^]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[\*\*(.*?)\*\*\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def get_news() -> list[NewsItem]:
    return parse_news(fetch_via_reader(HLTV_NEWS_RSS))


def get_nip_data() -> tuple[list[Match], list[Transfer]]:
    matches_page = fetch_via_reader(HLTV_MATCHES_URL)
    transfers_page = fetch_via_reader(HLTV_TRANSFERS_URL)
    return parse_matches_page(matches_page), parse_transfers(transfers_page)
