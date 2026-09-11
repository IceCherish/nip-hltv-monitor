from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from nip_monitor import translator
from nip_monitor.app import _schedule_overview_message
from nip_monitor.articles import Article, ArticleBlock, ArticleMatch, parse_article_html
from nip_monitor.models import Match, NewsItem, Result
from nip_monitor.notifiers import (
    NotificationError,
    OneBotNotifier,
    _article_as_text,
    _article_operations,
    _format_article_schedule,
    _format_published_at,
)
from nip_monitor.sources import (
    _enrich_missing_match_details,
    parse_match_details,
    parse_matches_html,
    parse_matches_page,
    parse_news,
    parse_results,
    parse_transfers,
    parse_upcoming_match_links,
    get_news,
)
from nip_monitor.translator import translate_article


class ParserTests(unittest.TestCase):
    def test_groq_prompt_uses_only_user_configured_terms(self):
        expected = {
            "force-buy=强起局",
            "anti-eco=反ECO局",
            "troll=犯病",
            "Mirage=荒漠迷城",
            "Ancient=远古遗迹",
            "Cache=叉车",
            "Anubis=阿努比斯",
            "Inferno=炼狱小镇",
            "Nuke=核子危机",
            "stavn=蛇",
            "xKacpersky=卡爹斯基",
            "sjuush=术士",
            "Krimbo=坤宝",
            "Ninjas in Pyjamas=废物NIP",
            "Major=Major",
            "IGL=指挥",
        }
        term_section = translator.GROQ_SYSTEM_PROMPT.split("只采用以下固定术语：", 1)[1].split("。不要自行添加", 1)[0]
        actual = {item.strip() for item in term_section.split("；")}
        self.assertEqual(actual, expected)

    def test_auto_translation_uses_groq_before_tencent_and_google(self):
        environment = {
            "TRANSLATE_ENABLED": "true",
            "TRANSLATE_PROVIDER": "auto",
            "GROQ_API_KEY": "test-groq-key",
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator, "_translate_groq", return_value="Qwen结果"
        ) as groq, patch.object(
            translator, "_translate_tencent", return_value="腾讯结果"
        ) as tencent, patch.object(
            translator, "_translate_google", return_value="谷歌结果"
        ) as google:
            self.assertEqual(translator.translate_text("hello", attempts=1), "Qwen结果")

        groq.assert_called_once()
        tencent.assert_not_called()
        google.assert_not_called()

    def test_auto_translation_falls_back_from_groq_to_tencent(self):
        environment = {
            "TRANSLATE_ENABLED": "true",
            "TRANSLATE_PROVIDER": "auto",
            "GROQ_API_KEY": "test-groq-key",
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator,
            "_translate_groq",
            side_effect=translator.TranslationError("rate limited"),
        ) as groq, patch.object(
            translator, "_translate_tencent", return_value="腾讯备用结果"
        ) as tencent, patch.object(
            translator, "_translate_google", return_value="谷歌备用结果"
        ) as google:
            self.assertEqual(
                translator.translate_text("hello", attempts=3),
                "腾讯备用结果",
            )

        groq.assert_called_once()
        tencent.assert_called_once()
        google.assert_not_called()

    def test_groq_translation_sends_configured_model_and_prompt(self):
        environment = {
            "GROQ_API_KEY": "test-groq-key",
            "GROQ_MODEL": "qwen/test-model",
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"choices":[{"message":{"content":"translated"}}]}'
        )
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator, "urlopen", return_value=response
        ) as mocked_urlopen:
            self.assertEqual(translator._translate_groq("hello", timeout=30), "translated")

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen/test-model")
        self.assertEqual(payload["messages"][1]["content"], "hello")
        self.assertIn("Bearer test-groq-key", request.headers.values())

    def test_auto_translation_uses_tencent_before_google(self):
        environment = {
            "TRANSLATE_ENABLED": "true",
            "TRANSLATE_PROVIDER": "auto",
            "GROQ_API_KEY": "",
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator, "_translate_tencent", return_value="腾讯结果"
        ) as tencent, patch.object(
            translator, "_translate_google", return_value="谷歌结果"
        ) as google:
            self.assertEqual(translator.translate_text("hello", attempts=1), "腾讯结果")

        tencent.assert_called_once()
        google.assert_not_called()

    def test_auto_translation_falls_back_to_google_on_tencent_api_error(self):
        environment = {
            "TRANSLATE_ENABLED": "true",
            "TRANSLATE_PROVIDER": "auto",
            "GROQ_API_KEY": "",
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator,
            "_translate_tencent",
            side_effect=translator.TranslationError("quota exceeded"),
        ) as tencent, patch.object(
            translator, "_translate_google", return_value="谷歌备用结果"
        ) as google:
            self.assertEqual(
                translator.translate_text("hello", attempts=3),
                "谷歌备用结果",
            )

        tencent.assert_called_once()
        google.assert_called_once()

    def test_tencent_translation_sends_configured_term_repository(self):
        environment = {
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
            "TENCENT_TERM_REPO_ID": "term-repo-id",
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"Response":{"TargetText":"translated"}}'
        )
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator, "urlopen", return_value=response
        ) as mocked_urlopen:
            self.assertEqual(
                translator._translate_tencent("hello", timeout=30),
                "translated",
            )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["TermRepoIDList"], ["term-repo-id"])
        self.assertEqual(payload["Source"], "en")

    def test_auto_translation_uses_tencent_safe_chunk_size(self):
        environment = {
            "TRANSLATE_ENABLED": "true",
            "TRANSLATE_PROVIDER": "auto",
            "GROQ_API_KEY": "",
            "TENCENT_SECRET_ID": "test-id",
            "TENCENT_SECRET_KEY": "test-key",
        }
        text = "a" * 2500
        with patch.dict(os.environ, environment, clear=False), patch.object(
            translator, "_translate_tencent", side_effect=lambda chunk, timeout: chunk
        ) as tencent, patch.object(translator, "_translate_google") as google:
            self.assertEqual(translator.translate_text(text, attempts=1), text)

        self.assertEqual(tencent.call_count, 2)
        self.assertTrue(
            all(len(call.args[0]) <= 1800 for call in tencent.call_args_list)
        )
        google.assert_not_called()

    def test_news_feed_uses_short_cache_tolerance(self):
        feed = """Markdown Content:
### [Fresh news](https://www.hltv.org/news/1/fresh-news)

Description.

Wed, 9 Sep 2026 12:42:00 GMT
"""
        with patch(
            "nip_monitor.sources.fetch_via_reader", return_value=feed
        ) as fetch:
            self.assertEqual(get_news()[0].news_id, "1")

        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["cache_tolerance"], 60)


    def test_article_image_failure_keeps_all_text(self):
        article = Article(
            title="Title",
            original_url="https://www.hltv.org/news/1/test",
            published_at="",
            blocks=(
                ArticleBlock(kind="text", text="before image"),
                ArticleBlock(kind="image", url="https://img.test/image.jpg"),
                ArticleBlock(kind="text", text="after image"),
            ),
        )
        self.assertEqual(
            [kind for kind, _ in _article_operations("news", article)],
            ["text", "image", "text"],
        )
        notifier = OneBotNotifier("http://127.0.0.1:3000", 123)
        with patch(
            "nip_monitor.notifiers._download_image",
            side_effect=NotificationError("download failed"),
        ), patch.object(notifier, "_post_message") as post_message:
            notifier.send_article("news", article)
        post_message.assert_called_once()
        message = post_message.call_args.args[0]
        self.assertTrue(all(part["type"] == "text" for part in message))
        rendered = "\n".join(part["data"]["text"] for part in message)
        self.assertIn("before image", rendered)
        self.assertIn("after image", rendered)
        self.assertIn("点此阅读原文", rendered)

    def test_article_text_and_image_use_one_onebot_message(self):
        article = Article(
            title="Title",
            original_url="https://www.hltv.org/news/1/test",
            published_at="",
            blocks=(
                ArticleBlock(kind="text", text="before image"),
                ArticleBlock(kind="image", url="https://img.test/image.jpg"),
                ArticleBlock(kind="text", text="after image"),
            ),
        )
        notifier = OneBotNotifier("http://127.0.0.1:3000", 123)
        with patch(
            "nip_monitor.notifiers._download_image",
            return_value=(b"fake-image", "image/jpeg"),
        ), patch.object(notifier, "_post_message") as post_message:
            notifier.send_article("news", article)

        post_message.assert_called_once()
        self.assertEqual(
            [part["type"] for part in post_message.call_args.args[0]],
            ["text", "image", "text"],
        )

    def test_published_time_is_shown_in_beijing_time(self):
        self.assertEqual(
            _format_published_at("Tue, 8 Sep 2026 09:13:00 GMT"),
            "2026年9月8日 17:13",
        )

    def test_parse_news(self):
        text = """Markdown Content:
### [NIP sign Krimbo](https://www.hltv.org/news/45424/nip-sign-krimbo)

The organization completed its lineup.

[https://www.hltv.org/news/45424/nip-sign-krimbo](https://www.hltv.org/news/45424/nip-sign-krimbo)

Mon, 7 Sep 2026 18:45:00 GMT
"""
        items = parse_news(text)
        self.assertEqual(items[0].news_id, "45424")
        self.assertEqual(items[0].description, "The organization completed its lineup.")

    def test_parse_schedule_and_details(self):
        team = """Upcoming matches for Ninjas in Pyjamas?
The next matches for Ninjas in Pyjamas will be against:
9 Sep vs. [SINNERS](https://www.hltv.org/matches/2397722/ninjas-in-pyjamas-vs-sinners-event)
## Coach of Ninjas in Pyjamas
"""
        matches = parse_upcoming_match_links(team)
        self.assertEqual(matches[0].opponent, "SINNERS")
        detail = """[Ninjas in Pyjamas](https://www.hltv.org/team/4411/ninjas-in-pyjamas)
14:00
9th of September 2026
[PGL Masters Bucharest](https://www.hltv.org/events/9241/pgl-masters-bucharest)
1d : 2h : 3m : 4s
"""
        full = parse_match_details(detail, matches[0])
        self.assertEqual(full.event, "PGL Masters Bucharest")
        self.assertEqual(full.start_at, "2026-09-09T14:00:00Z")

    def test_parse_transfers(self):
        text = """Transfers for Ninjas in Pyjamas
[**Krimbo**](https://www.hltv.org/player/19899/krimbo) transfers from [**BIG**](https://www.hltv.org/team/7532/big) to [**Ninjas in Pyjamas**](https://www.hltv.org/team/4411/ninjas-in-pyjamas)
Sep 2nd 2026
## Match stats for Ninjas in Pyjamas
"""
        items = parse_transfers(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].text, "Krimbo transfers from BIG to Ninjas in Pyjamas")

    def test_parse_results(self):
        text = """# Results
[Ninjas in Pyjamas ![Image 1: Ninjas in Pyjamas](nip.png)2 - 0![Image 2: HEROIC](heroic.png) HEROIC![Image 3: Stake Ranked Episode 2](event.png)Stake Ranked Episode 2 bo3](https://www.hltv.org/matches/123/nip-vs-heroic-event)
[Ninjas in Pyjamas ![Image 4: Ninjas in Pyjamas](nip.png)2 - 1![Image 5: magic](magic.png) magic![Image 6: Stake Ranked Episode 2](event.png)Stake Ranked Episode 2 bo3](https://www.hltv.org/matches/124/nip-vs-magic-event)
"""
        items = parse_results(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].opponent, "HEROIC")
        self.assertEqual(items[0].event, "Stake Ranked Episode 2")
        self.assertEqual((items[1].nip_score, items[1].opponent_score), (2, 1))

    def test_parse_global_matches_page(self):
        text = """Tuesday - 2026-09-08
[![Image 1: Other Event](logo) Other Event](https://www.hltv.org/matches/111/a-vs-b-other)
[12:00 bo3](https://www.hltv.org/matches/111/a-vs-b-other)[![Image 2: A](a) A ![Image 3: B](b) B](https://www.hltv.org/matches/111/a-vs-b-other)
Wednesday - 2026-09-09
[![Image 4: PGL Masters Bucharest](logo) PGL Masters Bucharest](https://www.hltv.org/matches/2397722/ninjas-in-pyjamas-vs-sinners-event)
[14:00 bo3](https://www.hltv.org/matches/2397722/ninjas-in-pyjamas-vs-sinners-event)[![Image 5: Ninjas in Pyjamas](nip)![Image 6: Ninjas in Pyjamas](nip2) Ninjas in Pyjamas ![Image 7: SINNERS](sin) SINNERS](https://www.hltv.org/matches/2397722/ninjas-in-pyjamas-vs-sinners-event)
Thursday - 2026-09-10
"""
        items = parse_matches_page(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].opponent, "SINNERS")
        self.assertEqual(items[0].event, "PGL Masters Bucharest")
        self.assertEqual(items[0].start_at, "2026-09-09T14:00:00Z")

    def test_parse_current_matches_html(self):
        text = """
<div class="match-wrapper" data-match-id="111" team1="1" team2="2" live="false">
  <div class="match-event" data-event-headline="Other Event"></div>
</div>
<div class="match-wrapper" data-match-id="2397722" team1="4411" team2="10577" live="false">
  <a href="/matches/2397722/ninjas-in-pyjamas-vs-sinners-event">
    <div class="match-event" data-event-headline="PGL Masters Bucharest"></div>
  </a>
  <div class="match-time" data-unix="1788962400000">14:00</div>
  <div class="match-teamname">Ninjas in Pyjamas</div>
  <div class="match-teamname">SINNERS</div>
</div>
"""
        items = parse_matches_html(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].opponent, "SINNERS")
        self.assertEqual(items[0].event, "PGL Masters Bucharest")
        self.assertEqual(items[0].start_at, "2026-09-09T14:00:00Z")

    def test_parse_matches_html_prefers_complete_duplicate(self):
        text = """
<div class="match-wrapper" data-match-id="2397722" team1="4411" team2="10577" live="false">
  <a href="/matches/2397722/ninjas-in-pyjamas-vs-sinners-event"></a>
  <div class="match-time" data-unix="1788962400000">14:00</div>
  <div class="match-teamname">Ninjas in Pyjamas</div>
  <div class="match-teamname">SINNERS</div>
</div>
<div class="match-wrapper" data-match-id="2397722" team1="4411" team2="10577" live="false">
  <a href="/matches/2397722/ninjas-in-pyjamas-vs-sinners-event">
    <div class="match-event" data-event-headline="PGL Masters Bucharest"></div>
  </a>
  <div class="match-time" data-unix="1788962400000">14:00</div>
  <div class="match-teamname">Ninjas in Pyjamas</div>
  <div class="match-teamname">SINNERS</div>
</div>
"""
        items = parse_matches_html(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].event, "PGL Masters Bucharest")

    def test_missing_match_event_is_filled_from_detail_page(self):
        basic = Match(
            match_id="2397722",
            opponent="SINNERS",
            url="https://www.hltv.org/matches/2397722/nip-vs-sinners-event",
            event="待定",
            start_at="2026-09-09T14:00:00Z",
        )
        detail = """[Ninjas in Pyjamas](https://www.hltv.org/team/4411/ninjas-in-pyjamas)
14:00
9th of September 2026
[PGL Masters Bucharest](https://www.hltv.org/events/9241/pgl-masters-bucharest)
"""
        with patch(
            "nip_monitor.sources.fetch_via_reader", return_value=detail
        ) as fetch:
            items = _enrich_missing_match_details([basic])
        self.assertEqual(items[0].event, "PGL Masters Bucharest")
        fetch.assert_called_once_with(basic.url)

    def test_parse_article_ignores_images_and_keeps_embedded_schedule(self):
        item = NewsItem("45014", "Semi-finals set", "", "https://www.hltv.org/news/45014/test")
        raw_html = """
<div class="newstext-con">
  <p>First paragraph with <strong>important</strong> words.</p>
  <img src="https://img-cdn.hltv.org/gallerypicture/editorial.jpg">
  <table class="event-matches-table">
    <tr class="event-header-cell"><th><a href="/events/1/test">Test Event</a></th></tr>
    <tr class="team-row">
      <td><span data-unix="1782568200000">27/06/2026</span></td>
      <td>
        <a class="team-name team-1">Sharks</a>
        <span data-unix="1782568200000">15:50</span>
        <a class="team-name team-2">Inner Circle</a>
      </td>
      <td><a href="/matches/2395397/test">Match</a></td>
    </tr>
  </table>
</div>
"""
        article = parse_article_html(raw_html, item)
        self.assertEqual(
            [block.kind for block in article.blocks],
            ["text", "image", "schedule"],
        )
        self.assertEqual(article.blocks[0].text, "First paragraph with important words.")
        self.assertEqual(
            article.blocks[1].url,
            "https://img-cdn.hltv.org/gallerypicture/editorial.jpg",
        )
        match = article.blocks[2].matches[0]
        self.assertEqual((match.team1, match.team2), ("Sharks", "Inner Circle"))
        self.assertEqual(match.event, "Test Event")
        self.assertEqual(match.start_at, "2026-06-27T13:50:00Z")

    def test_article_keeps_only_ten_text_paragraphs_and_footer(self):
        article = Article(
            title="Title",
            original_url="https://www.hltv.org/news/1/test",
            published_at="",
            blocks=tuple(
                ArticleBlock(kind="text", text=f"paragraph-{index}")
                for index in range(1, 13)
            ),
        )
        rendered = _article_as_text(article)
        self.assertIn("paragraph-10", rendered)
        self.assertNotIn("paragraph-11", rendered)
        self.assertTrue(
            rendered.endswith("🔗点此阅读原文：https://www.hltv.org/news/1/test")
        )

    def test_article_schedule_style(self):
        block = ArticleBlock(
            kind="schedule",
            matches=(
                ArticleMatch(
                    "Sharks",
                    "Echo",
                    "2026-06-28T13:30:00Z",
                    "数字远征超级德古拉N 第 1 季",
                ),
                ArticleMatch(
                    "Inner Circle",
                    "Acend",
                    "2026-06-28T17:00:00Z",
                    "数字远征超级德古拉N 第 1 季",
                ),
            ),
        )
        rendered = _format_article_schedule(block)
        self.assertEqual(rendered.count("🎮 赛事:"), 1)
        self.assertNotIn("\n\n⚔️", rendered)
        self.assertIn("⚔️ 28/06/2026 21:30 Sharks vs Echo", rendered)
        self.assertIn("⚔️ 29/06/2026 01:00 Inner Circle vs Acend", rendered)

    def test_article_translation_batches_title_text_and_event(self):
        article = Article(
            title="Title",
            original_url="https://www.hltv.org/news/1/test",
            published_at="",
            blocks=(
                ArticleBlock(kind="text", text="First paragraph"),
                ArticleBlock(
                    kind="schedule",
                    matches=(ArticleMatch("NIP", "FaZe", event="Big Event"),),
                ),
                ArticleBlock(kind="text", text="Second paragraph"),
            ),
        )

        def fake_translate(value):
            return (value.replace("Title", "标题")
                         .replace("First paragraph", "第一段")
                         .replace("Second paragraph", "第二段")
                         .replace("Big Event", "大型赛事"))

        with patch("nip_monitor.translator.translate_text", side_effect=fake_translate) as translate:
            translated = translate_article(article)

        self.assertEqual(translate.call_count, 1)
        self.assertEqual(translated.title, "标题")
        self.assertEqual(translated.blocks[0].text, "第一段")
        self.assertEqual(translated.blocks[1].matches[0].event, "大型赛事")
        self.assertEqual(translated.blocks[2].text, "第二段")

    def test_schedule_overview_groups_events_and_shows_latest_event_results(self):
        now = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)
        matches = [
            Match("1", "3DMAX", "u1", "XSE Pro League 2026", "2026-07-01T07:00:00Z"),
            Match("2", "HEROIC", "u2", "XSE Pro League 2026", "2026-07-02T07:00:00Z"),
            Match("3", "FaZe", "u3", "XPL", "2026-07-03T07:00:00Z"),
        ]
        results = [
            Result("r1", "HEROIC", 2, 0, "Stake Ranked Episode 2"),
            Result("r2", "Sharks", 2, 0, "Stake Ranked Episode 2"),
        ]
        title, rendered = _schedule_overview_message(matches, results, now)
        self.assertEqual(title, "🥷 【NIP 近期赛程预告】")
        self.assertEqual(rendered.count("🎮 赛事: XSE Pro League 2026"), 1)
        self.assertIn("⏰ 时间: 7/1 15:00", rendered)
        self.assertIn("⏳ 距离 3 天 4 小时", rendered)
        self.assertIn("🏆 【往期赛事回顾】", rendered)
        self.assertIn("📊 赛果: NIP 2 : 0 HEROIC", rendered)


if __name__ == "__main__":
    unittest.main()
