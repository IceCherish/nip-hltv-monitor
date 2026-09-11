from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .articles import get_article
from .models import Match, NewsItem, Result, Transfer
from .notifiers import NotificationError, configured_notifiers, deliver, deliver_article
from .sources import NIP_TEAM_URL, SourceError, get_news, get_nip_data
from .state import load_state, save_state
from .translator import TranslationError, translate_article

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_SCHEDULE_LOOKAHEAD_DAYS = 7
NEWS_MAX_AGE = timedelta(hours=1)
TRANSFER_MAX_AGE_DAYS = 1
QUIET_START_HOUR = 1
QUIET_END_HOUR = 7
MONTH_NUMBERS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


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


def _format_time(match: Match) -> str:
    if not match.start_datetime:
        return "开赛时间待定"
    local = match.start_datetime.astimezone(SHANGHAI)
    return local.strftime("%Y-%m-%d %H:%M")


def _news_label(item: NewsItem) -> str:
    return "📰【HLTV 新闻】"


def _latest_event_results(results: list[Result]) -> list[Result]:
    if not results:
        return []
    event = results[0].event
    return [result for result in results if result.event == event]


def _news_is_expired(item: NewsItem, now: datetime) -> bool:
    if not item.published_at:
        return False
    try:
        published_at = parsedate_to_datetime(item.published_at)
    except (TypeError, ValueError, OverflowError):
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) - published_at.astimezone(timezone.utc) > NEWS_MAX_AGE


def _is_quiet_hours(now: datetime) -> bool:
    """Return whether *now* falls in the Beijing-time no-message window."""
    local_hour = now.astimezone(SHANGHAI).hour
    return QUIET_START_HOUR <= local_hour < QUIET_END_HOUR


def _transfer_is_expired(item: Transfer, now: datetime) -> bool:
    match = re.fullmatch(
        r"([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th) (\d{4})",
        item.date.strip(),
    )
    if not match or match.group(1) not in MONTH_NUMBERS:
        return False
    transfer_date = datetime(
        int(match.group(3)),
        MONTH_NUMBERS[match.group(1)],
        int(match.group(2)),
        tzinfo=SHANGHAI,
    ).date()
    current_date = now.astimezone(SHANGHAI).date()
    return (current_date - transfer_date).days > TRANSFER_MAX_AGE_DAYS


def _upcoming_matches_within(
    matches: list[Match], now: datetime, days: int
) -> list[Match]:
    if days < 0:
        raise ValueError("SCHEDULE_LOOKAHEAD_DAYS 不能小于 0")
    deadline = now + timedelta(days=days)
    return [
        match
        for match in matches
        if match.start_datetime is not None
        and now <= match.start_datetime <= deadline
    ]


def _distance_to_match(match: Match, now: datetime) -> str:
    if not match.start_datetime:
        return "时间待定"
    seconds = max(0, int((match.start_datetime - now).total_seconds()))
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def _schedule_overview_message(
    matches: list[Match], recent_results: list[Result], now: datetime
) -> tuple[str, str]:
    title = "🥷 【NIP 近期赛程预告】"
    groups: dict[str, list[Match]] = {}
    ordered = sorted(
        matches,
        key=lambda match: match.start_datetime
        or datetime.max.replace(tzinfo=timezone.utc),
    )
    for match in ordered:
        groups.setdefault(match.event or "待定", []).append(match)

    sections: list[str] = []
    if groups:
        for event, event_matches in groups.items():
            lines = [f"🎮 赛事: {event}"]
            for index, match in enumerate(event_matches):
                if start := match.start_datetime:
                    local = start.astimezone(SHANGHAI)
                    display_time = f"{local.month}/{local.day} {local:%H:%M}"
                else:
                    display_time = "待定"
                if index:
                    lines.append("")
                lines.extend(
                    [
                        f"⚔️ 对阵: NIP vs {match.opponent}",
                        f"⏰ 时间: {display_time}",
                        f"⏳ 距离 {_distance_to_match(match, now)}",
                    ]
                )
            sections.append("\n".join(lines))
    else:
        sections.append("目前没有已公布的近期比赛。")

    review = ["---", "", "🏆 【往期赛事回顾】"]
    if recent_results:
        review.append("")
        review.append(f"🎮 赛事: {recent_results[0].event}")
        review.extend(
            f"📊 赛果: NIP {result.nip_score} : {result.opponent_score} {result.opponent}"
            for result in recent_results
        )
    else:
        review.extend(["", "目前没有可用的往期赛果。"])
    sections.append("\n".join(review))
    return title, "\n\n".join(sections)


