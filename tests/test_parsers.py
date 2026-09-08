from datetime import datetime, timezone
import unittest

from nip_monitor.app import _is_nip_news
from nip_monitor.models import Match, NewsItem
from nip_monitor.sources import (
    parse_match_details,
    parse_matches_page,
    parse_news,
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
        self.assertTrue(_is_nip_news(items[0]))

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


if __name__ == "__main__":
    unittest.main()
