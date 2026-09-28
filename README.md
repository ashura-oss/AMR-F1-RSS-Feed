# Aston Martin Aramco F1 — official news RSS builder

This is a standalone, host-neutral Python-standard-library RSS 2.0 generator. It reads the official `https://www.astonmartinf1.com/sitemap.xml` and the newest official `/en-GB/news/` articles. It includes team, race, driver, feature and official announcement stories without asserting that an item is a sale or inventing promotions. Official merchandise, ticket and sales announcements enter only if they are published as official news articles in this source; there is no separate shop crawler or guarantee of complete commercial coverage.

## Run

Python 3.10+ with network access and no third-party packages:

    python generate_feed.py
    python generate_feed.py --max-items 20 --output feed.xml
    python -m unittest discover -s tests -v

`--max-items` is bounded to 1–30 (default 20). The script requires every selected article to be fetched and parsed; if any required input fails, the existing feed remains untouched. It stages validated XML in the output directory and atomically replaces the file. Keep a known-good backup if required by your host. Feed GUIDs are stable original article URLs, and articles are sorted by their published timestamp. Day-only publication dates are represented at **00:00 UTC** in RSS. Sitemap `lastmod` is used only to select the latest candidate URLs, never as the publication timestamp. A later historical article may therefore be excluded if it falls outside the newest 20 sitemap updates. No generated fallback dates or fabricated sales.

When an article exposes a valid `og:image` URL on the team's `assets.astonmartinf1.com/public/cms/` host, its RSS item includes a Media RSS `media:content` image URL. The generator requests a 480-pixel-wide CDN variant for phone news cards. An article with missing or invalid image metadata remains in the feed without an image. The image files are **not copied to Azure**; readers fetch them from Aston Martin's CDN, so image availability depends on that host. The official article remains the linked source and attribution.

The source is polled with a dedicated User-Agent, request timeouts, per-resource size limits, a maximum of three redirects per request, and strict HTTPS origin/path checks on the request and every redirect. The source can still be unavailable or change markup. Failed runs exit nonzero; monitor the scheduler log and preserve last good output.

## Public repository and Azure feed

This folder is in a public repository for the team handoff. The feed is publicly served at **https://green-sky-08b27ad10.4.azurestaticapps.net/feed.xml** from the Azure Static Web App `amr-f1-rss-feed` (resource group `rg-amr-rss-feed`, Free plan). Azure serves the latest successfully published XML snapshot. The `.dev-team` notes and Python caches are local and excluded from the handoff.

The `.github/workflows/refresh-feed.yml` workflow checks for new official stories at 00:17, 06:17, 12:17 and 18:17 UTC, and can also be started manually from GitHub Actions. It runs tests and the generator, compares the validated XML with Azure, then deploys only when it differs (manual runs always deploy). The job stages only `feed.xml`, `index.html`, and `staticwebapp.config.json`, and verifies the bytes served by Azure. A source or deployment failure leaves the last good hosted feed in place. The workflow uses the repository Actions secret `AMR_RSS_SWA_DEPLOYMENT_TOKEN` for the Azure deployment; keep it out of Git and logs. Check the Actions run history for failures. GitHub scheduling is best-effort, so a new story is not immediate; Azure also caches the XML for five minutes.

The checked-in `feed.xml` is a known-good starting snapshot; scheduled runs do not commit generated XML back to Git, so the live feed can be newer. To update manually, run `python generate_feed.py` and the tests, then deploy the same three static files. The included `scheduler.example.cron` only covers local generation and is not installed. For rollback, disable the GitHub workflow and redeploy a known-good XML backup. The config serves `/feed.xml` as `application/rss+xml; charset=utf-8`.

The generator downloads only official publicly readable pages and reads image URLs from article metadata without downloading the image files. It makes no requests to a shop inventory, payment endpoint, login, WhatsApp, or third-party news. Article summaries come from the official page's Open Graph descriptions. This public repository does not grant content rights; the publisher remains responsible for permitted public redistribution and attribution. This feed does not guarantee complete merchandise, ticket, or sales coverage, and it supplies no live prices or discounts.
