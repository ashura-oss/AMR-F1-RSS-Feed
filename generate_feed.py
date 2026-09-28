#!/usr/bin/env python
"""Bounded, official-source-only Aston Martin F1 RSS 2.0 generator."""

import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

ORIGIN = "https://www.astonmartinf1.com"
SITEMAP = ORIGIN + "/sitemap.xml"
USER_AGENT = "AMR-F1-RSS/1.0 (official-news feed generator; local operator)"
SITEMAP_LIMIT = 1_000_000
ARTICLE_LIMIT = 2_000_000
MAX_ITEMS = 30
TIMEOUT = 12
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("media", MEDIA_NS)


class FeedError(Exception):
    """An untrusted source or unavailable required input prevents publication."""


def official_url(url, *, article=False):
    """Validate a URL without permitting off-origin, ambiguous or encoded paths."""
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or p.netloc != "www.astonmartinf1.com"
                or p.query or p.fragment or not p.path.startswith("/")):
            raise FeedError("noncanonical official URL: " + url[:200])
        decoded = unquote(p.path)
        if (re.search(r"[\\\x00-\x1f\x7f]", decoded)
                or any(seg in (".", "..") for seg in decoded.split("/"))
                or "%" in decoded or "//" in decoded):
            raise FeedError("unsafe URL path: " + url[:200])
        if article and not decoded.startswith("/en-GB/news/"):
            raise FeedError("outside official news: " + url[:200])
        return url
    except ValueError as exc:
        raise FeedError("invalid URL") from exc


