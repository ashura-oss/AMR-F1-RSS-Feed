"""Offline acceptance and adversarial tests for the RSS generator."""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from urllib.request import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import generate_feed as feed  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SITEMAP = (FIXTURES / "sitemap.xml").read_bytes()
ARTICLE_NEWER = (FIXTURES / "article_newer.html").read_bytes()
ARTICLE_OLDER = (FIXTURES / "article_older.html").read_bytes()
NEWER_URL = feed.ORIGIN + "/en-GB/news/story-newer"
OLDER_URL = feed.ORIGIN + "/en-GB/news/story-older"
IMAGE_URL = "https://assets.astonmartinf1.com/public/cms/story/image/photo.jpg?w=480&fit=fill"
MEDIA_CONTENT = "{http://search.yahoo.com/mrss/}content"


class FakeResponse:
    status = 200

    def __init__(self, body, final_url):
        self.body = body
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        return self.response


class SitemapTests(unittest.TestCase):
    def test_filters_non_news_and_deduplicates_using_latest_lastmod(self):
        urls = feed.parse_sitemap(SITEMAP)
        self.assertEqual(urls, [NEWER_URL, OLDER_URL])

    def test_rejects_foreign_and_ambiguous_origins_even_when_not_news(self):
        bad_locs = (
            "https://attacker.example/en-GB/news/x",
            "https://www.astonmartinf1.com.evil/en-GB/news/x",
            "https://www.astonmartinf1.com@attacker.example/en-GB/news/x",
            "http://www.astonmartinf1.com/en-GB/news/x",
            "https://www.astonmartinf1.com/en-GB/%2e%2e/private",
        )
        for location in bad_locs:
            with self.subTest(location=location):
                xml = (f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                       f"<url><loc>{location}</loc></url></urlset>").encode()
                with self.assertRaises(feed.FeedError):
                    feed.parse_sitemap(xml)

    def test_rejects_invalid_xml_entities_wrong_root_and_empty_news(self):
        cases = (
            b"<not-a-sitemap/>",
            b'<!DOCTYPE x [<!ENTITY e "boom">]><urlset/>',
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
        )
        for xml in cases:
            with self.subTest(xml=xml[:30]), self.assertRaises(feed.FeedError):
                feed.parse_sitemap(xml)

    def test_rejects_oversized_sitemap(self):
        with self.assertRaises(feed.FeedError):
            feed.parse_sitemap(b" " * (feed.SITEMAP_LIMIT + 1))


