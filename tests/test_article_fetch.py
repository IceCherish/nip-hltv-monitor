import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from nip_monitor import articles
from nip_monitor.app import _article_mentions_nip
from nip_monitor.models import NewsItem
from nip_monitor.sources import SourceError


BODY = '<div class="newstext-con"><p>Real article &amp; text.</p><div><picture><img src="https://img-cdn.hltv.org/gallerypicture/body.jpg"></picture></div><p>Second paragraph.</p></div>'
FULL_PAGE = '<html><body><p>Navigation.</p><img src="https://img-cdn.hltv.org/gallerypicture/outside.jpg">' + BODY + '<div class="comments"><p>NIP comment only.</p><img src="https://img-cdn.hltv.org/gallerypicture/comment.jpg"></div></body></html>'


class ArticleFetchTests(unittest.TestCase):
    def setUp(self):
        self.item = NewsItem("1", "Article title", "", "https://www.hltv.org/news/1/test", "Thu, 17 Sep 2026 20:37:00 GMT")

    def response(self, html=FULL_PAGE):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = html.encode('utf-8')
        return response

    def test_full_page_keeps_only_body_text_and_images_in_order(self):
        article = articles.parse_article_html(FULL_PAGE, self.item, require_body_container=True)
        self.assertEqual([b.kind for b in article.blocks], ["text", "image", "text"])
        self.assertEqual(article.blocks[0].text, "Real article & text.")
        self.assertEqual(article.blocks[1].url, "https://img-cdn.hltv.org/gallerypicture/body.jpg")
        self.assertEqual(article.blocks[2].text, "Second paragraph.")
        self.assertEqual(article.published_at, self.item.published_at)

    def test_comment_nip_mention_does_not_make_article_relevant(self):
        article = articles.parse_article_html(FULL_PAGE, self.item, require_body_container=True)
        self.assertFalse(_article_mentions_nip(article))

    def test_verification_phrase_in_real_article_does_not_block_it(self):
        article = articles.parse_article_html('<div class="newstext-con"><p>He said: Just a moment.</p></div>', self.item, require_body_container=True)
        self.assertEqual(article.blocks[0].text, "He said: Just a moment.")

    def test_full_page_without_body_is_not_accepted(self):
        with self.assertRaisesRegex(SourceError, "正文区域"):
            articles.parse_article_html('<html><p>Verification or comments.</p></html>', self.item, require_body_container=True)

    def test_unclosed_body_is_not_accepted(self):
        with self.assertRaisesRegex(SourceError, "不完整"):
            articles.parse_article_html('<div class="newstext-con"><p>Partial.</p>', self.item, require_body_container=True)

    def test_verification_is_reported_explicitly(self):
        with self.assertRaisesRegex(SourceError, "安全验证"):
            articles.parse_article_html('<html><title>Just a moment...</title></html>', self.item, require_body_container=True)

    def test_direct_success_does_not_call_reader(self):
        with patch.object(articles, "urlopen", return_value=self.response()) as direct, patch.object(articles, "fetch_via_reader") as reader:
            article = articles.get_article(self.item)
        self.assertEqual(article.blocks[0].text, "Real article & text.")
        self.assertEqual(direct.call_args.args[0].full_url, self.item.url)
        reader.assert_not_called()

    def test_direct_403_uses_reader_without_repeating_direct_request(self):
        with patch.object(articles, "urlopen", side_effect=HTTPError(self.item.url, 403, "Forbidden", None, None)) as direct, patch.object(
            articles, "fetch_via_reader", return_value=FULL_PAGE
        ) as reader:
            article = articles.get_article(self.item)
        direct.assert_called_once()
        self.assertEqual(reader.call_args.kwargs["selector"], ".newstext-con")
        self.assertFalse(_article_mentions_nip(article))

    def test_direct_missing_container_uses_reader(self):
        with patch.object(articles, "urlopen", return_value=self.response('<html><p>Not news.</p></html>')), patch.object(
            articles, "fetch_via_reader", return_value=BODY
        ) as reader:
            article = articles.get_article(self.item)
        self.assertEqual(len(article.blocks), 3)
        reader.assert_called_once()

    def test_direct_temporary_error_retries_at_most_twice(self):
        with patch.object(articles, "urlopen", side_effect=URLError("network failure")) as direct, patch.object(
            articles.time, "sleep"
        ), patch.object(articles, "fetch_via_reader", return_value=BODY):
            articles.get_article(self.item)
        self.assertEqual(direct.call_count, 2)

    def test_both_paths_failed_preserve_both_reasons(self):
        with patch.object(articles, "urlopen", side_effect=HTTPError(self.item.url, 403, "Forbidden", None, None)), patch.object(
            articles, "fetch_via_reader", side_effect=SourceError("reader 422")
        ):
            with self.assertRaisesRegex(SourceError, "正文两条读取路径均失败.*403.*422"):
                articles.get_article(self.item)
