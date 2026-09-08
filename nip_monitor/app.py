from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Match, NewsItem, Transfer
from .notifiers import NotificationError, configured_notifiers, deliver
from .sources import NIP_TEAM_URL, SourceError, get_news, get_nip_data
from .state import load_state, save_state

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
NIP_KEYWORDS = ("nip", "ninjas in pyjamas")


def _is_nip_news(item: NewsItem) -> bool:
    haystack = f"{item.title} {item.description}".lower()
    return any(keyword in haystack for keyword in NIP_KEYWORDS)


def _format_time(match: Match) -> str:
    if not match.start_datetime:
        return "开赛时间待定"
    local = match.start_datetime.astimezone(SHANGHAI)
    return local.strftime("北京时间 %Y-%m-%d %H:%M")


def _news_message(item: NewsItem) -> tuple[str, str]:
    label = "🥷 NIP 新闻" if _is_nip_news(item) else "📰 HLTV 新闻"
    body = item.title
    if item.description:
        body += f"\n{item.description}"
    body += f"\n{item.url}"
    return label, body


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
    lines = ["监控已经启动。", "", "当前功能：HLTV 新闻、NIP 赛程/动态、赛前提醒。", f"提醒时间：开赛前约 {reminder_minutes} 分钟。"]
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

    if not state["initialized"]:
        notifications.append(_startup_message(matches, reminder_minutes))
    else:
        known_news = set(state["news_ids"])
        new_news = [item for item in reversed(news) if item.news_id not in known_news]
        if news_mode == "nip":
            new_news = [item for item in new_news if _is_nip_news(item)]
        notifications.extend(_news_message(item) for item in new_news)

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

    for title, message in notifications:
        deliver(title, message, notifiers)

    state.update(
        {
            "initialized": True,
            "news_ids": [item.news_id for item in news[:100]],
            "matches": {
                item.match_id: {**item.to_dict(), "signature": item.signature()} for item in matches
            },
            "transfer_ids": [item.transfer_id for item in transfers[:100]],
            "sent_reminders": sorted(sent_reminders),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }
    )
    save_state(STATE_PATH, state)
    print(f"完成：新闻 {len(news)} 条，未来比赛 {len(matches)} 场，阵容动态 {len(transfers)} 条，新消息 {len(notifications)} 条。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="免费监控 HLTV 新闻和 NIP 赛程")
    parser.add_argument("--test-notification", action="store_true", help="只发送一条测试通知")
    args = parser.parse_args(argv)
    try:
        if args.test_notification:
            notifiers = configured_notifiers()
            if not notifiers:
                print("没有配置通知渠道。请先按 README 添加一个免费通知渠道。")
                return 2
            deliver("✅ NIP 监控测试", "如果你看到这条消息，通知配置成功。", notifiers)
            return 0
        return run_monitor()
    except (SourceError, NotificationError, ValueError) as exc:
        print(f"运行失败：{exc}")
        return 1