class ArticleTests(unittest.TestCase):
    def test_extracts_fixture_metadata_and_utc_day_precision(self):
        record = feed.extract_article(ARTICLE_NEWER, NEWER_URL)
        self.assertEqual(record["title"], "Team announces its latest update & plans")
        self.assertEqual(record["pub_date"].isoformat(), "2026-09-19T00:00:00+00:00")
        self.assertEqual(record["link"], NEWER_URL)
        self.assertEqual(record["image_url"], IMAGE_URL)

    def test_accepts_itemprop_time_element(self):
        record = feed.extract_article(ARTICLE_OLDER, OLDER_URL)
        self.assertEqual(record["pub_date"].isoformat(), "2026-09-20T00:00:00+00:00")
        self.assertIsNone(record["image_url"])

    def test_image_url_rejects_foreign_unsafe_and_unexpected_source_urls(self):
        bad = (
            "http://assets.astonmartinf1.com/public/cms/x/y/photo.jpg?w=1500&fit=fill",
            "https://assets.astonmartinf1.com.evil/public/cms/x/y/photo.jpg?w=1500&fit=fill",
            "https://assets.astonmartinf1.com@evil.example/public/cms/x/y/photo.jpg?w=1500&fit=fill",
            "https://assets.astonmartinf1.com/public/cms/../secret.jpg?w=1500&fit=fill",
            "https://assets.astonmartinf1.com/public/cms/x/y/photo.svg?w=1500&fit=fill",
            "https://assets.astonmartinf1.com/public/cms/x/y/photo.avif?w=1500&fit=fill",
            "https://assets.astonmartinf1.com/public/cms/x/y/photo.jpg?w=999999&fit=fill",
            "https://assets.astonmartinf1.com/public/cms/x/y/photo.jpg?w=1500&fit=fill&url=https://evil.example",
            "https://assets.astonmartinf1.com/public/cms/x/y/photo.jpg?w=1500&fit=fill#fragment",
        )
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaises(feed.FeedError):
                    feed.official_image_url(url)
                html = ARTICLE_OLDER.replace(b"</head>",
                                             ('<meta property="og:image" content="' + url.replace('&', '&amp;') + '"></head>').encode())
                self.assertIsNone(feed.extract_article(html, OLDER_URL)["image_url"])

    def test_rejects_missing_metadata_invalid_date_origin_and_oversize(self):
        cases = (
            (b"<html><title>not metadata</title></html>", NEWER_URL),
            (b'<meta property="og:title" content="x"><meta property="og:description" content="y"><meta itemprop="datePublished" content="not a date">', NEWER_URL),
            (ARTICLE_NEWER, "https://attacker.example/en-GB/news/story"),
            (b"x" * (feed.ARTICLE_LIMIT + 1), NEWER_URL),
        )
        for html, url in cases:
            with self.subTest(url=url, size=len(html)), self.assertRaises(feed.FeedError):
                feed.extract_article(html, url)

    def test_commercial_language_is_not_invented_or_exaggerated(self):
        record = feed.extract_article(ARTICLE_NEWER, NEWER_URL)
        self.assertEqual(record["title"], "Team announces its latest update & plans")
        self.assertEqual(record["description"], "The team has announced an update. No price, discount or availability is stated.")
        payload = feed.build_feed(fetcher=fixture_fetcher)
        item = next(item for item in ET.fromstring(payload).find("channel").findall("item")
                    if item.findtext("link") == NEWER_URL)
        self.assertEqual(item.findtext("title"), record["title"])
        self.assertEqual(item.findtext("description"), record["description"])
        combined = (item.findtext("title") + " " + item.findtext("description")).lower()
        self.assertNotIn("% off", combined)
        self.assertNotIn("save $", combined)
        self.assertNotIn("limited-time deal", combined)


def fixture_fetcher(url, *, article=False):
    if url == feed.SITEMAP and not article:
        return SITEMAP
    if article and url == NEWER_URL:
        return ARTICLE_NEWER
    if article and url == OLDER_URL:
        return ARTICLE_OLDER
    raise AssertionError(f"Unexpected offline fetch request: {url!r}, article={article!r}")


