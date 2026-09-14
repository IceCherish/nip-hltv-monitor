import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from nip_monitor import app
from nip_monitor.notifiers import NotificationError
from nip_monitor.sources import SourceError
from nip_monitor.x_posts import XPost, _signed_headers, parse_wanmei_x_posts


class XPostSourceTests(unittest.TestCase):
    def test_parse_public_feed_uses_reference_translation_and_ignores_comments(self):
        payload = {
            "code": 0,
            "result": [
                {
                    "postId": "1965000000000000000",
                    "originPublishTime": 1789098900000,
                    "content": (
                        "<b>NIP CS @NIPCS</b>"
                        "<p>参考翻译：拒绝 0-2</p>"
                        "<blockquote>We refuse to go 0-2<br>"
                        "<img src='https://img.example.test/post.jpg'></blockquote>"
                    ),
                    "videoInfo": {
                        "playInfoList": [
                            {"playURL": "https://video.example.test/post.mp4"}
                        ]
                    },
                    "hotComments": [
                        {
                            "content": (
                                "<img src='https://img.example.test/comment.jpg'>"
                            )
                        }
                    ],
                }
            ],
        }

        posts = parse_wanmei_x_posts(payload)

        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.display_name, "NIP CS")
        self.assertEqual(post.handle, "@NIPCS")
        self.assertEqual(post.text, "拒绝 0-2")
        self.assertEqual(post.original_text, "We refuse to go 0-2")
        self.assertEqual(
            post.images,
            ("https://img.example.test/post.jpg",),
        )
        self.assertNotIn("comment.jpg", post.images)
        self.assertEqual(post.video_url, "https://video.example.test/post.mp4")
        self.assertEqual(post.published_at, "2026-09-11T03:55:00Z")

    def test_request_signature_has_public_frontend_headers(self):
        with patch("nip_monitor.x_posts.time.time", return_value=1234567890), patch(
            "nip_monitor.x_posts.secrets.token_hex",
            return_value="0123456789abcdef0123456789abcdef",
        ):
            headers = _signed_headers(
                {
                    "hltvId": "4411",
                    "type": "1",
                    "pageNum": "1",
                    "pageSize": "20",
                }
            )

        self.assertEqual(headers["X-Timestamp"], "1234567890")
        self.assertEqual(headers["X-Nonce"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(len(headers["X-Signature"]), 64)


class XPostFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.now = datetime(2026, 9, 11, 4, 0, tzinfo=timezone.utc)
        self.posts = [
            XPost(
                "x-3",
                "NIP CS",
                "@NIPCS",
                "第三条（最新）",
                "third",
                "2026-09-11T03:55:00Z",
                ("https://img.example.test/3.jpg",),
            ),
            XPost(
                "x-2",
                "NIP CS",
                "@NIPCS",
                "第二条",
                "second",
                "2026-09-11T03:50:00Z",
            ),
            XPost(
                "x-1",
                "NIP CS",
                "@NIPCS",
                "第一条",
                "first",
                "2026-09-11T03:45:00Z",
            ),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _base_patches(self):
        return (
            patch.object(app, "STATE_PATH", self.state_path),
            patch.object(app, "configured_notifiers", return_value=[object()]),
            patch.object(app, "get_nip_data", return_value=([], [], [])),
            patch.object(app, "deliver"),
            patch.object(app, "deliver_article"),
        )

    def test_default_off_does_not_fetch_send_or_create_x_cache(self):
        patches = self._base_patches()
        with patch.dict(
            "os.environ",
            {"NEWS_MODE": "off", "NIP_X_ENABLED": "false"},
            clear=False,
        ), patches[0], patches[1], patches[2], patches[3], patches[4] as deliver_article, patch.object(
            app, "get_nip_x_posts"
        ) as get_x_posts:
            result = app.run_monitor(self.now)

        self.assertEqual(result, 0)
        get_x_posts.assert_not_called()
        deliver_article.assert_not_called()
        state = app.load_state(self.state_path)
        self.assertNotIn("x_initialized", state)
        self.assertNotIn("x_post_ids", state)

    def test_first_enable_sends_latest_two_oldest_first_and_saves_separate_cache(self):
        patches = self._base_patches()
        with patch.dict(
            "os.environ",
            {"NEWS_MODE": "off", "NIP_X_ENABLED": "true"},
            clear=False,
        ), patches[0], patches[1], patches[2], patches[3], patches[4] as deliver_article, patch.object(
            app, "get_nip_x_posts", return_value=self.posts
        ):
            result = app.run_monitor(self.now)

        self.assertEqual(result, 0)
        self.assertEqual(deliver_article.call_count, 2)
        self.assertEqual(
            [call.args[1].blocks[0].text for call in deliver_article.call_args_list],
            ["第二条", "第三条（最新）"],
        )
        self.assertTrue(
            all(
                call.args[0] == "🐦【NIP 官推】"
                for call in deliver_article.call_args_list
            )
        )
        state = app.load_state(self.state_path)
        self.assertTrue(state["x_initialized"])
        self.assertEqual(state["x_post_ids"], ["x-3", "x-2", "x-1"])

    def test_x_source_failure_does_not_stop_core_monitor_or_change_x_cache(self):
        patches = self._base_patches()
        with patch.dict(
            "os.environ",
            {"NEWS_MODE": "off", "NIP_X_ENABLED": "true"},
            clear=False,
        ), patches[0], patches[1], patches[2], patches[3], patches[4] as deliver_article, patch.object(
            app,
            "get_nip_x_posts",
            side_effect=SourceError("temporary source failure"),
        ):
            result = app.run_monitor(self.now)

        self.assertEqual(result, 0)
        deliver_article.assert_not_called()
        state = app.load_state(self.state_path)
        self.assertTrue(state["initialized"])
        self.assertNotIn("x_initialized", state)
        self.assertNotIn("x_post_ids", state)

    def test_x_delivery_failure_is_retried_without_failing_the_run(self):
        app.save_state(
            self.state_path,
            {
                "initialized": True,
                "news_ids": [],
                "x_initialized": True,
                "x_post_ids": [],
            },
        )
        patches = self._base_patches()
        with patch.dict(
            "os.environ",
            {"NEWS_MODE": "off", "NIP_X_ENABLED": "true"},
            clear=False,
        ), patches[0], patches[1], patches[2], patches[3], patches[4] as deliver_article, patch.object(
            app, "get_nip_x_posts", return_value=[self.posts[0]]
        ):
            deliver_article.side_effect = NotificationError("relay unavailable")
            result = app.run_monitor(self.now)

        self.assertEqual(result, 0)
        deliver_article.assert_called_once()
        self.assertNotIn(
            "x-3",
            app.load_state(self.state_path)["x_post_ids"],
        )


if __name__ == "__main__":
    unittest.main()
