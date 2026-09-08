from datetime import datetime, timezone
import unittest

from nip_monitor.app import _schedule_overview_message
from nip_monitor.articles import Article, ArticleBlock, ArticleMatch, parse_article_html
from nip_monitor.models import Match, NewsItem, Result
from nip_monitor.notifiers import _article_as_text, _format_article_schedule
from nip_monitor.sources import (
    parse_match_details,
    parse_matches_html,
    parse_matches_page,
    parse_news,
    parse_results,
    parse_transfers,
    parse_upcoming_match_links,
)


class ParserTests(unittest.TestCase):
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
        self.assertEqual([block.kind for block in article.blocks], ["text", "schedule"])
        self.assertEqual(article.blocks[0].text, "First paragraph with important words.")
        match = article.blocks[1].matches[0]
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
        self.assertIn("⏳ 距离开赛还有 3 天 4 小时", rendered)
        self.assertIn("🏆 【往期赛事回顾】", rendered)
        self.assertIn("📊 赛果: NIP 2 : 0 HEROIC", rendered)


if __name__ == "__main__":
    unittest.main()
