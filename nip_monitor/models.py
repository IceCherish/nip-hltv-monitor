from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    title: str
    description: str
    url: str
    published_at: str = ""


@dataclass(frozen=True)
class Match:
    match_id: str
    opponent: str
    url: str
    event: str = "待定"
    start_at: str = ""

    def signature(self) -> str:
        return "|".join((self.opponent, self.event, self.start_at))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def start_datetime(self) -> datetime | None:
        if not self.start_at:
            return None
        return datetime.fromisoformat(self.start_at.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Transfer:
    transfer_id: str
    text: str
    date: str