class FeedTests(unittest.TestCase):
    def test_build_feed_uses_injected_fetcher_and_publication_order(self):
        requested = []

        def fetcher(url, *, article=False):
            requested.append((url, article))
            return fixture_fetcher(url, article=article)

        payload = feed.build_feed(fetcher=fetcher)
        root = ET.fromstring(payload)
        items = root.find("channel").findall("item")
        self.assertEqual([item.findtext("link") for item in items], [OLDER_URL, NEWER_URL])
        self.assertEqual(requested, [(feed.SITEMAP, False), (NEWER_URL, True), (OLDER_URL, True)])
        self.assertEqual(root.get("version"), "2.0")
        self.assertEqual([item.findtext("guid") for item in items],
                         [OLDER_URL, NEWER_URL])
        self.assertTrue(all(item.find("guid").get("isPermaLink") == "true" for item in items))
        self.assertIsNone(items[0].find(MEDIA_CONTENT))
        self.assertEqual(items[1].find(MEDIA_CONTENT).get("url"), IMAGE_URL)
        self.assertEqual(items[1].find(MEDIA_CONTENT).get("medium"), "image")
        self.assertIn(b"xmlns:media=\"http://search.yahoo.com/mrss/\"", payload)

    def test_feed_validation_rejects_unofficial_image(self):
        payload = feed.build_feed(fetcher=fixture_fetcher)
        root = ET.fromstring(payload)
        media = root.find("channel").findall("item")[1].find(MEDIA_CONTENT)
        media.set("url", "https://attacker.example/photo.jpg")
        with self.assertRaises(feed.FeedError):
            feed.validate_feed(ET.tostring(root, encoding="utf-8"))

    def test_url_guid_deduplication_and_payload_are_stable(self):
        first = feed.build_feed(fetcher=fixture_fetcher)
        second = feed.build_feed(fetcher=fixture_fetcher)
        self.assertEqual(first, second)
        items = ET.fromstring(first).find("channel").findall("item")
        links = [item.findtext("link") for item in items]
        guids = [item.findtext("guid") for item in items]
        self.assertEqual(len(links), len(set(links)))
        self.assertEqual(guids, links)

    def test_fetch_failure_does_not_replace_existing_output(self):
        previous = b"previous known-good output\n"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "feed.xml"
            target.parent.mkdir()
            target.write_bytes(previous)

            def failing_fetcher(url, *, article=False):
                if url == feed.SITEMAP:
                    return SITEMAP
                raise feed.FeedError("offline injected article failure")

            with self.assertRaisesRegex(feed.FeedError, "offline injected"):
                payload = feed.build_feed(fetcher=failing_fetcher)
                feed.atomic_write(target, payload)
            self.assertEqual(target.read_bytes(), previous)

    def test_atomic_write_replaces_valid_feed_and_cleans_temp_files(self):
        payload = feed.build_feed(fetcher=fixture_fetcher)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deep" / "feed.xml"
            feed.atomic_write(target, payload)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_atomic_write_validation_failure_preserves_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "feed.xml"
            target.write_bytes(b"last good feed")
            with self.assertRaises(feed.FeedError):
                feed.atomic_write(target, b"<rss/>")
            self.assertEqual(target.read_bytes(), b"last good feed")

    def test_rejects_out_of_range_item_count_without_fetch(self):
        for count in (0, feed.MAX_ITEMS + 1):
            with self.subTest(count=count), self.assertRaises(feed.FeedError):
                feed.build_feed(fetcher=lambda *a, **k: self.fail("must not fetch"), max_items=count)


class RedirectTests(unittest.TestCase):
    def test_checked_redirect_handler_rejects_off_origin_and_non_article_redirects(self):
        req = Request(NEWER_URL)
        handler = feed.CheckedRedirects(article=True)
        for target in ("https://attacker.example/news", "http://www.astonmartinf1.com/en-GB/news/x",
                       "https://www.astonmartinf1.com/en-GB/drivers"):
            with self.subTest(target=target), self.assertRaises(feed.FeedError):
                handler.redirect_request(req, None, 302, "Found", {}, target)

    def test_checked_redirect_handler_allows_bounded_official_redirects(self):
        req = Request(NEWER_URL)
        handler = feed.CheckedRedirects(article=True)
        redirected = handler.redirect_request(req, None, 301, "Moved", {}, OLDER_URL)
        self.assertEqual(redirected.full_url, OLDER_URL)
        self.assertEqual(handler.count, 1)
        handler.count = 3
        with self.assertRaisesRegex(feed.FeedError, "redirect limit"):
            handler.redirect_request(req, None, 302, "Found", {}, OLDER_URL)

    def test_fetch_checks_start_url_request_headers_size_and_final_origin(self):
        with patch.object(feed, "build_opener") as build_opener:
            opener = FakeOpener(FakeResponse(b"ok", feed.SITEMAP))
            build_opener.return_value = opener
            self.assertEqual(feed.fetch(feed.SITEMAP), b"ok")
            request, timeout = opener.calls[0]
            self.assertEqual(request.get_header("User-agent"), feed.USER_AGENT)
            self.assertEqual(timeout, feed.TIMEOUT)
            with self.assertRaises(feed.FeedError):
                feed.fetch("https://attacker.example/sitemap.xml")

        with patch.object(feed, "build_opener") as build_opener:
            build_opener.return_value = FakeOpener(FakeResponse(b"ok", "https://attacker.example/"))
            with self.assertRaises(feed.FeedError):
                feed.fetch(feed.SITEMAP)

    def test_fetch_rejects_oversized_response(self):
        with patch.object(feed, "build_opener") as build_opener:
            build_opener.return_value = FakeOpener(
                FakeResponse(b"x" * (feed.SITEMAP_LIMIT + 1), feed.SITEMAP))
            with self.assertRaises(feed.FeedError):
                feed.fetch(feed.SITEMAP)


if __name__ == "__main__":
    unittest.main()
