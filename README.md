# Aston Martin Aramco F1 — official news RSS builder

This is a standalone, host-neutral Python-standard-library RSS 2.0 generator. It reads the official `https://www.astonmartinf1.com/sitemap.xml` and the newest official `/en-GB/news/` articles. It includes team, race, driver, feature and official announcement stories without asserting that an item is a sale or inventing promotions. Official merchandise, ticket and sales announcements enter only if they are published as official news articles in this source; there is no separate shop crawler or guarantee of complete commercial coverage.

## Run

Python 3.10+ with network access and no third-party packages:

    python generate_feed.py
    python generate_feed.py --max-items 20 --output feed.xml
    python -m unittest discover -s tests -v

`--max-items` is bounded to 1–30 (default 20). The script requires every selected article to be fetched and parsed; if any required input fails, the existing feed remains untouched. It stages validated XML in the output directory and atomically replaces the file. Keep a known-good backup if required by your host. Feed GUIDs are stable original article URLs, and articles are sorted by their published timestamp. Day-only publication dates are represented at **00:00 UTC** in RSS. Sitemap `lastmod` is used only to select the latest candidate URLs, never as the publication timestamp. A later historical article may therefore be excluded if it falls outside the newest 20 sitemap updates. No generated fallback dates or fabricated sales.

The source is polled with a dedicated User-Agent, request timeouts, per-resource size limits, a maximum of three redirects per request, and strict HTTPS origin/path checks on the request and every redirect. The source can still be unavailable or change markup. Failed runs exit nonzero; monitor the scheduler log and preserve last good output.

## Private handoff and future hosting

This folder is prepared for a private repository handoff. `feed.xml` is a sample generated artifact, not a live endpoint. The `.dev-team` notes and Python caches are local and excluded from the handoff. No public feed URL, active refresh schedule, or deployment is supplied here.

The receiving team should run this with Python 3.10+ and outbound HTTPS access, schedule regeneration (for example, adapt `scheduler.example.cron`), and serve `feed.xml` at a stable HTTPS URL with `Content-Type: application/rss+xml; charset=utf-8`. A static host can serve a separately regenerated XML artifact; it does not run this Python script automatically. Persist the output across runs, watch failed exits and feed freshness, and verify the actual URL in an independent RSS reader. For rollback, stop the schedule and restore a known-good XML backup.

The generator downloads only official publicly readable pages. It makes no requests to a shop inventory, payment endpoint, login, WhatsApp, or third-party news. Article summaries come from the official page's Open Graph descriptions. The private handoff does not grant content rights; the publisher remains responsible for permitted public redistribution and attribution. This feed does not guarantee complete merchandise, ticket, or sales coverage, and it supplies no live prices or discounts.
