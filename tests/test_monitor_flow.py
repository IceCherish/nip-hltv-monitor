import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from nip_monitor import app
from nip_monitor.models import Match, NewsItem, Result


class MonitorFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.news = [
            NewsItem(str(number), f"news {number}", "", f"https://example.test/{number}")
            for number in range(7, 0, -1)
        ]
        self.matches = [
            Match(
                "match-1",
                "HEROIC",
                "https://example.test/match-1",
                "XSE Pro League 2026",
                "2026-09-10T07:00:00Z",
            )
        ]
        self.results = [
            Result("result-1", "FaZe", 2, 1, "Stake Ranked Episode 2")
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _patch_monitor(self):
        return (
            patch.object(app, "STATE_PATH", self.state_path),
            patch.object(app, "configured_notifiers", return_value=[object()]),
            patch.object(app, "get_news", return_value=self.news),
            patch.object(
                app,
                "get_nip_data",
                return_value=(self.matches, self.results, []),
            ),
            patch.object(app, "get_article", side_effect=lambda item: item),
            patch.object(app, "translate_article", side_effect=lambda item: item),
            patch.object(app, "deliver"),
            patch.object(app, "deliver_article"),
        )

    def test_first_run_sends_latest_five_then_deduplicates(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc))

            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], "🥷 【NIP 近期赛程预告】")
            sent_ids = [call.args[1].news_id for call in deliver_article.call_args_list]
            self.assertEqual(sent_ids, ["3", "4", "5", "6", "7"])

            deliver.reset_mock()
            deliver_article.reset_mock()
            app.run_monitor(datetime(2026, 9, 8, 1, 1, tzinfo=timezone.utc))

            deliver.assert_not_called()
            deliver_article.assert_not_called()

    def test_daily_schedule_is_sent_once_after_ten_beijing_time(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc))
            deliver.reset_mock()
            deliver_article.reset_mock()

            app.run_monitor(datetime(2026, 9, 8, 2, 1, tzinfo=timezone.utc))
            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], "🥷 【NIP 近期赛程预告】")

            deliver.reset_mock()
            app.run_monitor(datetime(2026, 9, 8, 2, 7, tzinfo=timezone.utc))
            deliver.assert_not_called()
            deliver_article.assert_not_called()


if __name__ == "__main__":
    unittest.main()
