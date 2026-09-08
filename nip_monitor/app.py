from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .articles import Article, get_article
from .models import Match, NewsItem, Transfer
from .notifiers import NotificationError, configured_notifiers, deliver, deliver_article
from .sources import NIP_TEAM_URL, SourceError, get_news, get_nip_data
from .state import load_state, save_state
from .translator import TranslationError, translate_article

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
NIP_KEYWORDS = ("nip", "ninjas in pyjamas")


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_env(ROOT / ".env")


def _is_nip_news(item: NewsItem) -> bool:
    haystack = f"{item.title} {item.description}".lower()
    return any(keyword in haystack for keyword in NIP_KEYWORDS)


def _format_time(match: Match) -> str:
    if not match.start_datetime:
        return "开赛时间待定"
    local = match.start_datetime.astimezone(SHANGHAI)
    return local.strftime("北京时间 %Y-%m-%d %H:%M")


def _news_label(item: NewsItem) -> str:
    return "🥷【NIP 新闻】" if _is_nip_news(item) else "📰【HLTV 新闻】"


def _match_message(match: Match, changed: bool = False) -> tuple[str, str]:
    title = "🔄 NIP 赛程变更" if changed else "🎮 NIP 新赛程"
    body = (
        f"Ninjas in Pyjamas vs {match.opponent}\n"
        f"赛事：{match.event}\n"
        f"时间：{_format_time(match)}\n"
        f"{match.url}"
    )
    return title, body


def _transfer_message(item: Transfer) -> tuple[str, str]:
    return "🔁 NIP 阵容动态", f"{item.text}\n日期：{item.date}\n{NIP_TEAM_URL}"


def _reminder_message(match: Match, minutes: int) -> tuple[str, str]:
    return (
        "⏰ NIP 比赛即将开始",
        f"距离开赛约 {minutes} 分钟\n"
        f"Ninjas in Pyjamas vs {match.opponent}\n"
        f"赛事：{match.event}\n"
        f"时间：{_format_time(match)}\n"
        f"{match.url}",
    )


def _startup_message(matches: list[Match], reminder_minutes: int) -> tuple[str, str]:
    lines = [
        "监控已经启动。",
        "",
        "当前功能：HLTV 新闻中文全文/正文配图/文中赛程、NIP 赛程/转会动态、赛前提醒。",
        f"提醒时间：开赛前约 {reminder_minutes} 分钟。",
    ]
    if matches:
        lines.extend(["", "最近一场：", f"NIP vs {matches[0].opponent}", _format_time(matches[0]), matches[0].url])
    else:
        lines.extend(["", "目前没有已公布的 NIP 比赛。"]) 
    return "✅ NIP 监控已启动", "\n".join(lines)


def run_monitor(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    reminder_minutes = int(os.getenv("REMINDER_MINUTES", "30"))
    news_mode = os.getenv("NEWS_MODE", "all").strip().lower()
    if news_mode not in {"all", "nip", "off"}:
        raise ValueError("NEWS_MODE 只能是 all、nip 或 off")

    notifiers = configured_notifiers()
    if not notifiers:
        print("提示：尚未配置通知渠道，本次消息只会显示在运行记录中。")

    news = [] if news_mode == "off" else get_news()
    matches, transfers = get_nip_data()
    state = load_state(STATE_PATH)
    notifications: list[tuple[str, str]] = []
    article_notifications: list[tuple[str, Article]] = []

    if not state["initialized"]:
        notifications.append(_startup_message(matches, reminder_minutes))
    else:
        known_news = set(state["news_ids"])
        new_news = [item for item in reversed(news) if item.news_id not in known_news]
        if news_mode == "nip":
            new_news = [item for item in new_news if _is_nip_news(item)]
        for item in new_news:
            article_notifications.append(
                (_news_label(item), translate_article(get_article(item)))
            )

        known_matches = state["matches"]
        for match in matches:
            previous = known_matches.get(match.match_id)
            if previous is None:
                notifications.append(_match_message(match))
            elif previous.get("signature") != match.signature():
                notifications.append(_match_message(match, changed=True))

        known_transfers = set(state["transfer_ids"])
        notifications.extend(
            _transfer_message(item) for item in reversed(transfers) if item.transfer_id not in known_transfers
        )

    sent_reminders = set(state["sent_reminders"])
    for match in matches:
        start = match.start_datetime
        if not start or match.match_id in sent_reminders:
            continue
        minutes = int((start - now).total_seconds() // 60)
        if 0 <= minutes <= reminder_minutes:
            notifications.append(_reminder_message(match, minutes))
            sent_reminders.add(match.match_id)

    for label, article in article_notifications:
        deliver_article(label, article, notifiers)
    for title, message in notifications:
        deliver(title, message, notifiers)

    next_values = {
        "initialized": True,
        "news_ids": [item.news_id for item in news[:100]],
        "matches": {
            item.match_id: {**item.to_dict(), "signature": item.signature()} for item in matches
        },
        "transfer_ids": [item.transfer_id for item in transfers[:100]],
        "sent_reminders": sorted(sent_reminders),
    }
    if any(state.get(key) != value for key, value in next_values.items()):
        next_values["updated_at"] = now.isoformat().replace("+00:00", "Z")
    state.update(next_values)
    save_state(STATE_PATH, state)
    message_count = len(notifications) + len(article_notifications)
    print(f"完成：新闻 {len(news)} 条，未来比赛 {len(matches)} 场，阵容动态 {len(transfers)} 条，新消息 {message_count} 条。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="免费监控 HLTV 新闻和 NIP 赛程")
    parser.add_argument("--test-notification", action="store_true", help="只发送一条测试通知")
    parser.add_argument(
        "--test-rich-article",
        action="store_true",
        help="发送一篇含中文正文、图片和文中赛程的真实测试新闻",
    )
    args = parser.parse_args(argv)
    try:
        if args.test_notification or args.test_rich_article:
            notifiers = configured_notifiers()
            if not notifiers:
                print("没有配置通知渠道。请先按 README 添加一个免费通知渠道。")
                return 2
            if args.test_rich_article:
                demo = NewsItem(
                    news_id="45014",
                    title="Super DraculaN semi-finals set",
                    description="",
                    url="https://www.hltv.org/news/45014/super-draculan-semi-finals-set",
                    published_at="",
                )
                deliver_article(
                    "🧪【HLTV 富媒体测试】",
                    translate_article(get_article(demo)),
                    notifiers,
                )
                return 0
            deliver("✅ NIP 监控测试", "如果你看到这条消息，通知配置成功。", notifiers)
            return 0
        return run_monitor()
    except (SourceError, TranslationError, NotificationError, ValueError) as exc:
        print(f"运行失败：{exc}")
        return 1