def _transfer_message(item: Transfer) -> tuple[str, str]:
    return "🔁 NIP 阵容动态", f"{item.text}\n日期：{item.date}\n{NIP_TEAM_URL}"


def _reminder_message(match: Match, minutes: int) -> tuple[str, str]:
    if minutes <= 1:
        countdown = "🚨 比赛即将开始！"
    elif minutes <= 10:
        countdown = f"🚨 仅剩 {minutes} 分钟！"
    else:
        countdown = f"🔥 {minutes} 分钟后开战！"
    return (
        "🚨 【NIP 吃史警告】",
        f"{countdown}\n\n"
        f"🎮 赛事：{match.event if match.event != '待定' else '暂未公布'}\n"
        f"⚔️ 对阵：NIP vs {match.opponent}\n"
        f"⏰ 时间：{_format_time(match)}\n\n"
        f"🔗 比赛详情：\n{match.url}",
    )


def run_monitor(now: datetime | None = None, *, force_schedule: bool = False) -> int:
    fixed_now = now is not None
    now = now or datetime.now(timezone.utc)
    if _is_quiet_hours(now):
        print(
            "当前为北京时间 01:00-07:00 静默时段，本轮不抓取、不发送，"
            "也不更新去重或提醒记录。"
        )
        return 0

    reminder_minutes = int(os.getenv("REMINDER_MINUTES", "30"))
    schedule_lookahead_days = int(
        os.getenv("SCHEDULE_LOOKAHEAD_DAYS", str(DEFAULT_SCHEDULE_LOOKAHEAD_DAYS))
    )
    news_mode = os.getenv("NEWS_MODE", "all").strip().lower()
    if news_mode not in {"all", "off"}:
        raise ValueError("NEWS_MODE 只能是 all 或 off")

    notifiers = configured_notifiers()
    if not notifiers:
        print("提示：尚未配置通知渠道，本次消息只会显示在运行记录中。")

    news = [] if news_mode == "off" else get_news()
    matches, results, transfers = get_nip_data()
    schedule_matches = _upcoming_matches_within(
        matches, now, schedule_lookahead_days
    )
    recent_results = _latest_event_results(results)
    state = load_state(STATE_PATH)
    notifications: list[tuple[str, str]] = []
    news_to_send: list[NewsItem] = []
    eligible_news = [item for item in news if not _news_is_expired(item, now)]
    known_news = set(state["news_ids"])
    newly_expired_news = [
        item for item in news
        if item.news_id not in known_news and _news_is_expired(item, now)
    ]
    if newly_expired_news:
        print(
            "已跳过超过 1 小时的新闻："
            + ", ".join(item.news_id for item in newly_expired_news)
        )
    local_now = now.astimezone(SHANGHAI)
    today = local_now.date().isoformat()
    daily_schedule_due = (
        local_now.hour >= 10
        and state.get("last_daily_schedule_date", "") != today
    )
    schedule_needed = force_schedule or daily_schedule_due or not state["initialized"]

    if not state["initialized"]:
        news_to_send = list(reversed(eligible_news[:2]))
    else:
        news_to_send = [
            item for item in reversed(eligible_news) if item.news_id not in known_news
        ]

        known_transfers = set(state["transfer_ids"])
        unseen_transfers = [
            item for item in reversed(transfers)
            if item.transfer_id not in known_transfers
        ]
        expired_transfers = [
            item for item in unseen_transfers if _transfer_is_expired(item, now)
        ]
        if expired_transfers:
            print(
                "已跳过超过 1 天的阵容动态："
                + ", ".join(item.transfer_id for item in expired_transfers)
            )
        notifications.extend(
            _transfer_message(item)
            for item in unseen_transfers
            if not _transfer_is_expired(item, now)
        )

    schedule_sent = schedule_needed and bool(schedule_matches)
    if schedule_sent:
        notifications.append(
            _schedule_overview_message(schedule_matches, recent_results, now)
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

    failed_news_ids: set[str] = set()
    sent_article_count = 0
    for item in news_to_send:
        try:
            article = translate_article(get_article(item))
        except (SourceError, TranslationError) as exc:
            failed_news_ids.add(item.news_id)
            print(f"新闻 {item.news_id} 本轮暂缓，下次检查重试：{exc}")
            continue
        delivery_now = now if fixed_now else datetime.now(timezone.utc)
        if _news_is_expired(item, delivery_now):
            print(f"新闻 {item.news_id} 翻译完成时已超过 1 小时，已跳过。")
            continue
        deliver_article(_news_label(item), article, notifiers)
        sent_article_count += 1

    last_daily_schedule_date = state.get("last_daily_schedule_date", "")
    if schedule_sent:
        last_daily_schedule_date = today
    next_values = {
        "initialized": True,
        "news_ids": [
            item.news_id for item in news[:100]
            if item.news_id not in failed_news_ids
        ],
        "matches": {
            item.match_id: {**item.to_dict(), "signature": item.signature()} for item in matches
        },
        "transfer_ids": [item.transfer_id for item in transfers[:100]],
        "sent_reminders": sorted(sent_reminders),
        "recent_result_ids": [result.result_id for result in recent_results],
        "last_daily_schedule_date": last_daily_schedule_date,
    }
    if any(state.get(key) != value for key, value in next_values.items()):
        next_values["updated_at"] = now.isoformat().replace("+00:00", "Z")
    state.update(next_values)
    save_state(STATE_PATH, state)
    message_count = len(notifications) + sent_article_count
    print(
        f"完成：新闻 {len(news)} 条，未来比赛 {len(matches)} 场，"
        f"{schedule_lookahead_days} 天内比赛 {len(schedule_matches)} 场，"
        f"最近赛事赛果 {len(recent_results)} 场，"
        f"阵容动态 {len(transfers)} 条，新消息 {message_count} 条。"
    )
    return 0


def reset_news_state() -> None:
    state = load_state(STATE_PATH)
    state["initialized"] = False
    state["news_ids"] = []
    state["last_daily_schedule_date"] = ""
    state["updated_at"] = ""
    save_state(STATE_PATH, state)
    print("已清除首次运行和新闻去重记录；下次 monitor 会重新发送赛程和最新 2 条新闻。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="免费监控 HLTV 新闻和 NIP 赛程")
    parser.add_argument("--test-notification", action="store_true", help="只发送一条测试通知")
    parser.add_argument(
        "--test-rich-article",
        action="store_true",
        help="发送一篇含中文正文和文中赛程的真实测试新闻",
    )
    parser.add_argument(
        "--reset-news-state",
        action="store_true",
        help="清除首次运行和新闻去重记录，不发送消息",
    )
    args = parser.parse_args(argv)
    try:
        if args.reset_news_state:
            reset_news_state()
            return 0
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
        github_event = os.getenv("GITHUB_EVENT_NAME", "").lower()
        is_scheduled_github_check = (
            os.getenv("GITHUB_ACTIONS", "").lower() == "true"
            and (
                github_event == "schedule"
                or os.getenv("MONITOR_RUN_KIND", "").lower() == "scheduled"
            )
        )
        return run_monitor(force_schedule=not is_scheduled_github_check)
    except (SourceError, TranslationError, NotificationError, ValueError) as exc:
        print(f"运行失败：{exc}")
        return 1
