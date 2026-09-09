import tempfile
import unittest
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from nip_monitor import app
from nip_monitor.models import Match, NewsItem, Result, Transfer


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

    def test_reset_news_state_keeps_reminder_history(self):
        app.save_state(
            self.state_path,
            {
                "initialized": True,
                "news_ids": ["45480"],
                "sent_reminders": ["match-1"],
                "last_daily_schedule_date": "2026-09-08",
            },
        )
        with patch.object(app, "STATE_PATH", self.state_path):
            app.reset_news_state()
        state = app.load_state(self.state_path)
        self.assertFalse(state["initialized"])
        self.assertEqual(state["news_ids"], [])
        self.assertEqual(state["sent_reminders"], ["match-1"])

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

    def test_first_run_sends_latest_two_then_deduplicates(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc))

            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], "🥷 【NIP 近期赛程预告】")
            sent_ids = [call.args[1].news_id for call in deliver_article.call_args_list]
            self.assertEqual(sent_ids, ["6", "7"])

            deliver.reset_mock()
            deliver_article.reset_mock()
            app.run_monitor(datetime(2026, 9, 8, 1, 1, tzinfo=timezone.utc))

            deliver.assert_not_called()
            deliver_article.assert_not_called()

    def test_translation_failure_does_not_block_schedule_and_is_retried(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as translate, patches[6] as deliver, patches[7] as deliver_article:
            translate.side_effect = lambda item: (
                (_ for _ in ()).throw(app.TranslationError("HTTP 429"))
                if item.news_id == "6"
                else item
            )
            app.run_monitor(datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc))

            self.assertEqual(deliver.call_count, 1)
            sent_ids = [call.args[1].news_id for call in deliver_article.call_args_list]
            self.assertEqual(sent_ids, ["7"])
            self.assertNotIn("6", app.load_state(self.state_path)["news_ids"])

            deliver.reset_mock()
            deliver_article.reset_mock()
            translate.side_effect = lambda item: item
            app.run_monitor(datetime(2026, 9, 8, 1, 6, tzinfo=timezone.utc))

            deliver.assert_not_called()
            retried_ids = [call.args[1].news_id for call in deliver_article.call_args_list]
            self.assertEqual(retried_ids, ["6"])

    def test_failed_news_is_not_retried_after_one_hour(self):
        self.news = [
            NewsItem(
                "fresh-then-stale",
                "news",
                "",
                "https://example.test/news",
                "Tue, 8 Sep 2026 09:30:00 GMT",
            )
        ]
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as translate, patches[6], patches[7] as deliver_article:
            translate.side_effect = app.TranslationError("providers unavailable")
            app.run_monitor(datetime(2026, 9, 8, 9, 45, tzinfo=timezone.utc))

            self.assertEqual(translate.call_count, 1)
            deliver_article.assert_not_called()
            self.assertNotIn(
                "fresh-then-stale",
                app.load_state(self.state_path)["news_ids"],
            )

            translate.reset_mock()
            app.run_monitor(datetime(2026, 9, 8, 10, 31, tzinfo=timezone.utc))

            translate.assert_not_called()
            self.assertIn(
                "fresh-then-stale",
                app.load_state(self.state_path)["news_ids"],
            )

    def test_transfer_older_than_one_day_is_skipped_and_remembered(self):
        app.save_state(
            self.state_path,
            {
                "initialized": True,
                "last_daily_schedule_date": "2026-09-09",
                "news_ids": [item.news_id for item in self.news],
            },
        )
        old_transfer = Transfer(
            "changed-old-transfer",
            "Krimbo joins Ninjas in Pyjamas on loan from BIG",
            "Sep 1st 2026",
        )
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3] as nip_data, patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            nip_data.return_value = (self.matches, self.results, [old_transfer])
            app.run_monitor(datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc))

        deliver.assert_not_called()
        deliver_article.assert_not_called()
        self.assertIn(
            "changed-old-transfer",
            app.load_state(self.state_path)["transfer_ids"],
        )

    def test_yesterdays_transfer_can_still_be_sent(self):
        app.save_state(
            self.state_path,
            {
                "initialized": True,
                "last_daily_schedule_date": "2026-09-09",
                "news_ids": [item.news_id for item in self.news],
            },
        )
        recent_transfer = Transfer(
            "recent-transfer",
            "Player joins Ninjas in Pyjamas",
            "Sep 8th 2026",
        )
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3] as nip_data, patches[4], patches[5], patches[6] as deliver, patches[7]:
            nip_data.return_value = (self.matches, self.results, [recent_transfer])
            app.run_monitor(datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc))

        self.assertEqual(deliver.call_count, 1)
        self.assertEqual(deliver.call_args.args[0], "🔁 NIP 阵容动态")

    def test_daily_schedule_is_sent_once_after_ten_beijing_time(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc))
            deliver.reset_mock()
            deliver_article.reset_mock()

            app.run_monitor(datetime(2026, 9, 8, 2, 1, tzinfo=timezone.utc))
            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], "🥷 【NIP 近期赛程预告】")

            deliver.reset_mock()
            app.run_monitor(datetime(2026, 9, 8, 2, 7, tzinfo=timezone.utc))
            deliver.assert_not_called()
            deliver_article.assert_not_called()

    def test_startup_before_ten_marks_schedule_sent_for_the_day(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(
                datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
                force_schedule=True,
            )
            self.assertEqual(deliver.call_count, 1)
            deliver.reset_mock()
            deliver_article.reset_mock()

            app.run_monitor(datetime(2026, 9, 8, 2, 1, tzinfo=timezone.utc))
            deliver.assert_not_called()
            deliver_article.assert_not_called()

    def test_match_or_result_change_does_not_push_schedule(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3] as nip_data, patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            app.run_monitor(datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc))
            deliver.reset_mock()
            deliver_article.reset_mock()

            changed_matches = [
                Match(
                    "match-2",
                    "SINNERS",
                    "https://example.test/match-2",
                    "Another Event",
                    "2026-09-11T07:00:00Z",
                )
            ]
            changed_results = [
                Result("result-2", "fnatic", 1, 2, "Another Result Event")
            ]
            nip_data.return_value = (changed_matches, changed_results, [])
            app.run_monitor(datetime(2026, 9, 8, 3, 6, tzinfo=timezone.utc))

            deliver.assert_not_called()
            deliver_article.assert_not_called()

    def test_schedule_waits_until_a_match_is_within_seven_days(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3] as nip_data, patches[4], patches[5], patches[6] as deliver, patches[7] as deliver_article:
            far_match = Match(
                "match-far",
                "Vitality",
                "https://example.test/match-far",
                "Future Event",
                "2026-09-20T07:00:00Z",
            )
            nip_data.return_value = ([far_match], self.results, [])
            app.run_monitor(datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc))

            deliver.assert_not_called()
            self.assertEqual(
                app.load_state(self.state_path)["last_daily_schedule_date"], ""
            )

            deliver.reset_mock()
            deliver_article.reset_mock()
            nip_data.return_value = (self.matches, self.results, [])
            app.run_monitor(datetime(2026, 9, 8, 3, 15, tzinfo=timezone.utc))

            self.assertEqual(deliver.call_count, 1)
            self.assertIn("NIP vs HEROIC", deliver.call_args.args[1])
            self.assertEqual(
                app.load_state(self.state_path)["last_daily_schedule_date"],
                "2026-09-08",
            )

    def test_schedule_message_excludes_matches_beyond_seven_days(self):
        patches = self._patch_monitor()
        with patches[0], patches[1], patches[2], patches[3] as nip_data, patches[4], patches[5], patches[6] as deliver, patches[7]:
            far_match = Match(
                "match-far",
                "Vitality",
                "https://example.test/match-far",
                "Future Event",
                "2026-09-20T07:00:00Z",
            )
            nip_data.return_value = (self.matches + [far_match], self.results, [])
            app.run_monitor(datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc))

            self.assertEqual(deliver.call_count, 1)
            rendered = deliver.call_args.args[1]
            self.assertIn("NIP vs HEROIC", rendered)
            self.assertNotIn("NIP vs Vitality", rendered)

    def test_external_dispatch_is_treated_as_scheduled_check(self):
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "MONITOR_RUN_KIND": "scheduled",
        }
        with patch.dict(os.environ, environment, clear=False), patch.object(
            app, "run_monitor", return_value=0
        ) as run_monitor:
            self.assertEqual(app.main([]), 0)
        run_monitor.assert_called_once_with(force_schedule=False)


if __name__ == "__main__":
    unittest.main()