def official_image_url(url):
    """Keep only a bounded official CMS image URL and request a card-sized variant."""
    try:
        if (not url or len(url) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", url)):
            raise FeedError("invalid image URL")
        parsed = urlsplit(url)
        path = parsed.path
        if (parsed.scheme != "https" or parsed.netloc != "assets.astonmartinf1.com"
                or parsed.fragment or not path.startswith("/public/cms/")
                or "//" in path or "%" in path or "\\" in path
                or any(part in (".", "..") for part in path.split("/"))
                or not path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            raise FeedError("noncanonical official image URL")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != len({key for key, _ in pairs}) or any(key not in ("w", "fit") for key, _ in pairs):
            raise FeedError("unsupported image URL query")
        params = dict(pairs)
        width_text = params.get("w", "480")
        if (not re.fullmatch(r"[1-9][0-9]{0,3}", width_text)
                or int(width_text) > 3000 or params.get("fit", "fill") != "fill"):
            raise FeedError("unsupported image transform")
        width = min(int(width_text), 480)
        return f"https://assets.astonmartinf1.com{path}?w={width}&fit=fill"
    except ValueError as exc:
        raise FeedError("invalid image URL") from exc


class CheckedRedirects(HTTPRedirectHandler):
    def __init__(self, *, article):
        super().__init__()
        self.article = article
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 3:
            raise FeedError("redirect limit exceeded")
        official_url(newurl, article=self.article)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, *, article=False):
    official_url(url, article=article)
    opener = build_opener(CheckedRedirects(article=article))
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/xml, text/xml;q=0.9"})
    limit = ARTICLE_LIMIT if article else SITEMAP_LIMIT
    with opener.open(req, timeout=TIMEOUT) as response:
        official_url(response.geturl(), article=article)
        if response.status != 200:
            raise FeedError("unexpected HTTP status")
        body = response.read(limit + 1)
        if len(body) > limit or not body:
            raise FeedError("empty or oversized source")
        return body


def parse_sitemap(xml):
    if not xml or len(xml) > SITEMAP_LIMIT or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise FeedError("invalid or oversized sitemap")
    try:
        root = ET.fromstring(xml)
        if root.tag != NS + "urlset":
            raise FeedError("not a sitemap urlset")
        found = {}
        for entry in root.findall(NS + "url"):
            loc = entry.findtext(NS + "loc")
            if not loc:
                continue
            # Never follow a foreign or ambiguous sitemap link, even if it is not a news link.
            official_url(loc)
            if not urlsplit(loc).path.startswith("/en-GB/news/"):
                continue
            official_url(loc, article=True)
            lastmod = entry.findtext(NS + "lastmod") or ""
            # Sitemap lastmod ranks discovery only. Publication dates come from pages.
            if lastmod:
                parse_date(lastmod)
            found[loc] = max(lastmod, found.get(loc, ""))
        if not found:
            raise FeedError("no official news entries in sitemap")
        return sorted(found, key=lambda u: (found[u], u), reverse=True)
    except ET.ParseError as exc:
        raise FeedError("malformed sitemap") from exc


class ArticleMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.date_text = []
        self.in_date = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and a.get("content"):
                self.meta.setdefault(key.lower(), a["content"].strip())
        if a.get("itemprop", "").lower() == "datepublished":
            self.in_date = tag != "meta"
            value = a.get("datetime") or a.get("content")
            if value:
                self.meta.setdefault("datepublished", value.strip())

    def handle_data(self, data):
        if self.in_date:
            self.date_text.append(data)

    def handle_endtag(self, tag):
        if self.in_date and tag in ("time", "span", "div", "p"):
            self.in_date = False


def parse_date(value):
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if re.fullmatch(r"\d{1,2} [A-Za-z]+ \d{4}", value):
            for fmt in ("%d %B %Y", "%d %b %Y"):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            raise ValueError("unrecognized month")
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("no timezone")
        return date.astimezone(timezone.utc)
    except ValueError as exc:
        raise FeedError("invalid or missing publication date: " + value[:100]) from exc


def extract_article(html, url):
    official_url(url, article=True)
    if not html or len(html) > ARTICLE_LIMIT:
        raise FeedError("empty or oversized article")
    parser = ArticleMetadata()
    try:
        parser.feed(html.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise FeedError("invalid article encoding or HTML") from exc
    meta = parser.meta
    title = meta.get("og:title", "").strip()
    description = meta.get("og:description", "").strip()
    published = meta.get("datepublished") or " ".join(parser.date_text).strip()
    if not title or not description or not published:
        raise FeedError("article missing title, description or published date: " + url)
    image_url = None
    if meta.get("og:image"):
        try:
            image_url = official_image_url(meta["og:image"])
        except FeedError:
            # Image metadata is optional; never let a bad image hide a valid story.
            pass
    return {"title": title, "description": description,
            "pub_date": parse_date(published), "link": url, "image_url": image_url}


def build_feed(fetcher=fetch, max_items=20):
    if not 1 <= max_items <= MAX_ITEMS:
        raise FeedError("max-items outside allowed range")
    urls = parse_sitemap(fetcher(SITEMAP))[:max_items]
    items = [extract_article(fetcher(url, article=True), url) for url in urls]
    items.sort(key=lambda x: (x["pub_date"], x["link"]), reverse=True)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for name, text in (("title", "Aston Martin Aramco F1 — official news"),
                       ("link", ORIGIN + "/en-GB/news"),
                       ("description", "Official Aston Martin Aramco F1 team stories and announcements."),
                       ("language", "en-GB")):
        ET.SubElement(channel, name).text = text
    for record in items:
        item = ET.SubElement(channel, "item")
        for name, text in (("title", record["title"]), ("link", record["link"]),
                           ("guid", record["link"]), ("description", record["description"]),
                           ("pubDate", format_datetime(record["pub_date"], usegmt=True))):
            elem = ET.SubElement(item, name)
            elem.text = text
            if name == "guid":
                elem.set("isPermaLink", "true")
        if record["image_url"]:
            ET.SubElement(item, f"{{{MEDIA_NS}}}content",
                          {"url": record["image_url"], "medium": "image"})
    payload = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    validate_feed(payload)
    return payload


def validate_feed(payload):
    try:
        root = ET.fromstring(payload)
        channel = root.find("channel")
        if root.tag != "rss" or root.get("version") != "2.0" or channel is None:
            raise FeedError("invalid RSS root")
        items = channel.findall("item")
        links = [item.findtext("link") for item in items]
        if not items or not all(links) or len(set(links)) != len(links):
            raise FeedError("empty or duplicate RSS items")
        for item in items:
            link = item.findtext("link")
            official_url(link, article=True)
            if (item.findtext("guid") != link or not item.findtext("title")
                    or not item.findtext("description") or not item.findtext("pubDate")):
                raise FeedError("incomplete RSS item")
            images = item.findall(f"{{{MEDIA_NS}}}content")
            if len(images) > 1:
                raise FeedError("multiple RSS images")
            if images:
                image = images[0]
                if image.get("medium") != "image" or official_image_url(image.get("url")) != image.get("url"):
                    raise FeedError("invalid RSS image")
    except ET.ParseError as exc:
        raise FeedError("malformed RSS") from exc


def atomic_write(path, payload):
    validate_feed(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".feed-", suffix=".tmp", delete=False) as out:
            temp = Path(out.name)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(Path(__file__).with_name("feed.xml")))
    parser.add_argument("--max-items", type=int, default=20, help="1–30 latest sitemap entries")
    args = parser.parse_args(argv)
    try:
        payload = build_feed(max_items=args.max_items)
        atomic_write(args.output, payload)
    except (FeedError, OSError, HTTPError) as exc:
        print("Feed not replaced: " + str(exc), file=sys.stderr)
        return 1
    print(f"Wrote {args.output} with {len(ET.fromstring(payload).find('channel').findall('item'))} items")
    return 0

if __name__ == "__main__":
    sys.exit(main())
