# How to Add a New Scraper

This guide matches the current backend layout and registration flow.

## Current Structure

```text
backend/
â”œâ”€â”€ main.py
â””â”€â”€ app/
    â”œâ”€â”€ main.py
    â””â”€â”€ scrapers/
        â”œâ”€â”€ __init__.py
        â”œâ”€â”€ xnxx/
        â”‚   â”œâ”€â”€ __init__.py
        â”‚   â”œâ”€â”€ scraper.py
        â”‚   â””â”€â”€ categories.json
        â””â”€â”€ <site_name>/
            â”œâ”€â”€ __init__.py
            â”œâ”€â”€ scraper.py
            â””â”€â”€ categories.json
```

## Required Interface

Each scraper module must expose these functions:

- `can_handle(host: str) -> bool`
- `scrape(url: str) -> dict`
- `list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]`
- `get_categories() -> list[dict]` (or async if the scraper requires it)

Optional:

- `crawl_videos(...)` only if you want `/api/v1/crawls` support

## Step-by-Step

### 1) Create the new scraper folder

Create `backend/app/scrapers/<site_name>/` with:

- `scraper.py`
- `__init__.py`
- `categories.json`

Fastest start:

```bash
cp -r backend/app/scrapers/xnxx backend/app/scrapers/<site_name>
```

Then rename/update internals.

### 2) Implement exports in `__init__.py`

Example:

```python
from .scraper import can_handle, scrape, list_videos, get_categories

__all__ = ["can_handle", "scrape", "list_videos", "get_categories"]
```

If your scraper has `crawl_videos`, include it in imports/`__all__`.

### 3) Register scraper package

Edit `backend/app/scrapers/__init__.py`:

1. Add `from . import <site_name>`
2. Add `"<site_name>"` to `__all__`

If you skip this, importing from `app.scrapers` in `app/main.py` will fail.

### 4) Register in `backend/app/main.py`

Update all required dispatcher/router spots:

1. **Top-level import from `app.scrapers`**
   - Add `<site_name>` to the import list.
2. **`_scrape_dispatch(...)`**
   - Add branch for `can_handle()` -> `scrape()`.
3. **`_list_dispatch(...)`**
   - Add branch for `can_handle()` -> `list_videos()`.
4. **`get_categories(source: str)` endpoint**
   - Add source alias mapping -> `<site_name>.get_categories()`.
5. **`_crawl_dispatch(...)` (optional)**
   - Add only if your scraper implements crawling.

## Minimal `scraper.py` Template

```python
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return "example.com" in h or "www.example.com" in h


async def scrape(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else ""
    return {
        "url": url,
        "title": title,
        "thumbnail_url": None,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        },
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]:
    return []


def get_categories() -> list[dict]:
    return []
```

## Categories File

`categories.json` should be a list of category objects your scraper understands. Keep the shape consistent with existing scraper folders so `/api/v1/categories` returns valid `CategoryItem` entries.

## Verification Checklist

Before shipping:

- New folder exists in `backend/app/scrapers/<site_name>/`
- `backend/app/scrapers/__init__.py` includes `<site_name>`
- `backend/app/main.py` updated in:
  - scraper imports
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping
  - optional `_crawl_dispatch`
- `can_handle()` matches real hostnames
- `scrape()` and `list_videos()` return dict keys expected by API schemas

Quick manual tests (replace URL and source):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://example.com/video/123\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://example.com/videos&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=<site_name>"
```

If all three endpoints return valid data, your scraper integration is complete.

## TNAFlix Implementation Notes

Use this as a concrete example for `tnaflix.com` support.

### Host aliases

- `tnaflix.com`
- `www.tnaflix.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "tnaflix.com" or h.endswith(".tnaflix.com")
```

### Metadata extraction fallback order

For `scrape(url)` on TNAFlix, this order is resilient:

1. `og:title` / `og:description` / `og:image`
2. `twitter:title` / `twitter:image`
3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `keywords`)
4. Visible text fallback (duration/views regex)

This keeps the response stable even when one source disappears.

### Stream extraction approach

TNAFlix video URLs are typically exposed in inline script blocks. For a first pass:

- Scan page HTML for `.m3u8` and `.mp4` URLs
- Unescape script-escaped URLs (`\\/` -> `/`, `\\u0026` -> `&`)
- Build `video.streams` with:
  - `quality`
  - `url`
  - `format` (`hls` or `mp4`)
- Set `video.default` to the best candidate after sorting by quality

Keep the response shape compatible with existing `ScrapeResponse` expectations.

### Listing and pagination patterns

For `list_videos(base_url, page, limit)`:

- Parse video cards by filtering links that contain `/video`
- Pull title from `a[title]`, image `alt`, or visible text
- Pull thumbnail from `data-src` / `data-original` / `src`
- Extract duration/views/uploader from nearest card container text/selectors
- Start with query pagination (`?page={page}`) for page > 1

### Registration checklist for TNAFlix

Besides creating `backend/app/scrapers/tnaflix/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=tnaflix`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text (optional)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` for TNAFlix

### TNAFlix verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.tnaflix.com/video/123456/demo\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.tnaflix.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tnaflix"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.tnaflix.com/video/123456/demo"
```

## HornySimp Implementation Notes

HornySimp (`hornysimp.com`) is a WordPress/Elementor-style listing site where video pages typically embed third-party players via `<iframe>`, rather than exposing direct `.mp4`/`.m3u8` URLs on the main page HTML.

### Host aliases

- `hornysimp.com`
- `www.hornysimp.com` (if it ever appears)

### Pagination pattern

Section pages and the home page paginate using a query param:

- `?_page=2`
- `?_page=3`

So `list_videos(base_url, page)` should generally build `base_url + "?_page={page}"` (or `&` if `base_url` already has a query).

### Stream extraction approach (same idea as `xxxparodyhd`)

For `scrape(url)`:

- Extract metadata from `og:title`, `og:description`, `og:image`, plus `h1` fallback.
- Collect player embed URLs from `iframe[src]` (skip ad iframes). The site uses two tabs (`Server 1` / `Server 2`); expose each iframe as its own stream with `format="embed"` and `quality` set to `"Server 1"`, `"Server 2"`, â€¦ matching the UI.
- Set `video.default` to the **Byse / byseraguci.com** embed (â€œServer 2â€) when present, else **hrnyvid / LuluStream**, else the first embed.
- `GET /api/v1/videos/stream` for `hornysimp.com` includes **flat per-source fields** (`Server 1`, `Server 2`, â€¦) in the JSON response, same pattern as `xxxparodyhd.net` (see `get_stream_url` in `video_streaming.py`).

### Registration checklist for HornySimp

Besides creating `backend/app/scrapers/hornysimp/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hornysimp`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### HornySimp verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hornysimp.com/<post-slug>/\"}"

curl \"http://127.0.0.1:8000/api/v1/videos?base_url=https://hornysimp.com/leaked-clips/&page=1&limit=20\"

curl \"http://127.0.0.1:8000/api/v1/categories?source=hornysimp\"

curl \"http://127.0.0.1:8000/api/v1/videos/info?url=https://hornysimp.com/<post-slug>/\"
```

## PimpBunny Implementation Notes

[PimpBunny](https://pimpbunny.com/) is a Vicetemple-style tube: public video pages live under `/videos/{slug}/`, categories under `/categories/{slug}/`, and sitewide search under `/search/{query}/`.

### Host aliases

- `pimpbunny.com`
- `www.pimpbunny.com` (and other subdomains if they mirror the same paths)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "pimpbunny.com" or h.endswith(".pimpbunny.com")
```

### Metadata and streams (`scrape`)

- Prefer `og:title`, `og:description`, `og:image`, plus `<meta name="keywords">` for tags.
- **Progressive MP4** URLs appear in the HTML as same-origin `https://pimpbunny.com/get_file/.../*.mp4` (often several resolutions, e.g. `_360p`, `_720p`, `_1080p`, plus a basename `/{id}.mp4` â€œsourceâ€ variant).
- A **HEAD** request to each `get_file` URL (with `Referer: https://pimpbunny.com/`) usually returns **302** to the real playable URL on a CDN host: `https://st*.pimpbunny.com/remote_control.php?time=...&file=%2Fvideos%2F...&cv=...` (tokens are short-lived). If **HEAD** does not redirect, try **GET** with `Range: bytes=0-0` the same way. Tiers that still do not redirect (often premium-only) are **dropped** from `video.streams` so the API does not expose non-playable bare `get_file` links.
- Parse with regex after unescaping `\\/` â†’ `/` and `\\u0026` â†’ `&`. Build `video.streams` with `format="mp4"` and `quality` from the filename (`_720p`, `_pb_1080p`, etc.). The HTML often lists **the same quality more than once** with different signing hashes; **keep the last match per quality** (the player config block is usually later and is the one that returns 302).
- Resolve each `get_file` like the browser: **Referer** = the **full video page URL**, `GET` with `Range: bytes=0-` (and `HEAD` / `Range: 0-0` as fallbacks), URL form `...mp4/?rnd=<unix_ms>` (see network tab).
- The page also references `https://pimpbunny.com/embed/{numericId}`; you can expose that as `format="embed"` / `quality="embed"` as a fallback for clients that only handle embeds.
- Set `video.default` to the best MP4 by resolution, not the embed.

### Listing and pagination (`list_videos`)

- Video cards link to `https://pimpbunny.com/videos/{slug}/`. Skip `upload-video` and the bare `/videos/` index.
- **Videos index:** page 1 is `https://pimpbunny.com/videos/`, page *n* &gt; 1 is `https://pimpbunny.com/videos/{n}/` (not `?page=`).
- **Categories:** page 1 is `https://pimpbunny.com/categories/{slug}/`, page *n* &gt; 1 is `https://pimpbunny.com/categories/{slug}/{n}/`.
- **Search:** base URL `https://pimpbunny.com/search/{term}/`; for page *n* &gt; 1 add `?page=n` (combine with any existing query params).
- Treat bare `https://pimpbunny.com/` as the videos index when building the first page URL.

### Registration checklist for PimpBunny

Besides creating `backend/app/scrapers/pimpbunny/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=pimpbunny`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - flat `available_qualities` block (same pattern as `tnaflix.com`)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`baseUrl` should be list-friendly, e.g. `https://pimpbunny.com/videos/`)

### PimpBunny verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pimpbunny.com/videos/gracewearslace-receives-a-cumshot-after-sex/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pimpbunny.com/videos/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pimpbunny"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://pimpbunny.com/videos/gracewearslace-receives-a-cumshot-after-sex/"
```

## Hentaiser Implementation Notes

[Hentaiser](https://app.hentaiser.app/) exposes a JSON API and media on a CDN host. For this source, scraper logic can be mostly API-first rather than HTML parsing.

### Host aliases

- `app.hentaiser.app` (site/API)
- `api.hentaiser.app` (video feed API)
- `media2.hentaiser.com` (thumbnail/video CDN)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return (
        h == "app.hentaiser.app"
        or h.endswith(".hentaiser.app")
        or h == "media2.hentaiser.com"
        or h.endswith(".hentaiser.com")
    )
```

### API-first listing (`list_videos`)

Use the API endpoint as primary source:

- `https://api.hentaiser.app/v1/videos?sort=comments&limit=4&top=1`

Recommended approach:

- Build requests against `https://api.hentaiser.app/v1/videos`.
- Keep support for query params such as `sort`, `limit`, and `top`.
- When `page` is requested by backend API, map it to whatever pagination Hentaiser returns (offset/page/cursor) and gracefully fallback to first page if absent.
- Normalize response items to existing list schema (`url`, `title`, `thumbnail_url`, `duration`, `views`, `uploader_name`).

### Media URL and ID extraction (`scrape`)

Given sample URLs:

- Thumbnail URL:
  - `https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.jpg`
- Video URL:
  - `https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4`

Treat the CDN path as stable ID:

- **thumbnail_id**: `/videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.jpg`
- **video_id**: `/videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4`
- **media host**: `https://media2.hentaiser.com`

Implementation tips:

- Preserve nested path segments under `/videos/...` instead of reducing to only basename.
- Store full URLs in `thumbnail_url` and stream URLs.
- Add one MP4 stream entry (`format="mp4"`, `quality="source"` unless the API provides richer qualities).
- Set `video.default` to that MP4 URL and `video.has_video=True`.

### Registration checklist for Hentaiser

Besides creating `backend/app/scrapers/hentaiser/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hentaiser`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - quality map (`source` or API-provided tiers)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### Hentaiser verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://app.hentaiser.app/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://api.hentaiser.app/v1/videos?sort=comments&top=1&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hentaiser"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4"
```

## BollywoodMaal Implementation Notes

[BollywoodMaal](https://bollywoodmaal.com/) is a WordPress-style tube site with homepage/category card grids, pagination links, and post pages that usually expose playable sources in HTML or inline script/player config blocks.

### Host aliases

- `bollywoodmaal.com`
- `www.bollywoodmaal.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "bollywoodmaal.com" or h.endswith(".bollywoodmaal.com")
```

### Listing and pagination (`list_videos`)

Use a resilient card parser so theme/layout changes do not break quickly:

- Parse item links from anchors that look like video-post targets (title cards / thumbnails).
- Keep only unique links under the same domain and skip utility URLs (`/contact`, auth/profile paths, policy pages).
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration: parse card text using `mm:ss` / `hh:mm:ss` regex
  - views: parse numeric counters (`129`, `1K`, `34K`) from nearby text
- Page 1 should use `base_url` unchanged.
- For page > 1, follow the site pager links first (`/page/{n}/`, `?paged={n}`, or explicit numbered pager URLs). If no pager exists, fallback to appending `?paged={page}`.

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title/header fallback
- For playable sources, scan:
  - `<video>` tags (`source[src]`, `video[src]`)
  - `<iframe src>` embeds (external host streams)
  - inline scripts for direct `.mp4` / `.m3u8` URLs
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` entries:
  - direct files: `format="mp4"` / `format="hls"`
  - embedded players: `format="embed"` and `quality` like `Server 1`, `Server 2`
- Set `video.default` with this preference:
  1. highest quality direct MP4
  2. HLS URL
  3. first embed URL

### Registration checklist for BollywoodMaal

Besides creating `backend/app/scrapers/bollywoodmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=bollywoodmaal`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### BollywoodMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://bollywoodmaal.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bollywoodmaal.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=bollywoodmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://bollywoodmaal.com/<video-post-slug>/"
```

## Viralkand Implementation Notes

[Viralkand](https://viralkand.com/) looks like a WordPress-style clip index with:

- homepage/category grids of card links
- numbered pagination
- search support
- post/detail pages that should be treated as the canonical video URLs

Use the existing `bollywoodmaal`, `hornysimp`, and `masa49` scrapers as the closest starting references.

### Host aliases

- `viralkand.com`
- `www.viralkand.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "viralkand.com" or h.endswith(".viralkand.com")
```

### Listing and pagination (`list_videos`)

The public index exposes a paginated card grid plus category and search pages. Recommended approach:

- Parse candidate video links from thumbnail/title anchors inside the main listing grid.
- Keep only same-domain URLs and skip obvious utility pages such as:
  - `/dmca-remove-a-video`
  - `/18-u-s-c-2257`
  - `/terms-of-use`
  - tag/category index roots without a concrete video item
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible text
  - thumbnail: `data-src`, `data-lazy-src`, `data-original`, `srcset`, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss`
  - views/rating: parse nearby card text only if easy; keep them optional
- Page 1 should use `base_url` unchanged.
- For page > 1, first follow the site pager format if visible (`/page/{n}/` is the most likely WordPress pattern). If the supplied `base_url` already includes a category/tag path, preserve it and append the page segment there.
- For search URLs, prefer WordPress query search (`https://viralkand.com/?s={query}`) unless live inspection shows a different route.

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` if present
  4. visible `h1` / `<title>` fallback
- Scan for playable sources in:
  - `<video src>` / `<video><source src>`
  - `iframe[src]` embeds
  - inline scripts that expose `.mp4` or `.m3u8`
- Unescape inline-script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` using:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` and qualities like `Server 1`, `Server 2`
- Set `video.default` with this preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed

If the site only exposes third-party embeds on the post page, follow the same fallback pattern used by `hornysimp` / `xxxparodyhd`: return embed streams instead of forcing nonexistent direct media URLs.

### Categories (`get_categories`)

Start with a static `categories.json` copied from the public category list the scraper will support. Keep the schema aligned with the other scraper folders so `/api/v1/categories?source=viralkand` returns valid `CategoryItem` entries.

### Registration checklist for Viralkand

Besides creating `backend/app/scrapers/viralkand/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=viralkand`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
  - host checks for stream/info passthrough if needed
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request validation is still backed by explicit domain allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Viralkand verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://viralkand.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://viralkand.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=viralkand"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://viralkand.com/<video-post-slug>/"
```

## UncutMaza Implementation Notes

[UncutMaza](https://uncutmazaa.com/) is a WordPress-style clip index focused on episodic posts. The homepage exposes recent post cards with title links, relative publish-time labels, and duration-like badges (`mm:ss`) directly in listing text.

**Note:** `uncutmaza.com` redirects toward `uncutmaza.cc`, which often returns Cloudflare **403** to automated clients. The scraper rewrites `uncutmaza.com` / `uncutmaza.cc` requests to **`uncutmazaa.com`** (live HTML) before fetching.

Use `viralkand`, `mmsbro`, and `bollywoodmaal` as close implementation references.

### Host aliases

- `uncutmazaa.com` (canonical fetch host)
- `uncutmaza.com` / `uncutmaza.cc` (accepted; rewritten for HTTP fetch)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("uncutmazaa.com", "uncutmaza.com", "uncutmaza.cc")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail links from post-card anchors in the primary content grid.
- Keep only same-domain post URLs and skip utility/legal paths when present (`/contact`, `/privacy-policy`, `/dmca`, `/18-u-s-c-2257`, tag/category roots without concrete post slugs).
- Prefer metadata in this order:
  - title: card heading anchor text, then anchor `title`, then image `alt`
  - thumbnail: `data-src`, `data-lazy-src`, `data-original`, first `srcset` entry, then `src`
  - duration: parse `mm:ss` or `hh:mm:ss` from card text (many homepage entries expose `20:00`-style values)
  - views/uploader: optional (`None` if unavailable in card markup)
- Page 1 should use `base_url` unchanged.
- For page > 1, follow visible pager links first (WordPress commonly uses `/page/{n}/`). If no pager can be inferred, fallback to `?paged={n}` while preserving existing query params.

Useful list base URLs:

- `https://uncutmazaa.com/`
- `https://uncutmazaa.com/category/<category-slug>/` (if category archives are used)
- `https://uncutmazaa.com/?s=<query>` (if search query route is used)

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` and quality labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a detail page only exposes embedded players, return embed streams rather than manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's visible category navigation/archive list. Keep schema aligned with existing scraper folders so `/api/v1/categories?source=uncutmaza` returns valid `CategoryItem` entries.

### Registration checklist for UncutMaza

Besides creating `backend/app/scrapers/uncutmaza/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=uncutmaza`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### UncutMaza verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://uncutmazaa.com/kya-khoob-lagti-ho-episode-6/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmazaa.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=uncutmaza"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://uncutmazaa.com/kya-khoob-lagti-ho-episode-6/"
```

## Blowjobs.pro Implementation Notes

## DesiPorn.one Implementation Notes

[DesiPorn.one](https://desiporn.one/) is a tube-style site with canonical detail pages under `/videos/{id}/{slug}/`. The home page exposes card listings and navigation for Latest, Top Rated, Most Viewed, Categories, and Search.

### Host aliases

- `desiporn.one`
- `www.desiporn.one`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "desiporn.one" or h.endswith(".desiporn.one")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse card anchors that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain video URLs and skip utility pages such as `/terms`, `/2257`, and external DMCA links.
- Prefer metadata in this order:
  - title: anchor text, then `title`, then image `alt`
  - thumbnail: `data-src`, `data-original`, first `srcset` candidate, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss` from card text
  - views/rating: parse compact counters and percentages when easy; keep optional
- Page 1 should use `base_url` unchanged.
- For page > 1, follow visible paginator routes first; fallback to `?page={n}` if no route is detected.

Useful base URLs to support:

- `https://desiporn.one/`
- `https://desiporn.one/latest/` (or site's "Latest" route if different)
- `https://desiporn.one/top-rated/`
- `https://desiporn.one/most-viewed/`
- `https://desiporn.one/categories/<category-slug>/`
- `https://desiporn.one/search/<term>/` (or query search route used by live markup)

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` if present (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title fallback
- Scan for playable URLs in:
  - `<video src>` / `<video><source src>`
  - inline scripts exposing `.mp4` / `.m3u8`
  - `iframe[src]` embeds (fallback)
- Unescape script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` with server labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed URL

If the page only exposes embedded players, return embed streams instead of fabricating direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's public Categories index and keep schema aligned with existing scraper folders so `/api/v1/categories?source=desiporn` returns valid `CategoryItem` entries.

### Registration checklist for DesiPorn.one

Besides creating `backend/app/scrapers/desiporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desiporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
  - host checks for stream/info passthrough if needed
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If URL validation still uses strict allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiPorn.one verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://desiporn.one/videos/22481/desi-sex-bahu-and-sasur-indian-porn-videos/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desiporn.one/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desiporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://desiporn.one/videos/22481/desi-sex-bahu-and-sasur-indian-porn-videos/"
```

[Blowjobs.pro](https://blowjobs.pro/) is a tube-style site with canonical video pages under `/videos/{id}/{slug}/`, sortable listing views (Newest/Hottest/Most Viewed/Top Rated), category pages, model pages, and search.

### Host aliases

- `blowjobs.pro`
- `www.blowjobs.pro`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "blowjobs.pro" or h.endswith(".blowjobs.pro")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse video cards/anchors that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain URLs and skip utility/auth links (`/login`, `/signup`, `/terms`, `/dmca`, `/2257`).
- Prefer metadata in this order:
  - title: anchor text, then `title` attribute, then image `alt`
  - thumbnail: `data-src`, `data-original`, `srcset` first candidate, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss` from nearby card text
  - views/rating: parse compact counters (`304.8k`, `1.3m`) and percentages when easy; keep optional
- Page 1 should use `base_url` unchanged.
- For page > 1, follow whichever paginator route the page exposes first (numeric path segment or query param); fallback to `?page={n}`.

Useful base URLs to support:

- `https://blowjobs.pro/`
- `https://blowjobs.pro/videos/newest/`
- `https://blowjobs.pro/videos/hottest/`
- `https://blowjobs.pro/videos/most-viewed/`
- `https://blowjobs.pro/videos/top-rated/`
- `https://blowjobs.pro/categories/<category-slug>/`
- `https://blowjobs.pro/models/<model-slug>/`
- `https://blowjobs.pro/search/<term>/` (if search route is enabled in live markup)

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title fallback
- Scan for playable URLs in:
  - `<video src>` / `<video><source src>`
  - inline scripts for `.mp4` / `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` with `quality` labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first embed URL

If detail pages expose only third-party embeds, return embed streams instead of fabricating direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from public category pages under `/categories/` and keep schema aligned with existing scraper folders so `/api/v1/categories?source=blowjobspro` returns valid `CategoryItem` entries.

### Registration checklist for Blowjobs.pro

Besides creating `backend/app/scrapers/blowjobspro/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=blowjobspro`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If URL validation still uses strict allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Blowjobs.pro verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://blowjobs.pro/videos/7209/18-year-old-teen-gives-deepthroat-pov-blowjob/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blowjobs.pro/videos/newest/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=blowjobspro"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://blowjobs.pro/videos/7209/18-year-old-teen-gives-deepthroat-pov-blowjob/"
```

## BlackPorn24 Implementation Notes

[BlackPorn24](https://blackporn24.com/) follows the same family of tube layout as Blowjobs.pro: canonical detail URLs under `/videos/{id}/{slug}/`, category pages under `/categories/{slug}/`, and sortable list tabs (Newest/Hottest/Most Viewed/Top Rated) exposed from the home page.

### Fast implementation plan (same as Blowjobs.pro)

BlackPorn24 can be implemented as a near-clone of the `blowjobspro` scraper:

1. Copy `backend/app/scrapers/blowjobspro/` -> `backend/app/scrapers/blackporn24/`.
2. Rename host checks and defaults:
   - `blowjobs.pro` -> `blackporn24.com`
   - `sourceId/source` -> `blackporn24`
3. Keep the same core logic:
   - card parsing (`.title`, `.duration`, `.views`)
   - `get_file` -> signed CDN `remote_control.php` resolution
   - ad iframe filtering and native `/embed/{id}` preference
4. Replace `categories.json` with `blackporn24.com/categories` entries.
5. Register in dispatch, streaming service, explore source list, and schema allowlists.

Treat BlackPorn24 as the same scraper engine with site-specific configuration (host/base URLs/categories/favicons).

### Host aliases

- `blackporn24.com`
- `www.blackporn24.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "blackporn24.com" or h.endswith(".blackporn24.com")
```

### Listing and pagination (`list_videos`)

Recommended parser behavior:

- Accept only canonical video links matching `/videos/{numeric_id}/{slug}/`.
- Skip utility/auth/legal links (`/terms`, `/dmca`, `/2257`, login/signup pages).
- Prefer card fields by class selectors when available:
  - title from `.title`
  - duration from `.duration`
  - views/rating from `.views` and nearby text
- Keep fallback extraction in case selectors shift:
  - title: anchor text / `title` / image `alt`
  - duration: `mm:ss` / `hh:mm:ss` regex
  - views: compact counters (`919k`, `2.1m`)
- Page 1 should use `base_url` unchanged.
- For page > 1, follow the site pager format first (numeric page segment or query param); fallback to `?page={n}`.

Useful list base URLs:

- `https://blackporn24.com/`
- `https://blackporn24.com/categories/<category-slug>/`
- `https://blackporn24.com/models/<model-slug>/`
- `https://blackporn24.com/search/<term>/` (if the route is active in live markup)

### Metadata and streams (`scrape`)

For video detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible title/header fallback
- Stream extraction order:
  - direct download/player links (`.mp4`, `.m3u8`, `/get_file/...`)
  - `<video src>` / `<video><source src>`
  - inline scripts with escaped URLs
  - site-native embed URL fallback (`/embed/{id}`)
- If stream links use intermediate `/get_file/...` URLs, resolve redirects to signed CDN `remote_control.php` URLs before returning `video.default`/`video.streams`.
- Filter obvious ad-network iframes (promo/banners) and keep only playable/embed entries.
- Set default stream preference:
  1. resolved direct MP4
  2. HLS
  3. site-native embed

### Categories (`get_categories`)

Start `categories.json` from `https://blackporn24.com/categories/` entries, preserving scraper folder schema so `/api/v1/categories?source=blackporn24` returns valid `CategoryItem` objects.

### Registration checklist for BlackPorn24

Besides creating `backend/app/scrapers/blackporn24/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=blackporn24`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - per-quality flat fields behavior (same pattern as blowjobspro/tnaflix)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If your branch still validates URL domains using explicit allowlists, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### BlackPorn24 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://blackporn24.com/videos/4551/lustful-stepmom-uses-her-stepson-s-big-cock-for-pleasure/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blackporn24.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=blackporn24"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://blackporn24.com/videos/4551/lustful-stepmom-uses-her-stepson-s-big-cock-for-pleasure/"
```

## IndianPorn365 Implementation Notes

[Indian Porn 365](https://indianporn365.xyz/) is a WordPress-style clip index with:

- category routes from the top nav (for example: `bhabhi`, `leaked-amateur-porn`, `desi-sex-videos`, `tamil-porn`)
- post cards on the home/category pages
- numbered pagination (`1 2 ... Next`)
- detail pages per post slug that may expose direct or embedded playable sources

Use the existing `viralkand`, `bollywoodmaal`, and `hornysimp` scrapers as closest references.

### Host aliases

- `indianporn365.xyz`
- `www.indianporn365.xyz`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "indianporn365.xyz" or h.endswith(".indianporn365.xyz")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate item links from thumbnail/title anchors in the main post grid.
- Keep only same-domain detail URLs and skip utility/legal links such as:
  - `/contact-us`
  - `/privacy-policy`
  - `/cookie-policy`
  - `/18-u-s-c-2257`
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views: parse nearby card text when present; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow site pager links first (WordPress often uses `/page/{n}/`). If no pager URL can be inferred, fallback to adding `?paged={page}`.

Useful list base URLs:

- `https://indianporn365.xyz/`
- `https://indianporn365.xyz/bhabhi/`
- `https://indianporn365.xyz/leaked-amateur-porn/`
- `https://indianporn365.xyz/desi-sex-videos/`
- `https://indianporn365.xyz/tamil-porn/`
- `https://indianporn365.xyz/hd-porn/`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible `h1` / `<title>` fallback
- Stream extraction order:
  - `<video src>` / `<video><source src>`
  - inline script URLs ending in `.mp4` or `.m3u8`
  - `iframe[src]` embeds as fallback
- Unescape script URLs before returning (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` entries as:
  - direct media: `format="mp4"` / `format="hls"`
  - embedded players: `format="embed"` with labels like `Server 1`, `Server 2`
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed

If the page only exposes third-party embeds, return embed streams instead of fabricated direct media links.

### Categories (`get_categories`)

Seed `categories.json` from the site's public nav/category pages and keep the same schema as other scraper folders so `/api/v1/categories?source=indianporn365` returns valid `CategoryItem` entries.

### Registration checklist for IndianPorn365

Besides creating `backend/app/scrapers/indianporn365/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=indianporn365`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still relies on explicit domain allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### IndianPorn365 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://indianporn365.xyz/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://indianporn365.xyz/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=indianporn365"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://indianporn365.xyz/<video-post-slug>/"
```

## MMSBro Implementation Notes

[MMSBro](https://mmsbro.com/) is a WordPress-style clip index with:

- homepage card grid linking to post slugs (`https://mmsbro.com/<post-slug>/`)
- category archives (`/category/<slug>/`)
- numbered pagination via path segments (`/page/{n}/`)
- detail pages that may expose direct media in `<video>` tags, `<source>` tags, inline script URLs, or embedded players

Use `indianporn365`, `viralkand`, and `bollywoodmaal` as closest implementation references.

### Host aliases

- `mmsbro.com`
- `www.mmsbro.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "mmsbro.com" or h.endswith(".mmsbro.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate item links from anchor cards on homepage/category pages.
- Keep only same-domain post URLs and skip utility routes such as:
  - `/contact`
  - `/privacy-policy`
  - `/cookie-policy`
  - `/18-u-s-c-2257`
  - feed/tag/author pages
- Prefer metadata in this order:
  - title: anchor text / `title`
  - thumbnail: card image `data-src`, `data-lazy-src`, `srcset`, then `src`
  - duration: parse `mm:ss` / `hh:mm:ss` near the card title
  - views/uploader: optional (extract if available, else keep `None`)
- Page 1 should use `base_url` unchanged.
- For page > 1, first follow path pagination (`/page/{n}/`). If search query style is used (`?s=query`), add `paged={n}` as query fallback.

Useful list base URLs:

- `https://mmsbro.com/`
- `https://mmsbro.com/page/2/`
- `https://mmsbro.com/category/desi-mms/`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with `quality` labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live nav/category archives (for example `/category/desi-mms/`) and keep schema aligned with existing scraper folders so `/api/v1/categories?source=mmsbro` returns valid `CategoryItem` entries.

### Registration checklist for MMSBro

Besides creating `backend/app/scrapers/mmsbro/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=mmsbro`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host error text
  - per-quality response block where applicable
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit domain allowlists, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MMSBro verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://mmsbro.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://mmsbro.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://mmsbro.com/category/desi-mms/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=mmsbro"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://mmsbro.com/<video-post-slug>/"
```

## KamaBaba Implementation Notes

[KamaBaba](https://www.thekamababa.com/) is a WordPress-style clip index with:

- sort tabs on listing pages (`Newest`, `Best`, `Most viewed`, `Longest`, `Random`)
- category and tag archive routes
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources through native `<video>` tags, inline script URLs, or embedded players

Use `mmsbro`, `indianporn365`, and `viralkand` as the closest implementation references.

### Host aliases

- `thekamababa.com`
- `www.thekamababa.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "thekamababa.com" or h.endswith(".thekamababa.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from thumbnail/title anchors in the main listing grid.
- Keep only same-domain post URLs and skip obvious utility links such as:
  - `/contact-us`
  - `/video-removal`
  - `/privacy-policy`
  - `/18-usc-2257`
  - `/advertise`
  - `/jobs`
  - `/unblock-kmb`
  - auth/profile/reset-password pages
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text when present; keep optional when absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` already contains a sort/search query, preserve existing query params when adding page params.

Useful list base URLs:

- `https://www.thekamababa.com/`
- `https://www.thekamababa.com/categories/`
- `https://www.thekamababa.com/tags/`
- `https://www.thekamababa.com/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=kamababa` returns valid `CategoryItem` entries.

### Registration checklist for KamaBaba

Besides creating `backend/app/scrapers/kamababa/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=kamababa`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### KamaBaba verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.thekamababa.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thekamababa.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thekamababa.com/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=kamababa"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.thekamababa.com/<video-post-slug>/"
```

## DesiMMS2 Implementation Notes

[DesiMMS2](https://www.desimms2.site/) is a WordPress-style clip index with:

- sort tabs on listing pages (`Newest`, `Best`, `Most viewed`, `Longest`, `Random`)
- category and tag archive routes
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages exposing playable sources via native `<video>` tags, inline script URLs, or embedded players

Use `kamababa`, `mmsbro`, and `indianporn365` as the closest implementation references.

### Host aliases

- `desimms2.site`
- `www.desimms2.site`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "desimms2.site" or h.endswith(".desimms2.site")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors.
- Keep only same-domain post URLs and skip utility links such as:
  - `/report-content`
  - `/18-u-s-c-2257`
  - `/categories/`
  - `/tags/`
  - auth/login/reset-password/profile paths
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` includes a search query (`?s=`), preserve query params and append `paged={n}`.

Useful list base URLs:

- `https://www.desimms2.site/`
- `https://www.desimms2.site/categories/`
- `https://www.desimms2.site/tags/`
- `https://www.desimms2.site/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=desimms2` returns valid `CategoryItem` entries.

### Registration checklist for DesiMMS2

Besides creating `backend/app/scrapers/desimms2/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desimms2`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiMMS2 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.desimms2.site/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.desimms2.site/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.desimms2.site/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desimms2"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.desimms2.site/<video-post-slug>/"
```

## ThotsPorn Implementation Notes

[Thots Porn](https://thotsporn.com/) is a WordPress-style clip index with:

- tabbed listing views (Latest videos, Longest videos, Random videos)
- taxonomy-driven navigation (Categories, Tags, Actors)
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources via native `<video>`, iframe embeds, or inline script URLs

Use `desimms2`, `kamababa`, and `mmsbro` as the closest implementation references.

### Host aliases

- `thotsporn.com`
- `www.thotsporn.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "thotsporn.com" or h.endswith(".thotsporn.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors in the main grid.
- Keep only same-domain post URLs and skip utility/account links such as:
  - login/reset-password/profile/auth pages
  - legal/compliance pages (`/dmca`, `/2257`, `/terms`, `/privacy`) when present
  - taxonomy root pages without a concrete video detail target
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` contains a sort/search query, preserve existing query parameters while adding page params.

Useful list base URLs to support:

- `https://thotsporn.com/`
- `https://thotsporn.com/categories/`
- `https://thotsporn.com/tags/`
- `https://thotsporn.com/actors/`
- `https://thotsporn.com/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag/actor index pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=thotsporn` returns valid `CategoryItem` entries.

### Registration checklist for ThotsPorn

Besides creating `backend/app/scrapers/thotsporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=thotsporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### ThotsPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://thotsporn.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://thotsporn.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://thotsporn.com/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=thotsporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://thotsporn.com/<video-post-slug>/"
```

## LeakedAmateurPorn Implementation Notes

[Leaked Amateur Porn](https://leakedamateurporn.xyz/) is a WordPress-style clip index with:

- tabbed listing views (Latest videos, Longest videos, Random videos)
- taxonomy-driven navigation (Categories, Tags)
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources via native `<video>`, iframe embeds, or inline script URLs

Use `thotsporn`, `desimms2`, and `kamababa` as the closest implementation references.

### Host aliases

- `leakedamateurporn.xyz`
- `www.leakedamateurporn.xyz`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "leakedamateurporn.xyz" or h.endswith(".leakedamateurporn.xyz")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors in the main grid.
- Keep only same-domain post URLs and skip utility/account links such as:
  - login/reset-password/profile/auth pages
  - legal/compliance pages (`/terms`, `/privacy`, `/2257`, `/contact`) when present
  - taxonomy root pages without a concrete video detail target
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` contains a sort/search query, preserve existing query parameters while adding page params.

Useful list base URLs to support:

- `https://leakedamateurporn.xyz/`
- `https://leakedamateurporn.xyz/categories/`
- `https://leakedamateurporn.xyz/tags/`
- `https://leakedamateurporn.xyz/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag index pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=leakedamateurporn` returns valid `CategoryItem` entries.

### Registration checklist for LeakedAmateurPorn

Besides creating `backend/app/scrapers/leakedamateurporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=leakedamateurporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### LeakedAmateurPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://leakedamateurporn.xyz/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://leakedamateurporn.xyz/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://leakedamateurporn.xyz/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=leakedamateurporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://leakedamateurporn.xyz/<video-post-slug>/"
```

## Zeenite Implementation Notes

[Zeenite](https://zeenite.com/) is a tube-style index where canonical detail pages follow `/videos/{id}/{slug}/`. The site exposes feed/navigation views for New Videos, Top Videos, Most Viewed, Categories, Models, and search.

Use `desiporn`, `thotsporn`, and `xhamster2` as close implementation references.

### Host aliases

- `zeenite.com`
- `www.zeenite.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "zeenite.com" or h.endswith(".zeenite.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse card links that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain video detail URLs and skip utility pages such as `/terms` and `/2257`.
- Prefer metadata in this order:
  - title: anchor text, then `title`, then image `alt`
  - thumbnail: `data-src`, `data-original`, first `srcset` candidate, then `src`
  - duration/views/rating: parse compact values from nearby card text when available
- Page 1 should use `base_url` unchanged.
- For page > 1, follow any visible paginator route first; if not present, fallback to common patterns like `?page={n}`.
- If list endpoints are loaded incrementally ("Load more"), allow scraper fallback logic that can parse the first page reliably and advance by discovered links/params.

Useful list base URLs to support:

- `https://zeenite.com/`
- `https://zeenite.com/new-videos/` (or equivalent route used by live markup)
- `https://zeenite.com/top-videos/` (or equivalent route used by live markup)
- `https://zeenite.com/most-viewed/` (or equivalent route used by live markup)
- `https://zeenite.com/categories/`
- `https://zeenite.com/models/`
- `https://zeenite.com/search/<term>/` (or the query/search endpoint exposed by the page)

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's public Categories and Models indexes and keep schema aligned with existing scraper folders so `/api/v1/categories?source=zeenite` returns valid `CategoryItem` entries.

### Registration checklist for Zeenite

Besides creating `backend/app/scrapers/zeenite/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=zeenite`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Zeenite verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://zeenite.com/videos/215600/dance-kabyle-chaude-9a7ba-de-tizi-ouazou/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zeenite.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=zeenite"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://zeenite.com/videos/215600/dance-kabyle-chaude-9a7ba-de-tizi-ouazou/"
```

## 85PO Implementation Notes

[85PO](https://www.85po.com/) is a KVS-style tube site (Chinese UI). Video pages use `/v/{id}/{slug}/` and expose progressive MP4 via same-origin `/get_file/...` URLs (often `_720p`, `_1080p`, and a basename `source` tier).

Use `zeenite` and `pimpbunny` as close implementation references (module folder name is `po85` because Python identifiers cannot start with a digit).

### Host aliases

- `85po.com`
- `www.85po.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "85po.com" or h.endswith(".85po.com")
```

### Listing and pagination (`list_videos`)

- Video URLs: `https://www.85po.com/v/{id}/{slug}/`
- Embed player: `https://www.85po.com/embed/{id}` (iframe shell; also exposes `/get_file/` MP4 tiers inside)
- Parse only the main list block (not the â€œwatching nowâ€ sidebar):
  - home / default: `#list_videos_most_recent_videos`
  - `/4k/`: `#list_videos_latest_videos_list`
  - `/tags/...`: `#list_videos_common_videos_list`
- Pagination uses query param `from` (page 2 â†’ `?from=2`), not `?page=`. AJAX `#more` blocks exist but GET `?from={n}` is sufficient for the API list endpoint.

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `h1`, visible duration (`mm:ss` / `hh:mm:ss`), views from `svg.icon-eye` parent (`.thumb-item` on cards, `.count-item` on detail).
- Streams: inline `/get_file/.../*.mp4` links in HTML; filter screenshot/preview assets (`preview_preview.mp4.jpg`, `/contents/videos_screenshots/`).
- Resolve each `get_file` URL with the video page as `Referer` (HEAD/GET + `Range`) to the signed CDN redirect before returning `video.streams` (same pattern as Zeenite).
- Prefer highest `NNNp` MP4 as `video.default`.

### Categories (`get_categories`)

Seed `categories.json` from public nav: Home, 4K (`/4k/`), Tags (`/tags/`), Random (`/random_video.php`).

### Registration checklist for 85PO

Besides creating `backend/app/scrapers/po85/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=po85` or `source=85po`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
  - stream quality map host checks for `85po.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="po85"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### 85PO verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.85po.com/v/30261/zi-cuo-ri--5/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.85po.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=po85"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.85po.com/v/30261/zi-cuo-ri--5/"
```

## CosXplay Implementation Notes

[CosXplay](https://cosxplay.com/) is a WordPress **kolortube** cosplay tube. Canonical video pages use `/{post_id}-{slug}/` (for example `/78642-furries-2022-.../`). Listings use `.video-block[data-post-id]` cards; pagination is WordPress-style `/page/{n}/` (including on category paths).

Use `hornysimp` for embed fallbacks and `zeenite` for JSON-LD + stream ordering patterns.

### Host aliases

- `cosxplay.com`
- `www.cosxplay.com`

### Listing and pagination (`list_videos`)

- Home: `https://cosxplay.com/` â†’ page 2 is `https://cosxplay.com/page/2/`
- Category: `https://cosxplay.com/7841-nier-automata/` â†’ `https://cosxplay.com/7841-nier-automata/page/2/`
- Parse cards via `div.video-block[data-post-id]` â†’ `a.infos[href]` / `a.thumb[href]`; duration from `.video-datas span.duration.notranslate` (or `span.duration` on the card)
- Only accept single-segment `/{id}-{slug}/` URLs (exclude `/tag/`, `/categories/`, `/embed/`, etc.)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `contentUrl`, `embedUrl`, `interactionStatistic`), and inline `toStore` (`views`, `length`, `preview`).
- Streams: signed MP4 on `xcdn*.nosofiles.com` (`*_high.mp4`, `*_low.mp4`) from `<video><source>`, inline `videoHigh` / `videoLow` JS, and JSON-LD `contentUrl`. Skip `trailer.mp4` / poster assets.
- Optional embed stream from JSON-LD `embedUrl` (`https://cosxplay.com/embed/{id}`) when direct MP4 is unavailable.
- Cloudflare may challenge bare requests; send `Referer: https://cosxplay.com/` (homepage first helps for curl/manual tests).

### Categories (`get_categories`)

Seed from nav: Home, Categories, Cosplay Girls, Tags, plus popular character/genre hubs from the mobile category menu.

### Registration checklist for CosXplay

Besides creating `backend/app/scrapers/cosxplay/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=cosxplay` or `source=cosx`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `cosxplay.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="cosxplay"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### CosXplay verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://cosxplay.com/78642-furries-2022-fursuit-yiff-murrsuit-oral-butt-point-of-view-amaze-anal-cosplay-furry/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://cosxplay.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=cosxplay"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://cosxplay.com/78642-furries-2022-fursuit-yiff-murrsuit-oral-butt-point-of-view-amaze-anal-cosplay-furry/"
```

## MemoJav Implementation Notes

[MemoJav](https://memojav.com/) is a JAV catalog site. Canonical video pages use `/video/{CODE}` **without** a trailing slash (for example `/video/START-579` â€” `/video/START-579/` returns 404). Listings use `a.video-item` cards with `img.video-poster`; pagination is `page-{n}` under the current section path without a trailing slash (for example `/video/page-2`).

### Host aliases

- `memojav.com`
- `www.memojav.com`

### Listing and pagination (`list_videos`)

- Home: `https://memojav.com/`
- Best: `https://memojav.com/best/`
- New: `https://memojav.com/video/`
- Page 2 on new videos: `https://memojav.com/video/page-2` (no trailing slash â€” `/video/page-2/` is 404)
- Parse `a.video-item[href]` â†’ title from `.video-title`, thumb from `img.video-poster`

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `#title`, `#title-description`, `var mm = {type,id,vi}`, schema `itemprop="duration"` (`PT123M0S`), actress link, trailer `#preview-vid`.
- Full movie streams come from `/hls/get_video_info.php?id={CODE}&sig=...&sts=...` (same `video_sig()` algorithm as `static/main.js`). Response is JSON prefixed with `for (;;);`.
  - `type: "hls"` â†’ `master.m3u8` on `video*.memojav.net` (preferred default).
  - `type: "mp4"` â†’ base URL with `=m37` / `=m22` / `=m18` quality suffixes (JW Player convention).
- Always include embed fallback: `https://memojav.com/embed/{CODE}`.

### Categories (`get_categories`)

Seed from nav: Hot Videos (home), Best, New, Actress, Studio, Series, Categories, Label, Director.

### Registration checklist for MemoJav

Besides creating `backend/app/scrapers/memojav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=memojav` or `source=memo`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `memojav.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="memojav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MemoJav verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://memojav.com/video/START-579\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://memojav.com/video/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=memojav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://memojav.com/video/START-579"
```

## HoHoJ Implementation Notes

[HoHoJ](https://hohoj.tv/) (å¥½å¥½J) is a JAV catalog site in the GGJAV family (CDN thumbnails on `cdn-*.ggjav.com`, streams on `video-*.ggjav.com`). Video pages use numeric IDs: `/video?id={ID}` (not slug paths). The detail page embeds `/embed?id={ID}`, which exposes the HLS master URL in `<video src="...index.m3u8">` and `var videoSrc = "..."`.

### Host aliases

- `hohoj.tv`
- `www.hohoj.tv`

### Listing and pagination (`list_videos`)

- Home: `https://hohoj.tv/`
- Browse by type (query param `type`):
  - All: `https://hohoj.tv/search?type=all&p=1`
  - Censored: `https://hohoj.tv/search?type=censored&p=1`
  - Chinese subtitles: `https://hohoj.tv/search?type=chinese&p=1`
  - Uncensored: `https://hohoj.tv/search?type=uncensored&p=1`
  - Western: `https://hohoj.tv/search?type=europe&p=1`
- Sort order (optional `order`): `popular` (default), `latest`, `views`, `likes`
- Text search: `https://hohoj.tv/search?text={query}&p=1`
- Actresses index: `https://hohoj.tv/all_models`
- Parse cards in `div.video-item`; links are rendered as `{% if href="/video?id=123" %}` â€” extract with regex `/video?id=\d+`
- Pagination: set/replace query param `p` (page 2 â†’ `p=2`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `h5.mt-3`, `.info` (views/date), `.model` (actress), `.ctg a` (tags)
- Streams: fetch `https://hohoj.tv/embed?id={ID}`; read HLS from `#my-video[src]` or `videoSrc` in inline script
- Always include embed fallback: `https://hohoj.tv/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav/browse: Home, All, Censored, Chinese Subtitles, Uncensored, Western, Actresses.

### Registration checklist for HoHoJ

Besides creating `backend/app/scrapers/hohoj/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hohoj` or `source=hohojtv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `hohoj.tv` and `ggjav.com` (CDN)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="hohoj"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### HoHoJ verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hohoj.tv/video?id=51730\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hohoj.tv/search?type=all&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hohoj"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hohoj.tv/video?id=51730"
```

## GGJAV Implementation Notes

[GGJAV](https://ggjav.com/) is the flagship JAV catalog in the same CDN/player family as [HoHoJ](https://hohoj.tv/) (`cdn-*.ggjav.com`, `video-*.ggjav.com`). Video pages use `/main/video?id={ID}` (numeric catalog id). Streams are not on the bare `/main/embed?id={ID}` page; they come from a base64 player map embedded in the video page.

### Host aliases

- `ggjav.com`
- `www.ggjav.com`
- `ggjav.tv` (mirror)

### Listing and pagination (`list_videos`)

- Home: `https://ggjav.com/`
- Section listings:
  - Censored: `https://ggjav.com/main/censored`
  - Uncensored: `https://ggjav.com/main/uncensored`
  - Amateur: `https://ggjav.com/main/amateur`
  - Chinese subtitles: `https://ggjav.com/main/chinese`
  - Western: `https://ggjav.com/main/europe`
  - Anime: `https://ggjav.com/main/cartoon`
- Text search: `https://ggjav.com/main/search?string={query}`
- Parse cards in `div.item` with `a[href*="/main/video?id="]`; title in `.item_title`, thumb `img.item_image`, views in `.item_views`
- Pagination: query param `page` (site sometimes emits `&&page` â€” normalize to `&page`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `.title_text`, `.info img`, `.ctg_button` / `.ctg a`, optional `.model .model_name`
- Player map: `var l = "{base64}"` on the video page â†’ decode (`b64` then subtract `0x58` per byte) â†’ JSON object `links.{server}[]`
- Preferred HLS path: `links.ggjav[0]` is `/main/embed?u={base64_mp4_path}&poster=...` â†’ decode `u` â†’ append `/index.m3u8` to the `.mp4` base URL (e.g. `https://video-6.ggjav.com/video_1/...mp4/index.m3u8`)
- Alternate embed fallbacks: `mmfl04`, `mmsw02`, `embedrise`, `tapewithadblock`, etc. from the same `links` map
- Embed fallback: `https://ggjav.com/main/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav: Home, Censored, Uncensored, Amateur, Chinese Subtitles, Western, Anime, All Actresses, Uncensored Actresses.

### Registration checklist for GGJAV

Besides creating `backend/app/scrapers/ggjav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=ggjav` or `source=ggjavtv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `ggjav.com`, `ggjav.tv`, and `video-*.ggjav.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="ggjav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### GGJAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ggjav.com/main/video?id=256833\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ggjav.com/main/uncensored&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=ggjav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ggjav.com/main/video?id=256833"
```

## Porn87 Implementation Notes

[Porn87](https://porn87.com/) is a user-upload JAV/homemade catalog (GGJAV-family CDN: `cdn-*.porn87.com`, HLS on `cdn-*.porn87.com/media/video_*`). Video pages use `/main/html?id={ID}` (not `/main/video`). The player lives at `/main/embed?id={ID}` with direct HLS in `<video src="...index.m3u8">` / `videoSrc`.

### Host aliases

- `porn87.com`
- `www.porn87.com`
- `porn87.tv` (mirror)

### Listing and pagination (`list_videos`)

- Home: `https://porn87.com/`
- Latest: `https://porn87.com/main/tag?lineup=create_time`
- Popular: `https://porn87.com/main/tag?lineup=recent_views`
- Tag browse: `https://porn87.com/main/tag?name={tag}` (e.g. `é«˜æ¸…æ—¥æœ¬AV`, `ä¸­æ¸¯å°`)
- Text search: `https://porn87.com/main/search?name={query}`
- Parse cards in `div.chunk > a[href*="/main/html?id="]`; thumb `img.video_thumbnail`, duration `.video_time`, views/likes via `fi-eye` / `fi-heart`
- Pagination: query param `page` is **1-based** (UI page 2 â†’ `page=2`; API `page=1` omits the param)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, title spans, `.video_time`, tag links (`/main/tag?name=`), optional model links
- Streams: fetch `https://porn87.com/main/embed?id={ID}` â†’ read HLS from `#my-video[src]` or `var videoSrc`
- Optional multi-server map on the HTML page: same `var l = "{base64}"` decode as GGJAV (`b64` then subtract `0x58` per byte) for external embed fallbacks
- Embed fallback: `https://porn87.com/main/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav: Home, Latest, Popular, HD Japanese AV, Asian Homemade (ä¸­æ¸¯å°), All Tags, Actresses.

### Registration checklist for Porn87

Besides creating `backend/app/scrapers/porn87/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=porn87` or `source=porn87tv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `porn87.com`, `porn87.tv`, and `cdn-*.porn87.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="porn87"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Porn87 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://porn87.com/main/html?id=5952\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://porn87.com/main/tag?lineup=create_time&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=porn87"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://porn87.com/main/html?id=5952"
```

## GoodAV (æ­£å¦¹AV) Implementation Notes

[GoodAV](http://goodav17.com/) (`goodav17.com`) is a JAV catalog in the GGJAV CDN family. Video pages use `/html/{ID}/`; playback is via an embedded `ggjav.com/main/embed?u={base64_mp4_path}&site=goodav` iframe (HLS on `video-*.ggjav.com` / `cdn-*.ggjav.com`).

### Host aliases

- `goodav17.com`
- `www.goodav17.com`

### Listing and pagination (`list_videos`)

- Home (latest): `http://goodav17.com/` â€” page *n* &gt; 1 is `http://goodav17.com/{n}/`
- Types: `http://goodav17.com/type/{name}/{page}/` (e.g. `/type/ç„¡ç¢¼/1/`, page 2 â†’ `/type/ç„¡ç¢¼/2/`)
- Actresses: `http://goodav17.com/actor/{name}/{page}/`
- VR: `http://goodav17.com/vr/{page}/`
- Homemade: `http://goodav17.com/local/{page}/`
- Parse cards in `div.movie` â†’ `a[href*="/html/"]`; thumbs from `img` (`src`, `large_image` on `cdn-*.ggjav.com`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, title, tag/actor links (`/type/`, `/actor/`)
- Streams: read `iframe#video_frame` â†’ GGJAV embed URL â†’ decode `u` query (base64 MP4 path) â†’ `{path}/index.m3u8`, or fetch embed HTML for `videoSrc` (same helpers as `ggjav` scraper)
- Embed fallback: the GGJAV embed URL from the iframe

### Categories (`get_categories`)

Seed from nav: Home, sample types (ç„¡ç¢¼, äººå¦», å·¨ä¹³, ä¸­å‡º), VR, Asian Homemade, sample actress.

### Registration checklist for GoodAV

Besides creating `backend/app/scrapers/goodav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=goodav` or `source=goodav17`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks (`goodav17.com`; media CDN already covered via `ggjav.com` / `video-*.ggjav.com`)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="goodav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### GoodAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"http://goodav17.com/html/20818/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=http://goodav17.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=http://goodav17.com/type/%E7%84%A1%E7%A2%BC/1/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=goodav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=http://goodav17.com/html/20818/"
```

## KanAV Implementation Notes

[KanAV](https://kanav.ad/) (`kanav.ad`) is a MacCMS (è‹¹æžœCMS) JAV site. Listings link to play pages; the player exposes `player_aaaa` JSON with `encrypt: 2` and a base64-encoded HLS URL (decoded per MacCMS `player.js`: base64 then `unescape`).

### Host aliases

- `kanav.ad`
- `www.kanav.ad`

### Listing and pagination (`list_videos`)

- Home: `https://kanav.ad/` (section grids; for page &gt; 1 prefer a type URL)
- Categories: `https://kanav.ad/index.php/vod/type/id/{type_id}.html`
- Page *n* &gt; 1: `https://kanav.ad/index.php/vod/type/id/{type_id}/page/{n}.html`
- Parse `a[href*="/index.php/vod/play/id/"]`; merge duplicate IDs; title from link text or `img[alt]`
- Thumbs on `img.11yun.xyz`

### Metadata and streams (`scrape`)

- Canonical play URL: `https://kanav.ad/index.php/vod/play/id/{ID}/sid/1/nid/1.html`
- Also accept `/index.php/vod/detail/id/{ID}.html` (same ID, fetches play page)
- Streams: parse `player_aaaa={...}` from play HTML â†’ `"url"` field â†’ base64 decode when `encrypt==2` â†’ `.m3u8` on `*.11yun.space` / `*.11yun.xyz`
- Title from `vod_data.vod_name`, `og:title`, or `<title>`

### Categories (`get_categories`)

Seed from nav type links: Home, ä¸­æ–‡å­—å¹• (id=1), æ—¥éŸ©æœ‰ç , æ—¥éŸ©æ— ç , å›½äº§AV, etc.

### Registration checklist for KanAV

Besides creating `backend/app/scrapers/kanav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=kanav`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `kanav.ad`, `11yun.xyz`, `11yun.space`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="kanav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### KanAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://kanav.ad/index.php/vod/play/id/111060/sid/1/nid/1.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://kanav.ad/index.php/vod/type/id/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://kanav.ad/index.php/vod/type/id/1.html&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=kanav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://kanav.ad/index.php/vod/play/id/111060/sid/1/nid/1.html"
```

## MissAV Implementation Notes

[MissAV](https://missav.ai/) is a JAV catalog site. Video pages use a DVD-style slug (`fc2-ppv-1434674`, `ssni-123`, etc.) with optional locale prefix (`/en/`, `/ja/`, â€¦). Thumbnails and previews are served from `fourhoi.com`; HLS playback uses obfuscated `surrit.com` URLs in an inline `eval(...)` player block.

### Host aliases

- `missav.ai`
- `www.missav.ai`

### Listing and pagination (`list_videos`)

- Browse URLs use a rotating `dm{id}` prefix, e.g. `https://missav.ai/dm428/fc2`, `https://missav.ai/dm539/new`
- Localized browse: `https://missav.ai/dm428/en/fc2`
- Parse cards in `div.thumbnail` â†’ `a[href]` to `https://missav.ai/{slug}` or `https://missav.ai/en/{slug}`; thumb `img[data-src]` (`fourhoi.com/{slug}/cover-t.jpg`), duration in `span.absolute.bottom-1.right-1`
- Pagination: query param `page` (page 2 â†’ `?page=2`). Preserve the full `dm{id}/â€¦` path from `base_url` (the numeric `dm` segment can change between mirrors)

### Metadata and streams (`scrape`)

- Canonical page: `https://missav.ai/en/{dvd-slug}` (also accept `https://missav.ai/{dvd-slug}` and mirror paths like `https://missav.ai/dm1/en/{dvd-slug}`)
- Metadata: `og:title`, `og:image` (`fourhoi.com/{slug}/cover-n.jpg`), `og:video:duration` (seconds), `og:video:release_date`, `<h1>`, actress/genre links
- Streams: locate `eval(function(p,a,c,k,e,d){...}('e=\'...\';c=\'...\';b=\'...\';',15,15,'m3u8|...|surrit|https|...'.split('|'),0,{}))` â†’ decode digit placeholders against the split array; `d` in the template is the `dvdId` slug â†’ master HLS is variable `e`, e.g. `https://surrit.com/{hash}/{dvd-slug}.m3u8`
- `dvdId` is also exposed in Alpine `x-data` as `dvdId: 'fc2-ppv-1434674'`

### Categories (`get_categories`)

Seed from nav `dm*` links: New Releases, Recent Update, Uncensored Leak, Chinese Subtitle, FC2, hot lists, SIRO, LUXU, HEYZO, etc.

### Registration checklist for MissAV

Besides creating `backend/app/scrapers/missav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=missav` or `source=missavai`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `missav.ai` and `surrit.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="missav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MissAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://missav.ai/en/fc2-ppv-1434674\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://missav.ai/dm428/fc2&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=missav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://missav.ai/en/fc2-ppv-1434674"
```

## Jable Implementation Notes

[Jable.TV](https://jable.tv/) is a JAV catalog site. Video pages use slug URLs under `/videos/{code}/` (e.g. `start-579`). HLS is exposed inline as `var hlsUrl = '...m3u8'` on the player page (CDN hosts such as `*.mushroomtrack.com`).

### Host aliases

- `jable.tv`
- `www.jable.tv`

### Listing and pagination (`list_videos`)

- Latest: `https://jable.tv/latest-updates/`
- Hot: `https://jable.tv/hot/`
- New release: `https://jable.tv/new-release/`
- Categories: `https://jable.tv/categories/{slug}/`
- Tags: `https://jable.tv/tags/{slug}/`
- Parse cards in `div.video-img-box` â†’ `.img-box a[href*='/videos/']`; title in `h6.title a`; thumb `img[data-src]`; duration in `span.label`; views in `p.sub-title`
- Pagination: append page segment â€” page 2 of latest is `https://jable.tv/latest-updates/2/`

### Metadata and streams (`scrape`)

- Canonical page: `https://jable.tv/videos/{slug}/` (also accept mirror paths like `https://jable.tv/s0/videos/{slug}/`)
- Metadata: `og:title`, `og:image`, `section.video-info h4`, actress links in `.models`, tags in `h5.tags a`, views in `h6 span`, release date in `.header-right span.inactive-color`
- Streams: `var hlsUrl = 'https://.../*.m3u8'` in inline script next to `#player` (Hls.js / Plyr)

### Categories (`get_categories`)

Seed from nav: Latest Updates, Hot, New Release, Categories index, sample category/tag pages (Roleplay, Chinese Subtitle, Uniform, Pantyhose, NTR).

### Registration checklist for Jable

Besides creating `backend/app/scrapers/jable/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=jable` or `source=jabletv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `jable.tv`, `assets-cdn.jable.tv`, `mushroomtrack.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="jable"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Jable verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://jable.tv/videos/start-579/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://jable.tv/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=jable"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://jable.tv/videos/start-579/"
```

## Tianmei (å¤©ç¾Žå½±é™¢ / 94mt.cc) Implementation Notes

[å¤©ç¾Žå½±é™¢](https://www.94mt.cc/) (`94mt.cc`, easy domain `tianmei.one`) is a MacCMS (è‹¹æžœCMS) Chinese adult catalog. Video pages use numeric IDs under `/index.php/vod/play/id/{ID}/sid/1/nid/1.html`. Streams come from inline `player_aaaa` JSON; this site typically uses `"encrypt":0` with a plain `"url"` HLS field (not base64 like some `encrypt:2` mirrors).

### Host aliases

- `94mt.cc`
- `www.94mt.cc`
- `tianmei.one` (alternate domain)

### Listing and pagination (`list_videos`)

- Home: `https://www.94mt.cc/`
- Categories: `https://www.94mt.cc/index.php/vod/type/id/{type_id}.html` (e.g. `1` = éº»è±†è§†é¢‘)
- Parse `div.box-item` â†’ `a.item-link` / `a.movie-name`; title from `a.movie-name` or `title` attr; thumb `img[src]`; optional `upload_date` from `em span`
- Pagination: `/index.php/vod/type/id/{type_id}/page/{n}.html` (page 2 â†’ `.../page/2.html`)

### Metadata and streams (`scrape`)

- Canonical play URL: `https://www.94mt.cc/index.php/vod/play/id/{ID}/sid/1/nid/1.html`
- Also accept `/index.php/vod/detail/id/{ID}.html` (same ID, fetches play page)
- Streams: parse `player_aaaa={...}` â†’ when `encrypt==0`, use `"url"` directly (e.g. `https://*.cdn2020.com/.../index.m3u8`); when `encrypt==2`, base64 decode then `unescape` (MacCMS `player.js`)
- Title from `vod_data.vod_name`, `<title>`, or meta keywords

### Categories (`get_categories`)

Seed from nav type links: Home, éº»è±†è§†é¢‘, 91åˆ¶ç‰‡åŽ‚, å¤©ç¾Žå½±é™¢, èœœæ¡ƒä¼ åª’, etc. (`/index.php/vod/type/id/1.html` â€¦).

### Registration checklist for Tianmei

Besides creating `backend/app/scrapers/tianmei/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=tianmei`, `source=94mt`, or `source=94mtcc`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `94mt.cc`, `cdn2020.com`, `tutu1.space`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="tianmei"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Tianmei verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.94mt.cc/index.php/vod/play/id/25106/sid/1/nid/1.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.94mt.cc/index.php/vod/type/id/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tianmei"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.94mt.cc/index.php/vod/play/id/25106/sid/1/nid/1.html"
```

## BindasMood (bindasmood.com) Implementation Notes

[BindasMood](https://bindasmood.com/) is a WordPress site using the **UltimaTube** theme. Video posts use root-level slugs (not `/video/` paths). Listing uses `article.thumb-block` cards; streams are usually direct **MP4** URLs on CDN hosts (e.g. `ixifile.xyz`) embedded in post HTML, with an optional **clean-tube-player** iframe fallback.

### Host aliases

- `bindasmood.com`
- `www.bindasmood.com`

### Listing and pagination (`list_videos`)

- Home: `https://bindasmood.com/`
- Sort filters: `?filter=latest`, `?filter=popular`, `?filter=most-viewed`, `?filter=longest`, `?filter=random`
- Taxonomy indexes: `/categories/`, `/tags/`, `/actors/` (and `/category/{slug}/`, `/tag/{slug}/`, `/actor/{slug}/` for filtered lists)
- Parse `article.thumb-block` â†’ link `a[href]`; title from `span.title a`; thumb `img`; `span.duration`, `span.views`
- Pagination: WordPress `/page/{n}/` (e.g. `https://bindasmood.com/page/2/`); query preserved on filtered home URLs

### Metadata and streams (`scrape`)

- Canonical post URL: `https://bindasmood.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `tags`, `actors`, `category`, `tag`, `actor`, `page`, `wp-content`, etc.
- Streams: regex `.mp4` / `.m3u8` from post HTML; if none, fetch `clean-tube-player` iframe (`player-x.php`) and retry; last resort `format: embed` on iframe `src`
- Title/thumb from `og:title`, `og:image`, `h1`

### Categories (`get_categories`)

Home, Newest (`?filter=latest`), Best, Most Viewed, Longest, plus taxonomy index links in `categories.json`.

### Registration checklist for BindasMood

Besides creating `backend/app/scrapers/bindasmood/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=bindasmood` or `source=bindas`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `bindasmood.com`, `ixifile.xyz`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="bindasmood"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### BindasMood verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://bindasmood.com/valentine-date-2026-hindi-uncut-xxx-video/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bindasmood.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bindasmood.com/page/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=bindasmood"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://bindasmood.com/valentine-date-2026-hindi-uncut-xxx-video/"
```

## DOTMaal (dotmaal.com) Implementation Notes

[DOTMaal](https://dotmaal.com/) is a WordPress site using the **OGP** theme with the **xplayer** plugin. It aggregates Hindi/Indian OTT web series (ULLU, Atrangii, Pull, Desi Prime, Kahani Play, etc.). Episode pages use a two-segment path: `/{platform}/{episode-slug}/`. Listing cards use `div.vc-wrap` with thumb link `a.vc-thumb`, title `a.vc-title`, duration `span.vc-duration`, and OTT badge `span.vc-badge`.

### Host aliases

- `dotmaal.com`
- `www.dotmaal.com`

### Listing and pagination (`list_videos`)

- Home: `https://dotmaal.com/`
- Indexes: `/web-series/`, `/ott/`, `/models/`, `/tags/`
- Taxonomy: `/category/{slug}/` (OTT/network), `/tag/{slug}/`, `/model/{slug}/`, `/web-series/{series-slug}/`
- Parse `div.vc-wrap` â†’ `a.vc-thumb` / `a.vc-title`; thumb `img`; `span.vc-duration`; `span.vc-badge` as `uploader_name`
- Pagination: WordPress `/page/{n}/` on any list path (e.g. `https://dotmaal.com/page/2/`, `https://dotmaal.com/category/ullu/page/2/`)

### Metadata and streams (`scrape`)

- Canonical episode URL: `https://dotmaal.com/{platform}/{episode-slug}/` (reject reserved first segments: `category`, `tag`, `model`, `web-series`, `page`, etc.)
- Streams: `<video><source src="...">` on the episode page (signed MP4 on `video.maalcdn.com`); HTML-entity decode URLs (`&#038;` â†’ `&`); regex fallback for `.mp4` / `.m3u8`
- Title/thumb from `og:title`, `og:image`, `h1`, `video[poster]`

### Categories (`get_categories`)

Home, Web Series, OTT, Models, Tags, plus popular OTT networks (ULLU, Atrangii, Rabbit, ALTT, CinePrime, Kooku, Pull, Desi Prime, Kahani Play, Wow, Tru Uncut, Feel) in `categories.json`.

### Registration checklist for DOTMaal

Besides creating `backend/app/scrapers/dotmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=dotmaal` or `source=dot`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `dotmaal.com`, `maalcdn.com`, `video.maalcdn.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="dotmaal"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DOTMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://dotmaal.com/pull/tadap-pull-episode-2/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://dotmaal.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://dotmaal.com/category/ullu/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=dotmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://dotmaal.com/pull/tadap-pull-episode-2/"
```

## UncutMasti (uncutmasti.com) Implementation Notes

[UncutMasti](https://uncutmasti.com/) is a WordPress site using the **UltimaTube** theme (same stack as BindasMood). Video posts use a single root-level slug (`/{slug}/`). Listing uses `article.thumb-block` cards; streams are direct **MP4** URLs on CDN hosts (e.g. `cdn2.ixifile.xyz`) embedded in post HTML or resolved via the **clean-tube-player** iframe (`player-x.php`).

### Host aliases

- `uncutmasti.com`
- `www.uncutmasti.com`

### Listing and pagination (`list_videos`)

- Home: `https://uncutmasti.com/`
- Sort filters: `?filter=latest`, `?filter=popular`, `?filter=most-viewed`, `?filter=longest`, `?filter=random`
- Taxonomy indexes: `/categories/`, `/tags/`, `/actors/` (and `/category/{slug}/`, `/tag/{slug}/`, `/actor/{slug}/` for filtered lists)
- Parse `article.thumb-block` â†’ link `a[href]`; title from `span.title a`; thumb `img`; `span.duration`, `span.views`
- Pagination: WordPress `/page/{n}/` (e.g. `https://uncutmasti.com/page/2/`); query preserved on filtered home URLs

### Metadata and streams (`scrape`)

- Canonical post URL: `https://uncutmasti.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `tags`, `actors`, `category`, `tag`, `actor`, `page`, `wp-content`, etc.
- Streams: regex `.mp4` / `.m3u8` from post HTML; if none, fetch `clean-tube-player` iframe (`player-x.php`) and retry; last resort `format: embed` on iframe `src`
- Title/thumb from `og:title`, `og:image`, `h1`

### Categories (`get_categories`)

Home, Latest/Popular/Most viewed/Longest/Random filters, Categories/Tags/Actors indexes, plus popular OTT category links in `categories.json`.

### Registration checklist for UncutMasti

Besides creating `backend/app/scrapers/uncutmasti/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=uncutmasti` or `source=masti`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `uncutmasti.com`, `ixifile.xyz`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="uncutmasti"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### UncutMasti verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://uncutmasti.com/mona-darling-2026-moodx-hindi-xxx-web-series-episode-2/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmasti.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmasti.com/category/bindastimes-uncut-web-series/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=uncutmasti"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://uncutmasti.com/mona-darling-2026-moodx-hindi-xxx-web-series-episode-2/"
```

## ZMaal (zmaal.net) Implementation Notes

[ZMaal](https://zmaal.net/) is a WordPress site for Hindi uncut web series. The main video feed is at `/latest/`. Posts use a single root-level slug (`/{slug}/`). Listing uses `article.video` cards with `a.link`, `img`, and `span.rtitle`. Streams are signed **MP4** URLs on `video.maalcdn.com` (same CDN family as DOTMaal), embedded via `<video><source>` or HTML regex fallback.

### Host aliases

- `zmaal.net`
- `www.zmaal.net`

### Listing and pagination (`list_videos`)

- Primary feed: `https://zmaal.net/latest/`
- Indexes: `/model/`, `/web-series/`, `/hot-web-series/`
- Site search: `?s={query}` (e.g. `?s=Ullu`, `?s=Moodx`)
- Parse `article.video` â†’ `a.link[href]`; title from `aria-label`, `title`, or `span.rtitle`; thumb `img`
- Pagination: `/latest/page/{n}/` (e.g. `https://zmaal.net/latest/page/2/`); works on any list path with WordPress-style `/page/{n}/` suffix

### Metadata and streams (`scrape`)

- Canonical post URL: `https://zmaal.net/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `latest`, `model`, `web-series`, `hot-web-series`, `page`, `wp-content`, etc.
- Streams: `<video><source src="...">` and regex `.mp4` / `.m3u8`; HTML-entity decode URLs (`&#038;` â†’ `&`)
- Title/thumb from `og:title`, `og:image`, `h1`, `video[poster]`

### Categories (`get_categories`)

Latest feed, Home, Models, Web Series indexes, and popular keyword searches (`?s=Ullu`, etc.) in `categories.json`.

### Registration checklist for ZMaal

Besides creating `backend/app/scrapers/zmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=zmaal`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `zmaal.net`, `maalcdn.com`, `video.maalcdn.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="zmaal"`, `baseUrl=https://zmaal.net/latest/`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### ZMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://zmaal.net/husband-friend/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zmaal.net/latest/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zmaal.net/latest/page/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=zmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://zmaal.net/pastry-episode-1/"
```

## Ullu Web Series (ulluwebseries.one) Implementation Notes

[ulluwebseries.one](https://ulluwebseries.one/) is a WordPress site (Astra + Content Views grid) for ULLU and other Hindi uncut OTT web series. Video posts live under `/hot-series/{slug}/`. The home page and taxonomy archives use **Content Views** cards (`div.pt-cv-content-item`) with thumb link, `h4.pt-cv-title`, and `images.ulluwebseries.one` thumbnails. Streams are direct **MP4** on `cdn.ulluwebseries.one` via `<video><source>` or HTML regex.

### Host aliases

- `ulluwebseries.one`
- `www.ulluwebseries.one`

### Listing and pagination (`list_videos`)

- Home: `https://ulluwebseries.one/`
- Indexes: `/categories/`, `/series/`, `/models/`, `/audio-sex-story/`
- OTT filters: `/series_category/{slug}/` (e.g. `/series_category/ullu/`, `/series_category/moodx/`)
- Parse `div.pt-cv-content-item` â†’ `a.pt-cv-href-thumbnail` / `h4.pt-cv-title a`; thumb `img.pt-cv-thumbnail`
- Pagination: WordPress `/page/{n}/` (e.g. `https://ulluwebseries.one/page/2/`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://ulluwebseries.one/hot-series/{slug}/`
- Reject non-video paths (`/series/`, `/categories/`, `/models/`, etc.) â€” only `/hot-series/` posts are scraped
- Streams: `<video><source src="...">` and regex `.mp4` / `.m3u8` on `cdn.ulluwebseries.one`
- Title/thumb from `og:title`, `og:image`, `h2`, `<title>`

### Categories (`get_categories`)

Home, Categories, Series, Models, Audio Sex Story, plus OTT `series_category` links (ULLU, MoodX, HotHit, etc.) in `categories.json`.

### Registration checklist for ulluwebseries.one

Besides creating `backend/app/scrapers/ulluwebseries/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=ulluwebseries` or `source=ulluws`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `ulluwebseries.one`, `cdn.ulluwebseries.one`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="ulluwebseries"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Ullu Web Series verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ulluwebseries.one/hot-series/boss-malayalam-uncut-web-series-boomex-2025/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ulluwebseries.one/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ulluwebseries.one/series_category/ullu/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=ulluwebseries"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ulluwebseries.one/hot-series/tu-haan-kar-ya-naa-kar-ullu-web-series-e06/"
```

## DesiThotHub (desithothub.com) Implementation Notes

[DesiThotHub](https://desithothub.com/) is a custom WordPress-style site for desi live/cam and MMS-style videos. Posts use a single root-level slug (`/{slug}/`). Listing uses `div.thumb` cards with `a.card`, `h2.card-title`, `img`, and `span.time-ago`. Playback uses a **server dropdown** (`button.srv-drop-item`) with one `div.video-unit` per host â€” only **embed** streams are returned (no direct `.mp4` extraction).

### Host aliases

- `desithothub.com`
- `www.desithothub.com`

### Listing and pagination (`list_videos`)

- Home (newest): `https://desithothub.com/`
- Feeds: `/popular/`, `/favourites/`
- Taxonomy: `/categories/`, `/categories/{slug}/` (e.g. `/categories/tamil/`, `/categories/mallu/`)
- Parse `div.thumb` â†’ `a.card`; title `h2.card-title`; thumb `img`; `span.time-ago`
- Pagination: WordPress `/page/{n}/` (e.g. `https://desithothub.com/page/2/`)

### Metadata and streams (`scrape`)

- Canonical post URL: `https://desithothub.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `popular`, `newest`, `tags`, `favourites`, `page`, etc.
- Streams: parse `button.srv-drop-item` labels paired with `div.video-unit` entries
  - Sendvid: `iframe.vid-max-iframe` `src` (e.g. `https://sendvid.com/embed/{id}`)
  - Other hosts: `a.vid-maxwrap[href]` watch URLs converted to embed where possible (`streamtape.com/v/â€¦` â†’ `/e/â€¦`, `lulustream.com/â€¦` â†’ `/e/â€¦`, `vinovo.to/d/â€¦` â†’ `/embed/â€¦`, etc.); GoFile/VikingFile/Upfiles use page URL with `format: embed`
- All stream entries use `format: "embed"` only â€” do not regex-extract direct MP4 links from HTML
- Default stream prefers Sendvid embed
- Title/thumb from `og:title`, `og:image`, `h1`/`h2`

### Categories (`get_categories`)

Newest, Popular, Categories index, Favourites, plus popular tags (Tamil, Mallu, Bengali, Big Boobs, etc.) in `categories.json`.

### Registration checklist for DesiThotHub

Besides creating `backend/app/scrapers/desithothub/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desithothub` or `source=thothub`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `desithothub.com`, `streamtape.com`, `sendvid.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="desithothub"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiThotHub verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://desithothub.com/tamil-madhu-aunty-nude-premium-live-show/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desithothub.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desithothub.com/categories/tamil/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desithothub"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://desithothub.com/tamil-madhu-aunty-nude-premium-live-show/"
```

## Eporner (eporner.com) Implementation Notes

[Eporner](https://www.eporner.com/) is a large tube site with mandatory age verification in some regions. Video pages use alphanumeric IDs under `/video-{id}/{slug}/` (legacy `/hd-porn/{id}/{slug}/`). **Embed player URLs** (`/embed/{id}/`, used in iframes) are first-class scrape targets and always expose an `format: "embed"` stream for WebView/iframe playback. Streams are resolved via the site XHR API (hash from page HTML), with fallbacks to the public v2 metadata API and HTML `<source>` / MP4 regex extraction.

### Host aliases

- `eporner.com`
- `www.eporner.com`

### Listing and pagination (`list_videos`)

- Home: `https://www.eporner.com/`
- Feeds: `/recent/`, `/popular/`, `/top-rated/`, `/longest/`, `/4k/`, `/cats/`
- Parse `div.mb` cards â†’ `a[href*="/video-"]` or `/hd-porn/`; title from `.mbtit a`; thumb `img`; duration `.mbtim`; views `.mbvie`
- Pagination: append page number to path (e.g. `/recent/2/`, home page 2 â†’ `/2/`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://www.eporner.com/video-{id}/{slug}/`
- Embed player URL: `https://www.eporner.com/embed/{id}/` (iframe `src`, e.g. `https://www.eporner.com/embed/5avQdSA3oMK/`)
- When scraping an embed URL, the response `url` stays on `/embed/{id}/`; direct MP4/HLS are resolved from the embed page or the linked full video page; an embed stream is always included
- **Primary streams:** parse `hash` (32-char hex) from page â†’ `GET /xhr/video/{id}?hash={calc_hash}&device=generic&domain=www.eporner.com&fallback=false` â†’ `sources` dict (MP4 + HLS)
- **calc_hash:** split hash into four 8-char hex chunks, each encoded to base-36 (same as yt-dlp `EpornerIE`)
- **Fallback streams:** `GET /api/v2/video/search/?id={id}&per_page=1&thumbsize=big` â†’ `all_qualities` MP4 URLs on `static.eporner.com`
- **HTML fallback:** `<video><source>` tags and `.mp4` / `.m3u8` regex
- Fetch uses `curl_cffi` impersonation when available (helps with age gate / blocks), then shared `pool.fetch_html`

### Categories (`get_categories`)

Home, Recent, Popular, Top Rated, Longest, 4K, Categories index (`categories.json`).

### Registration checklist for Eporner

Besides creating `backend/app/scrapers/eporner/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=eporner`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `eporner.com`, `static.eporner.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="eporner"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Eporner verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.eporner.com/video-FJsA19J3Y3H/one-of-the-greats/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.eporner.com/recent/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.eporner.com/recent/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=eporner"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.eporner.com/video-FJsA19J3Y3H/one-of-the-greats/"

curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.eporner.com/embed/5avQdSA3oMK/\"}"
```

## Motherless (motherless.xxx) Implementation Notes

[Motherless](https://motherless.xxx/) is a user-upload host. The site moved from `motherless.com` to **`motherless.xxx`** (the old domain redirects). Single videos use a hex media code at the site root (e.g. `https://motherless.xxx/EE97006`). Category and tag browsing uses `/term/videos/{slug}`; feeds live under `/videos`, `/videos/recent`, etc. MP4 streams are served from `*.motherlessmedia.com` (SD and `-720p` variants).

### Host aliases

- `motherless.xxx` (primary)
- `www.motherless.xxx`
- `motherless.com` (legacy redirect; still accepted by scraper)
- `www.motherless.com`
- `*.motherlessmedia.com` (CDN, for direct MP4 proxying)

### Listing and pagination (`list_videos`)

- Home: `https://motherless.xxx/`
- Videos hub: `https://motherless.xxx/videos`
- Feeds: `/videos/recent`, `/videos/favorited`, `/videos/viewed`, `/videos/commented`
- Categories/tags: `https://motherless.xxx/term/videos/{slug}` (e.g. `amateur`, `milf`)
- Parse `div.thumb-container.video` blocks: `data-codename`, full `href="https://motherless.xxx/{ID}"`, title in `a.caption.title`, duration in `span.size`, views in `span.hits .value`, uploader in `a.uploader`
- Fallbacks: loose `href=".../{ID}" title="..."` regex and `data-codename="ID"` attributes
- Pagination: `?page=N` query parameter (page 1 omits `page`)
- Canonical listing URLs normalize to `motherless.xxx` even when HTML still links to `motherless.com`

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://motherless.xxx/{HEX_ID}` (also `https://motherless.xxx/g/{group}/{HEX_ID}`, `/iframe/{ID}`)
- **Primary streams (signed):** `__fileurl = '...'` and `<video><source src="..." res="720p">` from the watch page (URLs include `validfrom` / `hash` query params)
- **Fallback:** unsigned `cdn{N}-videos.motherlessmedia.com/videos/{ID}.mp4` patterns only when HTML lacks sources
- Metadata: `.media-meta-title h1`, `og:image`, `.media-meta-info span.count` for views, `/m/{user}` uploader, `/term/videos/` tags
- Exclude gallery-only paths matching `G[VIGF]?[A-F0-9]+` (e.g. `/GV338999F`)

Send `Cookie: age_verified=1` on fetch to bypass the age gate when possible.

### Categories (`get_categories`)

Home, Recent, Favorited, Viewed, Commented, plus popular straight tags (Amateur, Homemade, Teen, MILF, Asian, etc.) in `categories.json`.

### Registration checklist for Motherless

Besides creating `backend/app/scrapers/motherless/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=motherless`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `motherless.xxx`, `motherless.com`, `motherlessmedia.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="motherless"`, `baseUrl="https://motherless.xxx/"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Motherless verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://motherless.xxx/EE97006\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://motherless.xxx/videos/recent&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://motherless.xxx/term/videos/amateur&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=motherless"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://motherless.xxx/EE97006"
```

## YouJizz (youjizz.com) Implementation Notes

[YouJizz](https://www.youjizz.com/) is a mobile-oriented tube site. Watch URLs use a numeric ID in the path: `/videos/{slug}-{id}.html` or `/videos/-{id}.html`. Streams are exposed in a `dataEncodings` JSON array on the watch page (signed MP4/HLS CDN URLs on `*.youjizz.com`).

### Host aliases

- `youjizz.com`
- `www.youjizz.com`

### Listing and pagination (`list_videos`)

- Popular: `https://www.youjizz.com/most-popular/1.html`
- Newest: `https://www.youjizz.com/newest-clips/1.html`
- Top: `/top-rated/1.html`, `/top-rated-week/1.html`, `/top-rated-month/1.html` (not `/top-week/` or `/top/`)
- HD: `/highdefinition/1.html` (not `/hd/`)
- Tags: `/categories/{slug}-1.html` (not `/tags/{slug}/1.html`)
- Random: `https://www.youjizz.com/random` (no numbered pages)
- Parse `div.video-thumb[data-videoId]` â†’ `.video-title a`, `span.time`, `.format-views`, `img[data-original]`
- Pagination: `/most-popular/2.html` for feeds; `/categories/milf-2.html` for tags (read `#urlPattern` from page 1 HTML when needed)

### Metadata and streams (`scrape`)

- Watch URL: `https://www.youjizz.com/videos/{slug}-{id}.html`
- Embed URL: `https://www.youjizz.com/videos/embed/{id}`
- **Primary streams:** parse `dataEncodings = [{ "quality", "filename", "name" }, ...]` (balanced-bracket JSON parse)
- Fallback: `<video><source src="...">` and `encodings = [...];` assignment
- Normalize `//cdnâ€¦` filenames to `https://`
- Metadata: `og:title`, `og:image`, `og:video:duration`, `meta keywords`, Runtime span, Uploaded By regex

Send `Cookie: age_verified=1` on fetch to bypass the age gate when possible.

### Categories (`get_categories`)

Popular, Newest, Top Week/Month/All, Trending, Random, HD, and sample tags in `categories.json`.

### Registration checklist for YouJizz

Besides creating `backend/app/scrapers/youjizz/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=youjizz`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `youjizz.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="youjizz"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### YouJizz verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youjizz.com/videos/busty-redhead-filled-with-cum-77924611.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.youjizz.com/most-popular/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.youjizz.com/most-popular/1.html&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=youjizz"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.youjizz.com/videos/busty-redhead-filled-with-cum-77924611.html"
```

## PornOne (pornone.com) Implementation Notes

[PornOne](https://pornone.com/) (formerly vPorn) is a general tube site. Watch URLs use a numeric ID at the end of a three-segment path: `https://pornone.com/{category}/{slug}/{id}/` (e.g. `/foursome/french-teen-ang-granny-fucked-by-young-guys-in-good-foursome/280598663/`). Shorts use a different pattern (`/shorts/t/{id}/`) and are excluded from listing.

### Host aliases

- `pornone.com`
- `www.pornone.com`
- Stream/thumb CDNs: `s307.pornone.com`, `s308.pornone.com`, `th-eu*.pornone.com`, `cdn-eu-g*.pornone.com`

### Listing and pagination (`list_videos`)

- Home: `https://pornone.com/` (page 2+ â†’ `/2/`, `/3/`, â€¦)
- Newest: `https://pornone.com/newest/` (page 2+ â†’ `/newest/2/`)
- Tags/categories at site root: `/milf/`, `/hd/`, `/teen/` (not `/category/milf/` â€” that 404s)
- Avoid `/popular/` (404 on this host)
- Parse `<a href="/{cat}/{slug}/{id}/">` links; skip locale-prefixed duplicates (`/de/`, `/fr/`, â€¦) and `/shorts/`
- Optional enrichment from inline `related_videos = [{thumb, url, title, duration}, â€¦]` JSON on watch pages

Send `Cookie: age_verified=1; cookies_accepted=1` and `Referer: https://pornone.com/` on fetch.

### Metadata and streams (`scrape`)

- **Primary streams:** `<video id="pornone-video-player"><source src="..." label="720p" res="720">`
- **Fallback:** JSON-LD `contentUrl`, regex for `*.pornone.com/*.mp4`
- Canonical URL from `<link rel="canonical">` or `og:url`
- Metadata: `og:title`, `og:image`, `og:video:duration`, `meta keywords`

### Categories (`get_categories`)

Home, Newest, HD, and sample tag slugs in `categories.json` (all verified to return 200 with video links).

### Registration checklist for PornOne

Besides creating `backend/app/scrapers/pornone/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=pornone`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `pornone.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="pornone"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### PornOne verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pornone.com/foursome/french-teen-ang-granny-fucked-by-young-guys-in-good-foursome/280598663/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornone.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornone.com/newest/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pornone"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://pornone.com/foursome/french-teen-ang-granny-fucked-by-young-guys-in-good-foursome/280598663/"
```

## 3Movs (3movs.com) Implementation Notes

[3Movs](https://www.3movs.com/) is a KVS (Kernel Video Sharing) tube site using `kt_player` with inline `flashvars` on watch pages. Watch URLs use a numeric ID and slug: `https://www.3movs.com/videos/{id}/{slug}/` (e.g. `/videos/455161/hot-teen-erika-slips-out-of-her-lingerie/`). Embed fallback: `https://www.3movs.com/embed/{id}`.

### Host aliases

- `3movs.com`
- `www.3movs.com`
- `img.3movs.com` (thumbnails/CDN)

### Listing and pagination (`list_videos`)

- Home: `https://www.3movs.com/` (page 2+ uses `/latest-updates/{page}/`)
- Latest: `https://www.3movs.com/latest-updates/` (page 2+ â†’ `/latest-updates/2/`)
- Sort feeds: `/most-popular/`, `/top-rated/week/`, `/most-viewed/week/`, `/longest/`
- Categories: `/categories/{slug}/` (page 2+ â†’ `/categories/{slug}/2/`)
- Parse `.thumbs .item.thumb` blocks: `a.wrap_image`, `img[data-src]`, `.time`, `.icon-eye` sibling span
- Preview clips from `img[data-preview]` (short MP4 previews)

Use `curl_cffi` (Chrome impersonation) as primary fetch â€” plain httpx/aiohttp may TLS-timeout on this host.

### Metadata and streams (`scrape`)

- **Primary streams:** `flashvars.video_url` (HQ) and `flashvars.video_alt_url` (LQ) â€” both are `/get_file/...` URLs
- **Fallback:** download links (`360p - Free Download`), embed URL
- Resolve `/get_file/` via HEAD/GET redirect to signed CDN (`*.mjedge.net` or similar)
- Metadata: `og:title`, `og:image`, `ul.list_info` (duration/views/date), `flashvars.video_models`, `flashvars.video_tags`

### Categories (`get_categories`)

Home, Latest Updates, sort feeds, and sample category slugs in `categories.json`.

### Registration checklist for 3Movs

Package folder: `backend/app/scrapers/threemovs/` (Python module name; source aliases: `3movs`, `threemovs`).

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="3movs"`)

### 3Movs verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.3movs.com/videos/455161/hot-teen-erika-slips-out-of-her-lingerie/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.3movs.com/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.3movs.com/categories/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=3movs"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.3movs.com/videos/455161/hot-teen-erika-slips-out-of-her-lingerie/"
```

## HotMovs (hotmovs.tube) Implementation Notes

[HotMovs](https://hotmovs.tube/) is a Tubecup-network tube site (same stack as [TXXX](https://txxx.com/)). It serves JSON list/metadata APIs and obfuscated CDN stream URLs. Watch URLs use a numeric ID and slug: `https://hotmovs.tube/videos/{id}/{slug}/` (e.g. `/videos/13168711/theresa-morning-videos-johntronx/`). Embed fallback: `https://hotmovs.tube/embed/{id}`.

### Host aliases

- `hotmovs.tube`
- `www.hotmovs.tube`
- `hotmovs.com`
- `www.hotmovs.com`
- `tn.hotmovs.com` (thumbnails/CDN)

### Listing and pagination (`list_videos`)

Uses the Tubecup JSON API â€” not HTML scraping:

- **Latest:** `https://hotmovs.tube/latest-updates/` (page 2+ â†’ `/latest-updates/2/`)
- **Sort feeds:** `/most-popular/`, `/longest/`, `/top-rated/`, `/most-viewed/`
- **Categories:** `/categories/{slug}/` (page 2+ â†’ `/categories/{slug}/2/`)
- **Search:** `/search/?s={query}`

List endpoint:

```text
GET /api/videos2.php?params={lifetime}/str/{sort}/{count}/{section}.{object_id}.{page}.all..
```

Search adds `&s={query}` with `sort=relevance`. Response shape: `{ "videos": [ ... ], "total_count", "pages" }`.

Use `curl_cffi` (Chrome impersonation) as primary fetch â€” plain httpx may be blocked or TLS-fail on this host.

### Metadata and streams (`scrape`)

1. **Video info:** `GET /api/json/video/{lifetime}/{million_bucket}/{thousand_bucket}/{id}.json`
   - Example: `/api/json/video/86400/13000000/13168000/13168711.json`
   - `million_bucket = int(1e6 * (id // 1e6))`, `thousand_bucket = 1000 * (id // 1000)`
2. **Stream files:** `GET /api/videofile.php?video_id={id}&lifetime=8640000`
   - Returns array of `{ format, video_url }` where `video_url` is custom-base64-encoded
3. **Decode streams:** translate Cyrillic look-alike chars + `,`/`.`/`~` â†’ standard base64, then decode to CDN URL (often `/get_file/...` on `*.ahcdn.com`)
4. **Resolve `/get_file/`:** follow redirect (no auto-redirect) to signed MP4/HLS URL
5. **Embed fallback:** `https://{host}/embed/{id}` when direct streams fail

### Preview clips

The API `pv` field is often stale/wrong. Build preview URLs from video id when `pv` does not contain the id:

```text
https://vp2.txxx.com/c12/videos/{1000*(id//1000)}/{id}/{id}_tr.mp4
```

### Categories (`get_categories`)

Latest Updates, sort feeds, and sample category slugs in `categories.json`.

### Registration checklist for HotMovs

Package folder: `backend/app/scrapers/hotmovs/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="hotmovs"`)

### HotMovs verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hotmovs.tube/videos/13168711/theresa-morning-videos-johntronx/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hotmovs.tube/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hotmovs.tube/categories/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hotmovs.tube/search/?s=milf&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hotmovs"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hotmovs.tube/videos/13168711/theresa-morning-videos-johntronx/"
```

## ShemaleZ (shemalez.com) Implementation Notes

[ShemaleZ](https://shemalez.com/) is a Tubecup-network tube site (same JSON API stack as [TXXX](https://txxx.com/) and [HotMovs](https://hotmovs.tube/)). It serves JSON list/metadata APIs and obfuscated CDN stream URLs. Watch URLs use a numeric ID and slug: `https://shemalez.com/videos/{id}/{slug}/` (e.g. `/videos/868054/roc-khard-shemale-domination-threesome-sex-for-halloween/`). Embed fallback: `https://shemalez.com/embed/{id}`.

### Host aliases

- `shemalez.com`
- `www.shemalez.com`
- `tn.shemalez.com` (thumbnails/CDN)
- Any `*.shemalez.com` subdomain (handled via `can_handle()` suffix match)

Stream CDN hosts resolve to `*.ahcdn.com` (e.g. `shemalez.ahcdn.com`) â€” already covered by the global `ahcdn.com` allowlist in `schemas.py` and `video_streaming.py`.

### Listing and pagination (`list_videos`)

Uses the Tubecup JSON API â€” not HTML scraping:

- **Latest:** `https://shemalez.com/latest-updates/` (page 2+ â†’ `/latest-updates/2/`)
- **Sort feeds:** `/most-popular/`, `/longest/`, `/top-rated/`, `/most-viewed/`
- **Categories:** `/categories/{slug}/` (page 2+ â†’ `/categories/{slug}/2/`)
- **Search:** `/search/?s={query}`

List endpoint:

```text
GET /api/videos2.php?params={lifetime}/str/{sort}/{count}/{section}.{object_id}.{page}.all..
```

Search adds `&s={query}` with `sort=relevance`. Response shape: `{ "videos": [ ... ], "total_count", "pages" }`.

Use `curl_cffi` (Chrome impersonation) as primary fetch â€” plain httpx/aiohttp may be blocked or TLS-fail on this host.

### Metadata and streams (`scrape`)

1. **Video info:** `GET /api/json/video/{lifetime}/{million_bucket}/{thousand_bucket}/{id}.json`
   - Example: `/api/json/video/86400/0/868000/868054.json`
   - `million_bucket = int(1e6 * (id // 1e6))`, `thousand_bucket = 1000 * (id // 1000)`
2. **Stream files:** `GET /api/videofile.php?video_id={id}&lifetime=8640000`
   - Returns array of `{ format, video_url }` where `video_url` is custom-base64-encoded
3. **Decode streams:** translate Cyrillic look-alike chars + `,`/`.`/`~` â†’ standard base64, then decode to CDN URL (often `/get_file/...` on `*.ahcdn.com`)
4. **Resolve `/get_file/`:** follow redirect (no auto-redirect) to signed MP4/HLS URL
5. **Embed fallback:** `https://{host}/embed/{id}` when direct streams fail

Embed URLs (`/embed/{id}`) are accepted by `scrape()` â€” the numeric id is extracted and full metadata/streams are resolved the same way as watch-page URLs.

### Preview clips

The API `pv` field is often stale/wrong. Build preview URLs from video id when `pv` does not contain the id:

```text
https://vp2.txxx.com/c12/videos/{1000*(id//1000)}/{id}/{id}_tr.mp4
```

(Same shared Tubecup preview CDN as TXXX/HotMovs.)

### Categories (`get_categories`)

Latest Updates, sort feeds, and sample category slugs in `categories.json` (HD, Shemale, Ladyboy, Solo, Anal, Asian, etc.).

### Registration checklist for ShemaleZ

Package folder: `backend/app/scrapers/shemalez/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` â€” source aliases: `shemalez`, `shemaleZ`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="shemalez"`)

### ShemaleZ verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes   -H "Content-Type: application/json"   -d "{"url":"https://shemalez.com/videos/868054/roc-khard-shemale-domination-threesome-sex-for-halloween/"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://shemalez.com/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://shemalez.com/categories/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://shemalez.com/search/?s=anal&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=shemalez"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://shemalez.com/videos/868054/roc-khard-shemale-domination-threesome-sex-for-halloween/"
```

## PornDig (porndig.com) Implementation Notes

[PornDig](https://www.porndig.com/) is a modern tube site using a custom VHS player on `videos.porndig.com`. Watch URLs use a numeric post ID and slug: `https://www.porndig.com/videos/{id}/{slug}.html` (e.g. `/videos/254839/hayley-davies-hopes-he-ll-shove-it-all-the-way-inside-her.html`).

### Host aliases

- `porndig.com`
- `www.porndig.com`
- `videos.porndig.com` (embed player)
- `video-cdn.porndig.com` (HLS/MP4 streams)
- `image-cdn.porndig.com` (thumbnails/previews)

### Listing and pagination (`list_videos`)

- Main feed: `https://www.porndig.com/video/` (page 2+ â†’ `/videos/page/{n}/`)
- Channels: `/channels/{id}/{slug}/` (page 2+ â†’ `?page={n}`)
- Parse `.video_item_wrapper` blocks: `h2 a`, `img.js_video_preview`, `.bubble_duration`
- Preview clips from `img[data-vid]` (short MP4 previews on `image-cdn.porndig.com`)

Send `Cookie: dsclcnst=2; discl_s_t=1` to bypass the age disclaimer gate. Use `curl_cffi` (Chrome impersonation) as primary fetch.

### Metadata and streams (`scrape`)

- **Player URL:** extract `videos.porndig.com/player/index/{a}/{b}/{c}` from watch-page iframe/embed textarea
- **Streams:** fetch player page and parse `window.player_args.push({...})` JSON:
  - HLS: `src[].type == application/x-mpegurl` â†’ `master.m3u8`
  - MP4: `src[].type == multi-progressive` â†’ `srcSet[]` with `1080p`, `720p`, `540p`, `360p`
- **Metadata:** JSON-LD `VideoObject` (`name`, `thumbnailUrl`, `duration`, `uploadDate`, `actor`, `keywords`), `.video_stats` for length/upload date

### Categories (`get_categories`)

Videos hub plus sample `/channels/{id}/{slug}/` entries in `categories.json`.

### Registration checklist for PornDig

Package folder: `backend/app/scrapers/porndig/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="porndig"`)

### PornDig verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.porndig.com/videos/254839/hayley-davies-hopes-he-ll-shove-it-all-the-way-inside-her.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.porndig.com/video/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.porndig.com/channels/33/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=porndig"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.porndig.com/videos/254839/hayley-davies-hopes-he-ll-shove-it-all-the-way-inside-her.html"
```

## OK.XXX (ok.xxx) Implementation Notes

[OK.XXX](https://ok.xxx/) is a Private Host tube site. Watch URLs use a numeric video ID: `https://ok.xxx/video/{id}/` (e.g. `/video/751489/`). Embed URLs: `https://ok.xxx/embed/{id}`.

### Host aliases

- `ok.xxx`
- `www.ok.xxx`
- `static.ok.xxx` (thumbnails/screenshots)
- `cdn.privatehost.com` (resolved MP4/HLS streams)

### Listing and pagination (`list_videos`)

- Main feed: `https://ok.xxx/` (page 2+ â†’ `?page={n}`)
- Sort feeds: `/popular/`, `/trending/` (page 2+ â†’ `/{n}/` or `?page={n}`)
- Tags: `/tags/{slug}/` (e.g. `/tags/anal/`)
- Sites/channels: `/sites/{slug}/` (e.g. `/sites/brazzers/`) â€” not `/channels/{slug}/`
- Models: `/models/{slug}/`
- Search: `/search/?q={query}` (page 2+ â†’ `?q={query}&page={n}` or `/search/{n}/?q={query}`)
- Parse `.item.thumb-bl` / `.item.thumb-bl-video` blocks: `a[href*='/video/']`, `img[data-original]`, `data-preview-custom`, `.video-meta` for duration/views

Use `curl_cffi` (Chrome impersonation) as primary fetch; fall back to pooled `fetch_html`.

### Metadata and streams (`scrape`)

- **Metadata:** JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration` as ISO `PTâ€¦`, `uploadDate`, `author`, `actor`, `keywords`, `interactionStatistic.userInteractionCount` for views)
- **Streams:** `<video><source>` tags with `/get_file/â€¦` MP4 URLs (360p/480p/720p labels)
- **Redirect resolution:** `/get_file/` URLs 302 to signed `cdn.privatehost.com` links â€” resolve with `Referer: https://ok.xxx/video/{id}/`

### Categories (`get_categories`)

New, Popular, Trending, common tags, and sample `/sites/{slug}/` entries in `categories.json`.

### Registration checklist for OK.XXX

Package folder: `backend/app/scrapers/okxxx/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="okxxx"`)

### OK.XXX verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ok.xxx/video/751489/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ok.xxx/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ok.xxx/sites/brazzers/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=okxxx"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ok.xxx/video/751489/"
```

## PornHoarder (pornhoarder.tw) Implementation Notes

[PornHoarder](https://ww2.pornhoarder.tw/) is a porn search/aggregator. Watch URLs use a slug plus base64 token: `https://ww2.pornhoarder.tw/video/{slug}/{token}/`.

### Host aliases

- `ww2.pornhoarder.tw`
- `pornhoarder.tw`
- `www.pornhoarder.tw`
- `pornhoarder.net` (player host)
- `pornhoarder.pictures` (thumbnails)
- `playmogo.com` (DoodStream embed proxy)
- `cloudatacdn.com` / `*.cloudatacdn.com` (resolved MP4 CDN)

### Listing and pagination (`list_videos`)

- Home feed: `https://ww2.pornhoarder.tw/hp/`
- Trending: `/trending-videos/`
- Random: `/random-videos/`
- Tags: `/tag/{slug}/videos/`
- Pornstars: `/pornstar/{slug}/videos/`
- Studios: `/studio/{slug}/videos/`
- Pagination: `?page={n}` on all list paths (do not use `/path/{n}/` for trending)
- Parse `article` blocks: `a.video-link`, `.video-image[data-src]`, `.video-length`, `.video-content h1`

Use `curl_cffi` (Chrome impersonation) as primary fetch.

Note: `/search/?search=...` list pages require client-side/session state and are not reliably scrapable via simple GET.

### Metadata and streams (`scrape`)

- **Metadata:** JSON-LD `VideoObject` + `h1` title + `.video-info` (duration, host, upload age) + tags section
- **Player:** `embedUrl` â†’ `https://pornhoarder.net/player.php?video={token}`
- **Stream chain:**
  1. `POST` player with `play=` (click-to-play gate)
  2. Extract embed iframe (`playmogo.com/e/...` DoodStream wrapper)
  3. Parse `/pass_md5/...` from embed page JS
  4. `GET /pass_md5/...` returns signed `cloudatacdn.com` MP4 URL

### Categories (`get_categories`)

Home, trending, random, sample tags/pornstars/studios in `categories.json`.

### Registration checklist for PornHoarder

Package folder: `backend/app/scrapers/pornhoarder/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="pornhoarder"`)

### PornHoarder verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ww2.pornhoarder.tw/video/railey-diesel-see-through-chairs-fuck-2024/R0JjSWJjSnYweUJ0bnlSaGlLME5kVnJTL2h5M1dVYm1qOG13ckFQRVRIVT0=\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ww2.pornhoarder.tw/trending-videos/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ww2.pornhoarder.tw/tag/anal/videos/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pornhoarder"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ww2.pornhoarder.tw/video/railey-diesel-see-through-chairs-fuck-2024/R0JjSWJjSnYweUJ0bnlSaGlLME5kVnJTL2h5M1dVYm1qOG13ckFQRVRIVT0="
```

## Hentai Ocean (hentaiocean.com) Implementation Notes

[Hentai Ocean](https://hentaiocean.com/) is an English-subbed hentai streaming site. Watch URLs use a slug per episode: `https://hentaiocean.com/watch/{slug}` (e.g. `/watch/muchuu-no-tou-1`).

### Host aliases

- `hentaiocean.com`
- `www.hentaiocean.com`
- `w1.hentaiocean.com` (VIP/universal player CDN)
- `w2.hentaiocean.com` (VIP player CDN)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hentaiocean.com")
```

### Listing and pagination (`list_videos`)

- Home sections: `/view/recent-releases`, `/view/newly-added`, `/view/random`
- Genres: `/genre/{Name}` (e.g. `/genre/Milf`, `/genre/Creampie`)
- Search: `/explore?q={query}`
- Pagination: `?page={n}` on all list/search URLs (page 1 omits the query param)
- Parse `a.cell.card[href*='/watch/']` blocks: `img[alt]`, cover from `/assets/optcover/` or `/assets/cover/`

Use `curl_cffi` (Chrome impersonation) as primary fetch.

### Metadata and streams (`scrape`)

Watch pages embed inline `var jsondata = {...}` with:

- `info[]`: `urlname`, `videoname`, `description`, `releasedate`, `uploaddate`, `coverimg`
- `mirrors[]`: `{mirrorurl}` entries
- `genres[]`: genre labels

Fallback metadata API (no mirrors): `https://hentaiocean.com/api?action=hentai&slug={slug}`

**Mirror priority** (matches site `mirrorsort()`):

1. VIP: `https://w1|w2.hentaiocean.com/play?vid={filename}`
2. Universal: `https://w1.hentaiocean.com/universal?vid={filename}`
3. External embeds: listeamed, vidguard, streamtape, dooodster

**Direct MP4 extraction** for VIP/universal mirrors:

- Stream: `{mirror-host}/video/{urlencoded-vid}`
- Download: `{mirror-host}/download/{urlencoded-vid}`

Player pages set `videoElement.src = BASE_VIDEO_URL + encodeURIComponent(vid)` where `BASE_VIDEO_URL` is `https://w2.hentaiocean.com/video/` (host derived from mirror URL in the scraper).

Also expose the original `play?vid=` / external URLs as `format: "embed"` streams.

### Categories (`get_categories`)

Recent Releases, Newly Added, Random, and sample genre feeds in `categories.json`.

### Registration checklist for Hentai Ocean

Package folder: `backend/app/scrapers/hentaiocean/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="hentaiocean"`)

### Hentai Ocean verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hentaiocean.com/watch/muchuu-no-tou-1\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaiocean.com/view/recent-releases&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaiocean.com/genre/Milf&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hentaiocean"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hentaiocean.com/watch/muchuu-no-tou-1"
```

## Hentaverse (hentaverse.com) Implementation Notes

[Hentaverse](https://hentaverse.com/) is a Next.js hentai streaming site with series and episode pages. Episode watch URLs use `/video/{slug}` (e.g. `/video/a-size-classmate-episode-1`). Series pages use `/hentai/{series-slug}`.

### Host aliases

- `hentaverse.com`
- `www.hentaverse.com`
- `cdn.hentaverse.com` (MP4/thumbnail CDN)

### Listing and pagination (`list_videos`)

- Newest: `https://hentaverse.com/newest`
- Trending: `https://hentaverse.com/trending`
- Categories: `/categories/{slug}` (e.g. `/categories/ntr`, `/categories/creampie`)
- Series hub: `/hentai` and home page series cards at `/hentai/{series-slug}`
- Search: `/search?search_query={query}`

**Pagination:** the public site HTML only embeds page 1 in Next.js flight data. `list_videos` must call the content API at `https://apiv2.hentaverse.com/api/v1/content` with a `page` query param:

| Feed | API request |
|------|-------------|
| Newest | `GET /videos?type=newest&page={n}&limit={limit}` |
| Trending | `GET /videos?sort=trending&page={n}&limit={limit}` |
| Category | `GET /categories/{slug}?page={n}&limit={limit}` |
| Search | `GET /search/videos?q={query}&page={n}&limit={limit}` |
| Home | `GET /videos?sort=trending&page={n}&limit={limit}` |

Responses use `data.items` (feeds/search) or `data.videos` (categories). Fall back to HTML parsing only for `/hentai/{series-slug}` episode grids.

Use `curl_cffi` (Chrome impersonation) with `Origin`/`Referer` headers for API requests.

### Metadata and streams (`scrape`)

- **Series URL resolution:** `/hentai/{series-slug}` resolves to the first episode from embedded episode cards, then scrapes the watch page.
- **Metadata:** JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `uploadDate`, `genre`, `interactionStatistic` for views, `author`)
- **Stream base path:** `"videoPath":"uploads/videos/{uuid}/renditions"` from flight chunks, or `VideoObject.contentUrl`
- **Streams:** progressive MP4 renditions on CDN:
  - `https://cdn.hentaverse.com/uploads/videos/{uuid}/renditions/1080p.mp4`
  - `720p.mp4`, `480p.mp4`, `360p.mp4`
- Send `Referer: https://hentaverse.com/video/{slug}` when accessing CDN URLs.

### Categories (`get_categories`)

Newest, Trending, Hentai Series, and sample category feeds in `categories.json`.

### Registration checklist for Hentaverse

Package folder: `backend/app/scrapers/hentaverse/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="hentaverse"`)

### Hentaverse verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hentaverse.com/video/a-size-classmate-episode-1\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaverse.com/newest&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaverse.com/categories/ntr&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hentaverse"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hentaverse.com/video/a-size-classmate-episode-1"
```

## hstream.moe Implementation Notes

[hstream.moe](https://hstream.moe/) is a Laravel/Livewire hentai streaming site with English-subbed episodes in HD/4K. Episode watch URLs use `/hentai/{slug}` (e.g. `/hentai/heart-mark-oome-1`). Series hub pages use `/hentai/{series-slug}` without the trailing `-{episode}` suffix.

### Host aliases

- `hstream.moe`
- `www.hstream.moe`
- Stream CDN hosts returned by `/player/api` (e.g. `imoto-str.ane-h.xyz`, `oppai-str.shoujo-h.org`, `*.imoto-h.xyz`, `*.musume-h.xyz`, `*.rorikon-h.xyz`)

### Listing and pagination (`list_videos`)

- Home: `https://hstream.moe/`
- Recently uploaded: `https://hstream.moe/search?order=recently-uploaded`
- Recently released: `https://hstream.moe/search?order=recently-released`
- Most views: `https://hstream.moe/search?order=view-count`
- Tag filters: `https://hstream.moe/search?order=recently-uploaded&tags%5B0%5D={tag}` (e.g. `milf`, `uncensored`, `4k-48fps`)
- Search: `https://hstream.moe/search?q={query}&order=recently-uploaded`

**Pagination:** append `&page={n}` to search/list URLs (page 1 omits the param). Home page cards are embedded in tabs; use search feeds for reliable pagination.

Parse list cards from `div.episode-item a[href*="/hentai/"]` with title in `h3`, thumbnail in `img`, and views from the eye icon row.

### Metadata and streams (`scrape`)

- **Series URL resolution:** `/hentai/{series-slug}` resolves to the first episode link matching `{series-slug}-*`, then scrapes that episode page.
- **Episode id:** hidden input `#e_id` on episode pages (e.g. `value="2004"`).
- **Player API:** `POST https://hstream.moe/player/api` with JSON `{"episode_id": "<id>"}`. Requires Laravel CSRF from the page session (`X-XSRF-TOKEN` cookie + `X-CSRF-TOKEN` from meta or Livewire `data-csrf`).
- **Metadata:** JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `uploadDate`, `genre`, `interactionStatistic.userInteractionCount` for views), plus Open Graph fallbacks.
- **Stream payload fields:** `stream_url` (e.g. `2026/Heart.Mark.Oome/E01`), `stream_domains` (CDN base URLs), `interpolated`, `interpolated_uhd`.
- **Streams:** build from the first CDN domain in `stream_domains`:
  - Guest MP4: `{cdn}/{stream_url}/x264.720p.mp4`
  - DASH MPD: `{cdn}/{stream_url}/720/manifest.mpd`, `/1080/manifest.mpd`, `/2160/manifest.mpd`
  - Optional interpolated: `/1080i/manifest.mpd`, `/2160i/manifest.mpd`
- Send `Referer: https://hstream.moe/hentai/{slug}` when accessing CDN URLs.

Use a single `httpx.AsyncClient` with cookies for the page fetch and player API POST.

### Categories (`get_categories`)

Home, Recently Uploaded, Recently Released, Most Views, and sample tag feeds in `categories.json`.

### Registration checklist for hstream.moe

Package folder: `backend/app/scrapers/hstream/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="hstream"`)

### hstream.moe verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hstream.moe/hentai/heart-mark-oome-1\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hstream.moe/search?order=recently-uploaded&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hstream.moe/search?order=recently-uploaded&tags%5B0%5D=milf&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hstream"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hstream.moe/hentai/heart-mark-oome-1"
```

## Anibd (anibd.app) Implementation Notes

[Anibd](https://anibd.app/) is a WordPress anime streaming site backed by `eng.animeapps.top` and `epeng.animeapps.top` APIs. Series pages use `/up/{postid}/`. Episode playback uses `/up/{postid}/watch/?server={id}&slug={slug}`.

### Host aliases

- `anibd.app`
- `www.anibd.app`
- API/CDN hosts: `eng.animeapps.top`, `epeng.animeapps.top`, `playeng.animeapps.top`, `imganibd.ims2.top`, `rez1.ims1.top`, `*.ims1.top`, `*.ims2.top`, `*.1imgdarr.top`

### Listing and pagination (`list_videos`)

- Home / latest: `https://anibd.app/` â†’ `GET https://eng.animeapps.top/api/singlefilter.php?page={n}&limit={limit}`
- Filter page: `https://anibd.app/filter/?fo=22258&pg=2`
  - `fo` â†’ `postseasontypetagid`
  - `ty` â†’ `anitypestagid`
  - `ge` â†’ `postanigenrestagid`
  - `ye` â†’ `postyeartagid`
  - `pg` â†’ page (only when page arg is 1)
- Search: `https://anibd.app/?s={query}` â†’ `GET https://eng.animeapps.top/api/search3.php?keyword={query}&page={n}&limit={limit}`

List items map to `https://anibd.app/up/{postid}/` with title/thumbnail from API rows.

### Metadata and streams (`scrape`)

1. `GET https://eng.animeapps.top/api/single.php?postid={id}` â†’ metadata (`postname`, `anilist`, covers, genres, description)
2. `GET https://epeng.animeapps.top/api2.php?epid={anilist}` â†’ servers and episode slugs
3. Pick episode from URL `server`/`slug` query params, or first episode of first server
4. `GET https://epeng.animeapps.top/apilink.php?data={episode.link}` â†’ player embed URLs (`playeng.animeapps.top/.../play2.php`)
5. Fetch each embed page and parse `videoUrl: "/.../index.m3u8"` from inline player config
6. Resolve to absolute HLS URL on `playeng.animeapps.top`

Send `Referer: https://anibd.app/up/{postid}/watch/?server=...&slug=...` when accessing player/CDN URLs.

### Categories (`get_categories`)

Home, Bluray Uncensored, ani16+, Movie, Filter, and Top Anime feeds in `categories.json`.

### Registration checklist for Anibd

Package folder: `backend/app/scrapers/anibd/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="anibd"`)

### Anibd verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://anibd.app/up/407121/\"}"

curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://anibd.app/up/407121/watch/?server=10&slug=01\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://anibd.app/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://anibd.app/filter/?fo=22258&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=anibd"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://anibd.app/up/407121/watch/?server=10&slug=01"
```

## 1Porn.TV (oneporn) Implementation Notes

[1Porn.TV](https://www.1porn.tv/) is a KVS-based tube site with slug-based watch URLs, Video.js progressive MP4 sources via signed `/get_file/` links, and JSON-LD metadata. The scraper package folder is `oneporn` because Python module names cannot start with a digit.

### Host aliases

- `1porn.tv`
- `www.1porn.tv`
- CDN/thumbnail hosts: `img.1porn.tv`, `cast.1porn.tv`, `fpvcdn.com` (resolved MP4 CDN)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "1porn.tv" or h.endswith(".1porn.tv")
```

### Listing and pagination (`list_videos`)

- Home: `https://www.1porn.tv/` â†’ `#list_videos_most_recent_videos_items`
- Latest: `https://www.1porn.tv/latest-updates/` â†’ `#list_videos_latest_videos_list_items`
- Categories / top lists: `#list_videos_common_videos_list_items`
- Search: `https://www.1porn.tv/search/{query}/` â†’ `#custom_list_videos_videos_list_search_result_items`

**Pagination:** append `/{page}/` to the list path (page 1 omits the page segment). Examples:

- Page 2 latest: `https://www.1porn.tv/latest-updates/2/`
- Page 3 category: `https://www.1porn.tv/categories/anal/3/`

Parse cards from `.item` blocks with `a[href*='/videos/']`, thumbnail in `img`, preview in `.img[data-preview]`, duration in `.duration`.

Use `curl_cffi` (Chrome impersonation) with `Referer: https://www.1porn.tv/` â€” direct video-page fetches can return Cloudflare 503 without a warm session/referer.

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://www.1porn.tv/videos/{slug}/`
- **Embed URL shape:** `https://www.1porn.tv/embed/{video_id}` â€” resolve to the canonical watch URL via inline `flashvars.video_url`
- **Metadata:** JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `uploadDate`, `duration` as ISO-8601, `embedUrl`, `interactionStatistic` for views), plus Open Graph fallbacks
- **Streams:** progressive MP4 `<source>` tags inside `video.video-js`, typically signed:
  - `https://www.1porn.tv/get_file/{token}/{bucket}/{id}/{id}_2160m.mp4/`
  - `..._720m.mp4/`, `..._480m.mp4/`
- Resolve `/get_file/` URLs through redirects before returning stream endpoints (same pattern as `porngo`).
- Send `Referer: https://www.1porn.tv/` when accessing CDN/get_file URLs.

### Categories (`get_categories`)

Latest, Top Rated, Most Viewed, 4K, and sample category feeds in `categories.json`.

### Registration checklist for 1Porn.TV

Package folder: `backend/app/scrapers/oneporn/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `oneporn`, `1porn`, `1porn.tv`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="oneporn"`)

### 1Porn.TV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.1porn.tv/videos/softcore-makeout-turns-hardcore-vaginal-sex-with-luna-bunny/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.1porn.tv/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.1porn.tv/categories/anal/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=oneporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.1porn.tv/videos/softcore-makeout-turns-hardcore-vaginal-sex-with-luna-bunny/"
```

## ThePornBang.com Implementation Notes

[ThePornBang.com](https://www.thepornbang.com/home36/) is a KVS/kt_player tube site with slug-based watch URLs, signed `/get_stream/` MP4 links in inline `flashvars`, and home/category/search feeds.

### Host aliases

- `thepornbang.com`
- `www.thepornbang.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "thepornbang.com" or h.endswith(".thepornbang.com")
```

### Listing and pagination (`list_videos`)

- Home: `https://www.thepornbang.com/home36/`
- Category: `https://www.thepornbang.com/category/{slug}_c{id}/`
- Search: `https://www.thepornbang.com/search/{query}/`

**Pagination:** append `/{page}/` to the list path (page 1 omits the page segment). Examples:

- Page 2 category: `https://www.thepornbang.com/category/anal_c13/2/`
- Page 3 search: `https://www.thepornbang.com/search/milf/3/`

Parse cards from `.row.item` with `a.thumb[href*='/video/']`, thumbnail in `img[data-original]`, preview in `img[data-preview]`, duration in `.duration`, views in `.views`.

List section roots include `#list_videos_latest_videos_list_items`, `#list_videos_latest_videos_items`, `#list_videos_most_recent_videos_items`, and related home blocks.

Use `curl_cffi` (Chrome impersonation) with `Referer: https://www.thepornbang.com/home36/`.

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://www.thepornbang.com/video/{slug}_v{id}/`
- **Embed URL shape:** `https://www.thepornbang.com/embed/{video_id}/`
- **Metadata:** Open Graph (`og:title`, `og:image`), `h1`, and inline `flashvars` (`video_title`, `video_categories`, `video_tags`, `video_id`)
- **Streams:** signed progressive MP4 links from `flashvars`:
  - `video_url` + `video_url_text` (480p)
  - `video_alt_url` + `video_alt_url_text` (720p)
  - `video_alt_url2` + `video_alt_url2_text` (1080p)
  - `video_alt_url3` + `video_alt_url3_text` (2160p)
  - Example: `https://www.thepornbang.com/get_stream/8897-720.mp4?md5=...&timestamp=...`
- Deduplicate streams by normalized URL and quality label before returning.
- Send `Referer: https://www.thepornbang.com/home36/` when accessing `/get_stream/` URLs.

### Categories (`get_categories`)

Home, New, Best, 4K, and sample category feeds in `categories.json`.

### Registration checklist for ThePornBang

Package folder: `backend/app/scrapers/thepornbang/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `thepornbang`, `pornbang`, `thepornbang.com`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="thepornbang"`)

### ThePornBang verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.thepornbang.com/video/en-pointe-pounding_v28/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thepornbang.com/home36/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thepornbang.com/category/anal_c13/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=thepornbang"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.thepornbang.com/video/en-pointe-pounding_v28/"
```

## PornHD3X (pornhd3x) Implementation Notes

[PornHD3X](https://www9.pornhd3x.tv/) is a CMS-based movie site (Brazzers3X family) with slug-based watch URLs, JW Player sources loaded via AJAX, and home/category/search feeds.

### Hosts

- `pornhd3x.tv`, `www.pornhd3x.tv`, `www9.pornhd3x.tv`
- `pornhd3x.me`, `www.pornhd3x.me`
- Related mirror domains: `brazzers3x.com`, `brazzers3x.me`

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h.startswith("www9."):
        h = h[5:]
    return h in SITE_ALIASES or h.endswith(".pornhd3x.tv") or h.endswith(".pornhd3x.me")
```

### Listing URLs

- Home page 1: `https://www9.pornhd3x.tv/`
- Home page 2+: `https://www9.pornhd3x.tv/premium-porn-hd/page-{n}`
- Category: `https://www9.pornhd3x.tv/category/{slug}/`
- Category page 2+: `https://www9.pornhd3x.tv/category/{slug}/page-{n}`
- Search: `https://www9.pornhd3x.tv/search/{query}/`

List cards use `.ml-item.item` with `a[href*='/movies/']`, `img[data-original]`, and optional `[data-preview]`.

### Watch page + streams

- **Watch URL shape:** `https://www9.pornhd3x.tv/movies/{slug}/`
- **Movie id:** inline `var movie = { id: "...", name: "...", ... }`
- **Stream API:** `GET /ajax/get_sources/{episode_id}/{md5}?count=1&mobile=false`
  - Requires a session cookie named `{token[13:37]}{episode_id}{token[40:64]}` with a random 6-char value
  - MD5 token: `md5(episode_id + cookie_value + "98126avrbi6m49vd7shxkn985")`
  - Response JSON contains JW Player `playlist[].sources[]` with signed HLS/MP4 URLs (often `cdn-aws-exp.cdnamz.me`)
- **Embed fallback (servers 12â€“15):** `GET /ajax/load_embed/{episode_id}` â†’ `{ "embed_url": "..." }`

Use `curl_cffi` (Chrome impersonation) with `Referer` set to the movie page.

Package folder: `backend/app/scrapers/pornhd3x/`.

Registration checklist:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `pornhd3x`, `pornhd3x.tv`, `www9.pornhd3x.tv`)
- `backend/app/services/video_streaming.py`
- `backend/app/models/schemas.py` (allow `pornhd3x.tv`, `www9.pornhd3x.tv`, `brazzers3x.com`, `cdnamz.me`)
- `backend/app/api/endpoints/explore.py` (`sourceId="pornhd3x"`)
- `backend/HOW_TO_ADD_SCRAPER.md`

### Test commands

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/scrapes" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www9.pornhd3x.tv/movies/new-deeper-stella-luxx-the-cure-for-loneliness-02-06-2026-anal-hardcore-artporn-bigtits-iluvy-lulustream-com-doodstream-co\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www9.pornhd3x.tv/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www9.pornhd3x.tv/category/brazzers&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pornhd3x"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www9.pornhd3x.tv/movies/new-deeper-stella-luxx-the-cure-for-loneliness-02-06-2026-anal-hardcore-artporn-bigtits-iluvy-lulustream-com-doodstream-co"
```

## JavFun.me (javfun) Implementation Notes

[JavFun.me](https://en.javfun.me/) is a JavHub-family CMS site with slug-based watch URLs, JW Player HLS via `/ajax/get_sources/`, and studio/category/search feeds.

### Hosts

- `javfun.me`, `en.javfun.me`, `www.javfun.me`
- Media/thumbnails may also reference `javhub.me`

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h.startswith("en."):
        h = h[3:]
    return h in SITE_ALIASES or h.endswith(".javfun.me")
```

### Listing URLs

- Home page 1: `https://en.javfun.me/`
- Home page 2+: `https://en.javfun.me/japanese-porn-videos/page-{n}`
- All movies: `https://en.javfun.me/japanese-porn-videos`
- Studio: `https://en.javfun.me/studio/{slug}/`
- Category: `https://en.javfun.me/category/{slug}/`
- Search: `https://en.javfun.me/search/{query}/`
- Pagination: append `/page-{n}` to the browse path

List cards use `div.ml-item[data-movie-id]` with `a.ml-mask[href*='/movies/']`, `img.mli-thumb[data-original]`.

### Watch page + streams

- **Watch URL shape:** `https://en.javfun.me/movies/{slug}` (no trailing slash â€” trailing slash redirects to junk)
- **Episode ID:** `a.btn-eps[episode-id]` or inline `var movie = { id: "..." }`
- **Stream API:** `GET /ajax/get_sources/{episode_id}/{md5}?count=1&mobile=0`
  - MD5: `md5(episode_id + random6 + "9826avrbi6m49vd7shxkn9815")`
  - Response: JW Player `playlist[].sources[]` with HLS on `*.gogocdnaws-2.online`
- **Embed fallback:** `GET /ajax/load_embed/{episode_id}`

Use `curl_cffi` with `Referer` set to the movie page. Prefer `#mv-info h3` over `og:title` (often `"Loading..."`).

Package folder: `backend/app/scrapers/javfun/`.

Registration checklist:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `javfun`, `javfun.me`, `en.javfun.me`)
- `backend/app/services/video_streaming.py`
- `backend/app/models/schemas.py` (allow `javfun.me`, `en.javfun.me`, `gogocdnaws-2.online`)
- `backend/app/api/endpoints/explore.py` (`sourceId="javfun"`)
- `backend/HOW_TO_ADD_SCRAPER.md`

### Test commands

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/scrapes" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://en.javfun.me/movies/asiansexdiary-boat-trip-and-jennifer\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://en.javfun.me/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://en.javfun.me/studio/caribbeancom&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=javfun"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://en.javfun.me/movies/asiansexdiary-boat-trip-and-jennifer"
```

## PornHD4K.net (pornhd4k) & PornHouse.me (pornhouse) Implementation Notes

[PornHD4K.net](https://pornhd4k.net/) and [PornHouse.me](https://pornhouse.me/) share the same JavHub/PornHD CMS as PornHD3X (JW Player, `/movies/{slug}`, `/ajax/get_sources/`).

Package folders: `backend/app/scrapers/pornhd4k/`, `backend/app/scrapers/pornhouse/`.

### Hosts

| Site | Hosts | CDN |
|------|-------|-----|
| **pornhd4k** | `pornhd4k.net`, `www.pornhd4k.net` | `free50.cdnamz.me`, `cdnamz.me` |
| **pornhouse** | `pornhouse.me`, `www.pornhouse.me` | `cdn.pornhouse.me` |

### Listing URLs

- Home page 1: `/`
- Home page 2+: `/premium-porn-hd/page-{n}`
- Category: `/category/{slug}/`
- Studio: `/studio/{slug}/`
- Search: `/search/{query}/`
- List cards: `div.ml-item[data-movie-id]` with `a.ml-mask[href*='/movies/']`

### Watch page + streams

- **Watch URL:** `https://{host}/movies/{slug}` (no trailing slash required)
- **Episode ID:** inline `var movie = { id: "..." }` or `episode-id` attribute
- **Stream API:** `GET /ajax/get_sources/{episode_id}/{md5}?count=1&mobile=0`
  - Requires session cookie + MD5 token (salt: `98126avrbi6m49vd7shxkn985`)
  - Returns JW Player HLS playlist

Package folders: `backend/app/scrapers/pornhd4k/`, `backend/app/scrapers/pornhouse/`.

Registration: `sourceId="pornhd4k"` / `sourceId="pornhouse"` in explore, main, video_streaming, schemas.

### Test commands

```bash
# PornHD4K
curl -X POST "http://127.0.0.1:8000/api/v1/scrapes" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pornhd4k.net/movies/rk-prime-tony-rubino-vivienne-vo-getting-sleazy-in-the-speakeasy-01-07-2026\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornhd4k.net/&page=1&limit=20"
curl "http://127.0.0.1:8000/api/v1/categories?source=pornhd4k"

# PornHouse
curl -X POST "http://127.0.0.1:8000/api/v1/scrapes" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pornhouse.me/movies/asian-panties-scene-2_bukkake-for-a-pretty-japanese-girl-in-lingerie-and-sex-toys\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornhouse.me/&page=1&limit=20"
curl "http://127.0.0.1:8000/api/v1/categories?source=pornhouse"
```

## Hanime1.me (hanime1) Implementation Notes

[Hanime1.me](https://hanime1.me/) is a Traditional Chinese hentai streaming site. Watch URLs use a numeric video ID query param (`/watch?v=407006`). Signed MP4 streams and thumbnails are served from `vdownload.hembed.com`.

### Host aliases

- `hanime1.me`
- `www.hanime1.me`
- `vdownload.hembed.com` / `*.hembed.com` (CDN for MP4 + thumbnails)

### Listing and pagination (`list_videos`)

- Home: `https://hanime1.me/`
- Search/browse: `https://hanime1.me/search?sort=æœ€æ–°ä¸Šå¸‚`, `?sort=æœ€æ–°ä¸Šå‚³`, `?sort=ä»–å€‘åœ¨çœ‹`
- Genre filters: `https://hanime1.me/search?genre=è£ç•ª`, `?genre=3DCG`, etc.
- Text search: `https://hanime1.me/search?query={query}`
- Parse `div.video-item-container` cards (`a.video-link`, `img.main-thumb`, `div.duration`, stats) or fallback `a[href*='watch?v=']`
- Pagination: `?page=N` query parameter (page 1 omits `page`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://hanime1.me/watch?v={ID}`
- **Streams:** signed MP4 URLs on `vdownload.hembed.com/{ID}-1080p.mp4`, `-720p.mp4`, `-480p.mp4` from `<video><source>` and inline HTML
- Metadata: `og:title`, `og:image`, `og:video:duration`, `#video-artist-name` uploader, `.single-video-tag` tags, view count/date in `.video-description-panel`
- Use `curl_cffi` browser impersonation; plain pool fetch often gets 403

Package folder: `backend/app/scrapers/hanime1/`.

Registration: `sourceId="hanime1"` in explore, main, video_streaming, schemas.

### Hanime1 verification examples

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/scrapes" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hanime1.me/watch?v=407006\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hanime1.me/search?sort=%E6%9C%80%E6%96%B0%E4%B8%8A%E5%B8%82&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hanime1.me/search?genre=%E8%A3%8F%E7%95%AA&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hanime1"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hanime1.me/watch?v=407006"
```

## HentaiBros.net (hentaibros) Implementation Notes

[HentaiBros.net](https://hentaibros.net/) is a WordPress RetroTube site with FV FlowPlayer embeds. Watch URLs are slug-based posts (`/{slug}/`), and MP4 streams are exposed inline in the player `data-item` JSON.

### Host aliases

- `hentaibros.net`
- `www.hentaibros.net`
- `povblowjob.net` (external MP4 CDN used by the player)

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hentaibros.net") or h == "povblowjob.net"
```

### Listing and pagination (`list_videos`)

- Home: `https://hentaibros.net/`
- Hentai list: `https://hentaibros.net/hentai-list/`
- Series pages: `https://hentaibros.net/anime/{slug}/`
- Genre feeds: `https://hentaibros.net/genres/{slug}/`
- Search: `https://hentaibros.net/?s={query}`
- Parse cards from `article.loop-video` (`a[href]`, `img[alt]`, `data-main-thumb`, `.duration`)
- Pagination: append `/page/{N}/` to the list path (page 1 omits the page segment)

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://hentaibros.net/{slug}/`
- **Metadata:** `og:title`, `og:image`, `og:description`, `h1.entry-title`, tag links (`a[rel='tag']`), series link (`a[href*='/anime/']`)
- **Streams:** parse `.flowplayer[data-item]` JSON and read `sources[].src` (typically MP4 on `povblowjob.net`)
- Fallback: regex scan page HTML for `.mp4` / `.m3u8` URLs when the player payload is missing
- Use `curl_cffi` browser impersonation with `Referer: https://hentaibros.net/`

### Categories (`get_categories`)

Home, Hentai List, 3D Hentai, Motion Anime, Uncensored, genre feeds, and anime series pages in `categories.json` (generated via `backend/scripts/gen_hentaibros_categories.py`).

### Registration checklist for HentaiBros

Package folder: `backend/app/scrapers/hentaibros/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `hentaibros`, `hentaibros.net`, `hbros`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists for `hentaibros.net` and `povblowjob.net`)
- `backend/app/api/endpoints/explore.py` (`sourceId="hentaibros"`)

### HentaiBros verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hentaibros.net/cheat-item-kanrikyoku-no-oshigoto-ex-episode-1/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaibros.net/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hentaibros.net/genres/uncensored/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hentaibros"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hentaibros.net/cheat-item-kanrikyoku-no-oshigoto-ex-episode-1/"
```

## HenVids.com (henvids) Implementation Notes

[HenVids.com](https://henvids.com/) is a Svelte-based hentai streaming site. Watch URLs use `/hentai/{slug}` paths, and HLS streams are served from `cdn.henvids.com`.

### Host aliases

- `henvids.com`
- `www.henvids.com`
- `cdn.henvids.com` (HLS + thumbnails)

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".henvids.com") or h == "cdn.henvids.com"
```

### Listing and pagination (`list_videos`)

- Home: `https://henvids.com/`
- Latest: `https://henvids.com/latest`
- Trending: `https://henvids.com/trending`
- Tag feeds: `https://henvids.com/tag/{slug}`
- Search: `https://henvids.com/search?q={query}`
- Parse cards from `article` blocks with `a[href^='/hentai/']`, `img[alt]`, duration/views text
- Pagination: `?page=N` query parameter (page 1 omits `page`)

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://henvids.com/hentai/{slug}`
- **Metadata:** JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `uploadDate`, `interactionStatistic`, `productionCompany`, `genre`), plus Open Graph fallbacks
- **Streams:** HLS playlist at `https://cdn.henvids.com/hentai/{slug}/playlist.m3u8` from JSON-LD `contentUrl`, `og:video`, or `<video><source>`
- Duration uses ISO-8601 (`PT15M17S`) converted to `MM:SS` / `H:MM:SS`
- Use `curl_cffi` browser impersonation with `Referer: https://henvids.com/`

### Categories (`get_categories`)

Home, Latest, Trending, All Tags, and individual `/tag/{slug}` feeds in `categories.json` (generated via `backend/scripts/gen_henvids_categories.py`).

### Registration checklist for HenVids

Package folder: `backend/app/scrapers/henvids/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `henvids`, `henvids.com`, `hvids`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists for `henvids.com` and `cdn.henvids.com`)
- `backend/app/api/endpoints/explore.py` (`sourceId="henvids"`)

### HenVids verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://henvids.com/hentai/kenki-virgo-2\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://henvids.com/latest&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://henvids.com/tag/uncensored&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=henvids"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://henvids.com/hentai/kenki-virgo-2"
```

## MuchoHentai.com (muchohentai) Implementation Notes

[MuchoHentai.com](https://muchohentai.com/home) is a WordPress hentai site using JW Player. Watch URLs use `/{prefix}/{id}` paths (e.g. `/avH6Dh/200451`), and HLS streams are built from inline JW Player config plus `*.edge.tmncdn.io` CDN hosts.

### Host aliases

- `muchohentai.com`
- `www.muchohentai.com`
- `va01.edge.tmncdn.io`, `va02.edge.tmncdn.io` (stream CDN mirrors)

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".muchohentai.com") or h.endswith("edge.tmncdn.io")
```

### Listing and pagination (`list_videos`)

- Home: `https://muchohentai.com/home`
- Latest: `https://muchohentai.com/latest-hentai-posts/`
- Series list: `https://muchohentai.com/hentai-series-list/`
- Genre feeds: `https://muchohentai.com/g/{slug}/`
- Search: `https://muchohentai.com/?s={query}`
- Parse cards from `a[href]` matching `/{prefix}/{id}` with `img[alt]`, views text (`11.27K Views`)
- Pagination: append `/page/{N}/` to list paths; search uses `?s=query&paged=N`

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://muchohentai.com/{prefix}/{id}/`
- **Metadata:** Open Graph (`og:title`, `og:image`, `og:description`), page title, views regex, tag links (`a[rel='tag']`, `a[href*='/g/']`)
- **Streams:** parse inline JW Player vars:
  - `var servers = ['va01', 'va02'];`
  - `var files = [{"file":"/wp-content/uploads/.../ja.m3u8"}];`
  - Build HLS URLs as `https://{server}.edge.tmncdn.io` + relative file path for each mirror
- Use `curl_cffi` browser impersonation with `Referer: https://muchohentai.com/home`

### Categories (`get_categories`)

Home, Latest, Series, Upcoming, Genre List, and `/g/{slug}/` feeds in `categories.json` (generated via `backend/scripts/gen_muchohentai_categories.py`).

### Registration checklist for MuchoHentai

Package folder: `backend/app/scrapers/muchohentai/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `muchohentai`, `muchohentai.com`, `mh`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists for `muchohentai.com` and `edge.tmncdn.io`)
- `backend/app/api/endpoints/explore.py` (`sourceId="muchohentai"`)

### MuchoHentai verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://muchohentai.com/avH6Dh/200451\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://muchohentai.com/home&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://muchohentai.com/g/uncensored/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=muchohentai"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://muchohentai.com/avH6Dh/200451"
```

## UnderHentai.net (underhentai) Implementation Notes

[UnderHentai.net](https://www.underhentai.net/) is a WordPress hentai site with episode cards, download mirrors, and inline JS-injected stream embeds. Watch URLs use slug-based posts (`/{slug}/`), and streams are resolved from `/watch/?id={id}&ep={ep}` pages.

### Host aliases

- `underhentai.net`
- `www.underhentai.net`
- `static.underhentai.net` (thumbnails/assets)
- `krakenfiles.com` / `krakencloud.net` (KrakenFiles embed MP4)
- `luluvdo.com` (LuluStream embed fallback)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".underhentai.net") or h in STREAM_HOSTS
```

### Browse/list URL shapes

- Home: `https://www.underhentai.net/`
- Index: `https://www.underhentai.net/index/`
- Releases: `https://www.underhentai.net/releases/`
- Uncensored: `https://www.underhentai.net/uncensored/`
- Top: `https://www.underhentai.net/top/`
- Tag feeds: `https://www.underhentai.net/tag/{slug}/`
- Brand feeds: `https://www.underhentai.net/cat/brand/{slug}/`
- Search: `https://www.underhentai.net/?s={query}`

Pagination:

- Home/tag/archive pages: `/page/{n}/` or `/tag/{slug}/page/{n}/`
- Search: `/?s={query}&page={n}`

### Metadata + stream extraction

- **Watch URL shape:** `https://www.underhentai.net/{slug}/`
- **Episode stream page:** `https://www.underhentai.net/watch/?id={id}&ep={ep}`
- **List cards:** `article.data-block` with `.article-header h2 a`
- **Streams:** parse episode cards (`.ep2-card`) for Raw/Subbed variants; for each variant add `{variant} Krakenfiles` and `{variant} Lulustream` embed URLs from the linked `/watch/?id={id}&ep={ep}` page inline JS. Variant labels: `Japanese raw`, `English sub`, `Spanish sub`, or `Sub`. MEGA/ouo download links are omitted. For direct `/watch/` URLs, resolve the parent post via `/?p={id}` redirect before labeling.
- Do **not** resolve KrakenFiles embed pages to direct MP4 â€” keep embed URLs only.

Example stream labels:

```text
Japanese raw Krakenfiles
Japanese raw Lulustream
English sub Krakenfiles
English sub Lulustream
Spanish sub Krakenfiles
Spanish sub Lulustream
```
- Use `curl_cffi` browser impersonation with `Referer: https://www.underhentai.net/`

### Categories

Home, Index, Releases, Uncensored, Top, and popular `/tag/{slug}/` feeds in `categories.json`.

### Registration checklist

Package folder: `backend/app/scrapers/underhentai/`.

Besides creating `backend/app/scrapers/underhentai/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `underhentai`, `underhentai.net`, `uhen`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists for `underhentai.net`, `static.underhentai.net`, `krakenfiles.com`, `krakencloud.net`, `luluvdo.com`)
- `backend/app/api/endpoints/explore.py` (`sourceId="underhentai"`)

### Manual verification

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.underhentai.net/cheat-item-kanrikyoku-no-oshigoto-ex/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.underhentai.net/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.underhentai.net/tag/ahegao/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=underhentai"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.underhentai.net/watch/?id=11135&ep=0"
```

## LetsPorn.com (letsporn) Implementation Notes

[LetsPorn.com](https://letsporn.com/) is a KVS/kt_player tube site with root-level slug watch URLs, signed `/get_file/` MP4 links in inline `flashvars`, and home/category/channel/pornstar feeds.

### Host aliases

- `letsporn.com`
- `www.letsporn.com`
- `img.letsporn.com` (thumbnails CDN)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "letsporn.com" or h.endswith(".letsporn.com")
```

### Listing and pagination (`list_videos`)

- Home: `https://letsporn.com/`
- Popular: `https://letsporn.com/popular/`
- Newest: `https://letsporn.com/newest/`
- Best: `https://letsporn.com/best/`
- Category: `https://letsporn.com/categories/{slug}/`
- Category sort: `https://letsporn.com/categories/{slug}/?sort=newest|best|popular`
- Channel: `https://letsporn.com/channels/{slug}/`
- Pornstar: `https://letsporn.com/pornstars/{slug}/`

**Pagination:** append `/{page}/` to the list path (page 1 omits the page segment). Preserve existing query params such as `sort=newest`. Examples:

- Page 2 popular: `https://letsporn.com/popular/2/`
- Page 2 newest: `https://letsporn.com/newest/2/`
- Page 2 category: `https://letsporn.com/categories/teen/2/`
- Page 2 category (sorted): `https://letsporn.com/categories/teen/2/?sort=newest`
- Page 2 channel: `https://letsporn.com/channels/brazzers/2/`

For bare home (`https://letsporn.com/`), page 2+ maps to `https://letsporn.com/popular/{page}/` because root `/2/` returns 404.

Do **not** use `?page=` â€” LetsPorn ignores that query param and returns page 1 again.

Parse cards from anchors whose `href` matches `https://letsporn.com/{slug}-{id}/`. Thumbnails often come from `img.letsporn.com/contents/videos_screenshots/...`.

Use `curl_cffi` (Chrome impersonation) with `Referer: https://letsporn.com/`.

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://letsporn.com/{slug}-{id}/` (e.g. `/mia-khalifa-wants-bbc-to-bang-her-brutally-once-again-5477/`)
- **Embed URL shape:** `https://letsporn.com/embed/{video_id}`
- **Download URL shape:** `https://letsporn.com/download/{video_id}`
- **Metadata:** Open Graph (`og:title`, `og:image`), `h1`, and inline `flashvars` (`video_title`, `video_categories`, `video_tags`, `video_id`)
- **Streams:** signed progressive MP4 links from `flashvars`:
  - `video_url` + `video_url_text`
  - `video_alt_url` + `video_alt_url_text`
  - `video_alt_url2` + `video_alt_url2_text`
  - `video_alt_url3` + `video_alt_url3_text`
- Strip `function/0/` kt_player prefixes before resolving `/get_file/` redirects.
- Send `Referer: https://letsporn.com/` when accessing `/get_file/` URLs.

### Categories (`get_categories`)

Home, Most Viewed, and sample category feeds in `categories.json`.

### Registration checklist for LetsPorn

Package folder: `backend/app/scrapers/letsporn/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `letsporn`, `letsporn.com`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="letsporn"`)

### LetsPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://letsporn.com/mia-khalifa-wants-bbc-to-bang-her-brutally-once-again-5477/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://letsporn.com/categories/teen&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=letsporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://letsporn.com/mia-khalifa-wants-bbc-to-bang-her-brutally-once-again-5477/"
```

## TeamSkeetTube.com (teamskeettube) Implementation Notes

[TeamSkeetTube.com](https://www.teamskeettube.com/) is a WordPress tube site using the `clean-tube-player` plugin. Watch pages embed XVideos via a base64-encoded `player-x.php?q=` payload; there are no direct MP4 URLs on the page.

### Host aliases

- `teamskeettube.com`
- `www.teamskeettube.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in {"teamskeettube.com", "www.teamskeettube.com"}:
        return True
    return h.endswith(".teamskeettube.com")
```

### Listing and pagination (`list_videos`)

- Home: `https://www.teamskeettube.com/`
- Latest: `https://www.teamskeettube.com/?filter=latest`
- Random: `https://www.teamskeettube.com/?filter=random`
- Category: `https://www.teamskeettube.com/video/category/{slug}/`
- Categories index (paginated, 4 pages): `https://www.teamskeettube.com/categories/`
- Pornstars: `https://www.teamskeettube.com/pornstars/`

`categories.json` has 73 entries (Home, Latest, Random + 70 brand categories scraped from `/categories/` pages 1â€“4). The `freeuse` slug is an alias that redirects to home on-site; use `freeuse-bundle` (mapped automatically in `list_videos`).

WordPress-style path pagination (not `?page=N`):

- Page 2 home: `https://www.teamskeettube.com/page/2/`
- Page 2 category: `https://www.teamskeettube.com/video/category/anal-mom/page/2/`
- Page 2 latest: `https://www.teamskeettube.com/page/2/?filter=latest`

Query params such as `?filter=latest` are preserved; strip any existing `/page/N/` segment before appending the new page path.

Parse cards from `article.thumb-block` / `article.loop-video` anchors matching `https://www.teamskeettube.com/video/{slug}/` (exclude `/video/category/` links). Category name comes from `category-{slug}` article classes.

Use `curl_cffi` (Chrome impersonation) with `Referer: https://www.teamskeettube.com/` â€” plain `httpx` may get 406 Mod_Security on some URLs.

### Scraping (`scrape`)

- **Watch URL shape:** `https://www.teamskeettube.com/video/{slug}/`
- **Player:** `player-x.php?q={base64}` decodes to `tag=<iframe src="https://www.xvideos.com/embedframe/{id}">`
- **Streams:** expose decoded XVideos embed URLs as `format: "embed"` (same pattern as yesporn/justporn)
- **Metadata:** `og:title`, `og:image`, `og:description`, JSON-LD Article; category from first `/video/category/` link

Package folder: `backend/app/scrapers/teamskeettube/`.

Register in:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `teamskeettube`, `teamskeettube.com`)
- `backend/app/services/video_streaming.py`
- `backend/app/models/schemas.py`
- `backend/app/api/endpoints/explore.py` (`sourceId="teamskeettube"`)

### Test commands

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/scrape" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.teamskeettube.com/video/pervz-chloe-temple-concept-charmed/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.teamskeettube.com/video/category/pervz&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.teamskeettube.com/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=teamskeettube"

curl "http://127.0.0.1:8000/api/v1/videos/info?url=https://www.teamskeettube.com/video/pervz-chloe-temple-concept-charmed/"
```

## Sosalkino (sosalkino.guru) Implementation Notes

[Sosalkino](https://wvw.sosalkino.guru/) is a Russian KVS/kt_player tube site with slug-based watch URLs under `/videos/`, signed `/get_file/` MP4 links in inline `flashvars`, and numeric path pagination.

### Host aliases

- `wvw.sosalkino.guru` (current working mirror)
- `sosalkino.guru`
- `www.sosalkino.guru`
- `sosalkino.ooo`
- `www.sosalkino.ooo`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in {"sosalkino.guru", "wvw.sosalkino.guru", "sosalkino.ooo"}:
        return True
    return h.endswith(".sosalkino.guru") or h.endswith(".sosalkino.ooo")
```

### Listing and pagination (`list_videos`)

- Home: `https://wvw.sosalkino.guru/`
- Latest: `https://wvw.sosalkino.guru/latest-updates/`
- Top rated: `https://wvw.sosalkino.guru/top-rated/`
- Most popular: `https://wvw.sosalkino.guru/most-popular/`
- Short clips: `https://wvw.sosalkino.guru/short/`
- Category: `https://wvw.sosalkino.guru/categories/{slug}/`

**Pagination:** append `/{page}/` to the list path (page 1 omits the page segment). Examples:

- Page 2 home: `https://wvw.sosalkino.guru/2/`
- Page 2 category: `https://wvw.sosalkino.guru/categories/anal/2/`

Do **not** use `?page=` â€” Sosalkino ignores that query param.

Parse cards from `div.item > a.link[href*='/videos/']`. Thumbnails use lazy-loaded `data-src` / `data-webp`; preview clips are in `data-preview` on `.img-holder`.

Use `curl_cffi` (Chrome impersonation) with `Referer: https://wvw.sosalkino.guru/`.

### Metadata and streams (`scrape`)

- **Watch URL shape:** `https://wvw.sosalkino.guru/videos/{slug}/`
- **Embed URL shape:** `https://wvw.sosalkino.guru/embed/{video_id}/`
- **Metadata:** Open Graph (`og:title`, `og:image`, `og:duration`, `og:description`), inline `flashvars` (`video_title`, `video_categories`, `video_models`, `video_id`)
- **Streams:** signed progressive MP4 links from `flashvars`:
  - `video_url` + `video_url_text`
  - `video_alt_url` + `video_alt_url_text`
  - `video_alt_url2` + `video_alt_url2_text`
  - `video_alt_url3` + `video_alt_url3_text`
- Resolve `/get_file/` redirects with `Referer` set to the watch page URL.
- Prefer `og:duration` over related-video `.duration` spans on watch pages.

### Categories (`get_categories`)

`categories.json` has 163 entries: Home, Latest Updates, Top Rated, Most Popular, Short Videos, plus category feeds scraped from the site header.

### Registration checklist for Sosalkino

Package folder: `backend/app/scrapers/sosalkino/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` with aliases `sosalkino`, `sosalkino.guru`, `wvw.sosalkino.guru`, `sosalkino.ooo`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists)
- `backend/app/api/endpoints/explore.py` (`sourceId="sosalkino"`)

### Sosalkino verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://wvw.sosalkino.guru/videos/fotosessiya-12-letney-davnosti/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://wvw.sosalkino.guru/categories/anal&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://wvw.sosalkino.guru/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=sosalkino"

curl "http://127.0.0.1:8000/api/v1/videos/info?url=https://wvw.sosalkino.guru/videos/fotosessiya-12-letney-davnosti/"
```

## TubePornClassic (tubepornclassic.com) Implementation Notes

[TubePornClassic](https://tubepornclassic.com/) is a Tubecup-network vintage/classic tube site (same JSON API stack as [TXXX](https://txxx.com/), [HotMovs](https://hotmovs.tube/), and [ShemaleZ](https://shemalez.com/)). Home HTML is JS-heavy; listing and scrape use JSON APIs, not card HTML. Watch URLs use a numeric ID and slug: `https://tubepornclassic.com/videos/{id}/{slug}/` (e.g. `/videos/1248889/crazy-porn-clip-vintage-greatest/`). Embed fallback: `https://tubepornclassic.com/embed/{id}`. Thumbnails live on `tn.tubepornclassic.com`.

### Host aliases

- `tubepornclassic.com`
- `www.tubepornclassic.com`
- `tn.tubepornclassic.com` (thumbnails/CDN)
- Any `*.tubepornclassic.com` subdomain (`can_handle()` suffix match; `tn.` is normalized back to the main host for API calls)

Stream CDN hosts resolve to `*.ahcdn.com` â€” already covered by the global `ahcdn.com` allowlist in `schemas.py` and `video_streaming.py`.

### Listing and pagination (`list_videos`)

Uses the Tubecup JSON API â€” not HTML scraping:

- **Latest:** `https://tubepornclassic.com/latest-updates/` (page 2+ â†’ `/latest-updates/2/`)
- **Sort feeds:** `/most-popular/`, `/longest/`, `/top-rated/`, `/most-viewed/`
- **Categories:** `/categories/{slug}/` (page 2+ â†’ `/categories/{slug}/2/`). Confirmed working slug: `vintage`
- **Search:** `/search/?s={query}`

List endpoint:

```text
GET /api/videos2.php?params={lifetime}/str/{sort}/{count}/{section}.{object_id}.{page}.all..
```

Search adds `&s={query}` with `sort=relevance`. Response shape: `{ "videos": [ ... ], "total_count", "pages" }`.

Use `curl_cffi` (Chrome impersonation) as primary fetch. Plain httpx may still work on this host (home + `videos2.php` returned 200 without impersonation in probing).

### Metadata and streams (`scrape`)

1. **Video info:** `GET /api/json/video/{lifetime}/{million_bucket}/{thousand_bucket}/{id}.json`
   - Example: `/api/json/video/86400/1000000/1248000/1248889.json`
   - `million_bucket = int(1e6 * (id // 1e6))`, `thousand_bucket = 1000 * (id // 1000)`
2. **Stream files:** `GET /api/videofile.php?video_id={id}&lifetime=8640000`
   - Returns array of `{ format, video_url }` where `video_url` is custom-base64-encoded
3. **Decode streams:** translate Cyrillic look-alike chars + `,`/`.`/`~` â†’ standard base64, then decode to CDN URL (often `/get_file/...`)
4. **Resolve `/get_file/`:** follow redirect (no auto-redirect) to signed MP4/HLS URL
5. **Embed fallback:** `https://{host}/embed/{id}` when direct streams fail

Embed URLs (`/embed/{id}`) are accepted by `scrape()` â€” the numeric id is extracted and full metadata/streams are resolved the same way as watch-page URLs.

### Preview clips

The API `pv` field is often stale/wrong. Build preview URLs from video id when `pv` does not contain the id:

```text
https://vp2.txxx.com/c12/videos/{1000*(id//1000)}/{id}/{id}_tr.mp4
```

(Same shared Tubecup preview CDN as TXXX/HotMovs/ShemaleZ.)

### Categories (`get_categories`)

`/api/json/categories/14400/str.json` returned an empty `categories` array on this host. Ship sort feeds plus working category slugs (Vintage, Classic, Retro, HD, MILF, Mature, Anal, Lesbian, etc.) in `categories.json`.

### Registration checklist for TubePornClassic

Package folder: `backend/app/scrapers/tubepornclassic/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` â€” source aliases: `tubepornclassic`, `tubepornclassic.com`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map)
- `backend/app/models/schemas.py` (scrape/list URL allowlists including `tn.tubepornclassic.com`)
- `backend/app/api/endpoints/explore.py` (`sourceId="tubepornclassic"`)

### TubePornClassic verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://tubepornclassic.com/videos/1248889/crazy-porn-clip-vintage-greatest/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tubepornclassic.com/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tubepornclassic.com/categories/vintage/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tubepornclassic.com/search/?s=vintage&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tubepornclassic"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://tubepornclassic.com/videos/1248889/crazy-porn-clip-vintage-greatest/"
```

## XXXDan (xxxdan.com) Implementation Notes

[XXXDan](https://xxxdan.com/) is a cdn3x/Xozilla tube site. Watch URLs use a short alphanumeric id plus slug: `https://xxxdan.com/{id}/{slug}.html` (e.g. `/O5Na1z/lonely-55-year-old-milf-mom-hooks-up-with-stepson.html`). Embed fallback: `https://xxxdan.com/embed/{id}`. Thumbnails live on `t*.cdn3x.com`; progressive MP4s on `c*.cdn3x.com` / `d*.cdn3x.com`.

### Host aliases

- `xxxdan.com`
- `www.xxxdan.com`
- `xxxdan2.com`
- `www.xxxdan2.com`
- Any `*.xxxdan.com` / `*.xxxdan2.com` subdomain (`can_handle()` suffix match)

Stream/thumbnail CDN hosts resolve to `*.cdn3x.com` â€” allowlisted in `schemas.py` and `video_streaming.py`. Do **not** put `cdn3x.com` in `can_handle()`; that would treat CDN URLs as watch pages.

### Listing and pagination (`list_videos`)

Parse cards from `a.video-card[href]` (`data-vid`, `data-tid`, `.video-card__title`, `.video-card__duration`, thumb `img`).

- **Home / Trending:** `https://xxxdan.com/` or `https://xxxdan.com/straight/trending` (page 2+ â†’ `/straight/trending/{page}`)
- **Popular:** `/straight/popular1` (page N â†’ `/straight/popular{N}`)
- **Recent:** `/newest` (page 2+ â†’ `/newest/{page}`)
- **Category:** `/channel/{slug}` (page 2+ â†’ `/channel/{slug}/{page}`)
- **Search:** `/search/{query}` or `/search?query={query}` (page 2+ â†’ `/search/{query}/{page}`)

Language prefixes (`/ja/`, `/fr/`, â€¦) are stripped when building list URLs. Do **not** treat `/channel/{slug}` as a watch URL â€” `channel` is a reserved path.

### Metadata and streams (`scrape`)

- **Metadata:** JSON-LD `VideoObject` (`name`, `thumbnailUrl`, `duration`, `uploadDate`, `embedUrl`), `h1.video-title`, Open Graph `og:image`
- **Duration:** `PT00H33M10S` / `PT33M10S` on JSON-LD or `.video-desc__stat time`
- **Tags:** `.tag-row a[rel=tag]` and related-tag chips
- **Streams:** inline Flowplayer config:

```js
sources.push({type:'video/mp4',src:'https://c44.cdn3x.com/xd/...',engine:'html5'});
```

Two CDN mirrors of the same file are common. Keep both. Quality is usually unlabeled (`default`).

### Categories (`get_categories`)

`categories.json` has sort feeds (Trending, Popular, Recent) plus high-traffic `/channel/{slug}` feeds scraped from `/channels`.

### Registration checklist for XXXDan

Package folder: `backend/app/scrapers/xxxdan/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` â€” source aliases: `xxxdan`, `xxxdan.com`, `www.xxxdan.com`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, quality map including `cdn3x.com`)
- `backend/app/models/schemas.py` (scrape/list URL allowlists including `cdn3x.com`)
- `backend/app/api/endpoints/explore.py` (`sourceId="xxxdan"`)

### XXXDan verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://xxxdan.com/O5Na1z/lonely-55-year-old-milf-mom-hooks-up-with-stepson.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://xxxdan.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://xxxdan.com/straight/trending&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://xxxdan.com/channel/milf&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://xxxdan.com/search/milf&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=xxxdan"

curl "http://127.0.0.1:8000/api/v1/videos/info?url=https://xxxdan.com/O5Na1z/lonely-55-year-old-milf-mom-hooks-up-with-stepson.html"
```

## PornXXX Implementation Notes

[PornXXX.tube](https://pornxxx.tube/) is a hprofits-style gallery tube. Video pages live under `/gallery/{id}/{slug}/`, category pages under `/videos/{slug}/`, tag/search pages under `/tags/{query}/`. Thumbnails come from `icdn05.pornxxx.tube`; signed progressive MP4s from `vcdn01/vcdn02.pornxxx.tube`.

### Host aliases

- `pornxxx.tube`
- `www.pornxxx.tube`
- Any `*.pornxxx.tube` subdomain (`can_handle()` suffix match)

Do **not** put the CDN hosts (`icdn05.`, `vcdn01.`, `vcdn02.`, `u3.`) in `can_handle()`; they are media hosts only and are allowlisted in `schemas.py`.

### Listing and pagination (`list_videos`)

Parse cards from `a.js-gallery-link[href]` matching `/gallery/{numeric_id}/{slug}`:

- title: anchor `title` attr, then `.b-thumb-item__title`, then img `alt`
- thumbnail: img `data-src` (`https://icdn05.pornxxx.tube/{dir}/{gid}_{n}.jpg`)
- duration: `.b-thumb-item__duration span` (`mm:ss` / `hh:mm:ss`)
- views: not exposed by this platform (`None`)

Pagination is a query param on every listing route (verified `?page=2..N`):

- **Popular:** `https://pornxxx.tube/` (page N â†’ `/?page=N`)
- **Newest:** `https://pornxxx.tube/new-vids/` (page N â†’ `/new-vids/?page=N`)
- **Category:** `https://pornxxx.tube/videos/{slug}/` (page N â†’ `/videos/{slug}/?page=N`)
- **Tag/Search:** `https://pornxxx.tube/tags/{query}/` (page N â†’ `/tags/{query}/?page=N`)

Skip the ad cards (`random-thumb` blocks) â€” they never match the `/gallery/` href pattern, so the `_normalize_video_href` filter drops them automatically.

### Metadata and streams (`scrape`)

- **Metadata:** `og:title` (suffix ` - PornXXX.tube` is stripped), `og:description`, poster from `video#video[poster]` / `og:image` (both `icdn05.pornxxx.tube`).
- **Duration:** the `script#video-track-data` JSON blob carries `"vd": <seconds>`; format to `mm:ss` / `hh:mm:ss`, fallback to text regex.
- **Uploader:** `.b-gallery-meta__item.channel-link .b-gallery-meta__text` (the "Uploaded by:" value).
- **Tags:** all `/tags/{slug}/` anchor texts. **Category:** first `/videos/{slug}/` anchor text.
- **Streams:** the page embeds a direct signed progressive MP4 in `<video id="video"><source src="https://vcdn02.pornxxx.tube/key=...,end=.../video18/.../{hash}_480.mp4" type="video/mp4">`. The `end=<unix>` token is time-limited but the scraper returns it fresh per request, so no redirect resolution is needed (no `get_file` dance).
- Quality is parsed from the filename suffix (`_480.mp4` â†’ `480p`), unknown â†’ `source`. Inline scripts are also scanned (unescaped `\/`, `\u0026`) for `.mp4`/`.m3u8` fallbacks; `video.default` prefers the highest-scored MP4.
- **Related videos:** the `.js-related-list` section is parsed into `related_videos` (same shape as list items), enabling `hasRelatedVideos=True` in the explore source.

### Categories (`get_categories`)

`categories.json` seeds the Popular/Newest tabs plus `/videos/{slug}/` category routes from the public `/categories` index (counts available in `.b-thumb-item__count`).

### Registration checklist for PornXXX

Package folder: `backend/app/scrapers/pornxxx/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` â€” source aliases: `pornxxx`, `pornxxx.tube`, `www.pornxxx.tube`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text)
- `backend/app/models/schemas.py` (scrape allowlist incl. CDN hosts; list base_url allowlist incl. `pornxxx.tube`)
- `backend/app/api/endpoints/explore.py` (`sourceId="pornxxx"`, `hasRelatedVideos=True`)

### PornXXX verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pornxxx.tube/gallery/14693036/jav-hd-asian-wants-real-sex-in-her-lingerie/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornxxx.tube/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornxxx.tube/new-vids/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pornxxx.tube/videos/asian/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pornxxx"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://pornxxx.tube/gallery/14693036/jav-hd-asian-wants-real-sex-in-her-lingerie/"
```

## SxyPrn Implementation Notes

[SxyPrn](https://sxyprn.com/) (SexyPorn) is a community/blog-style tube. Videos are "posts" at `/post/{hex_id}.html`; thumbnails/preview clips live on `b1/b2/b3.trafficdeposit.com`. Posts are user-made: the actual playable sources are usually **external players linked in the post text** (Vidara, LuluStream, DoodStream, SaveFiles), not a direct file.

### Direct .vid link — known dead end (do NOT use)

The page HTML carries `<span class='vidsnfo' data-vnfo='{"{id}":"/cdn/cN/.../....vid"}'>`. `main2.js` rebuilds a direct URL with (see `getvsrc`/`ssut51`/`boo`/`preda`):

```
tmp[1] += "8/" + base64url("<digitsum(seg6)>-sxyprn.com-<digitsum(seg7)>")   # '=' -> '.'
tmp[5] = str(int(tmp[5]) - digitsum(seg6) - digitsum(seg7))
```

This transform was implemented and verified byte-for-byte against `main2.js`, but the built `https://sxyprn.com/cdn8/.../....vid` URL still returns **404** server-side (tested with fresh tokens + PHPSESSID + Referer, Range and plain GET). The scraper therefore **excludes** the direct link and returns embed streams only.

### Host aliases

- `sxyprn.com`
- `www.sxyprn.com`
- Any `*.sxyprn.com` subdomain (`can_handle()` suffix match)

Do not put `trafficdeposit.com` (media CDN) or embed hosts in `can_handle()`; they are allowlisted in `schemas.py` only.

### Stream extraction (`scrape`) — embeds from post text

Each post text contains external player links as `a.extlink` anchors. They are normalized to embed (player) form and returned as `format="embed"` streams labeled `Server 1`, `Server 2`, ...:

| Found in post text             | Returned stream                          |
|--------------------------------|------------------------------------------|
| `https://vidara.so/v/{id}`     | `https://vidara.so/e/{id}`               |
| `https://vidara.to/v/{id}`     | `https://vidara.to/e/{id}`               |
| `https://lulustream.com/{id}`  | `https://luluvdo.com/e/{id}`             |
| `https://doodstream.co/e/{id}` | unchanged (already embed form)           |
| `https://doodstream.co/{id}`   | `https://doodstream.co/e/{id}`           |
| `https://savefiles.com/{id}`   | kept as-is (embed host, no /e/ route)    |

- `video.default` = first embed (`Server 1`); `has_video=True` only when at least one embed link exists.
- Metadata: `og:title` (suffix ` on SexyPorn OG` / ` on the SexyPorn` stripped), `og:description`, `og:image` for thumbnail; duration from `meta[itemprop=duration]` (`PT11M2S` -> `11:02`), fallback to the `Video Info -> duration:` block; views from `.post_control_time` (`35267 views`); uploader from `.pes_author_div .a_name`; tags from `a.hash_link[label]` in the main post.
- Deleted posts are soft-404 (HTTP 200 + "Post Not Found") â€” the scraper raises so callers return 502 instead of an empty result.
- Related videos: other `.post_el_small` cards on the post page (same parser as listings).

### Listing and pagination (`list_videos`)

Cards are `div.post_el_small` containing `a.js-pop[href^="/post/{hex_id}.html"]`, thumb `img[data-src]` (trafficdeposit, protocol-relative), duration in `span.duration_small`, views in `.post_control_time`.

Page URLs by route (page 1 = `base_url` unchanged):

- **Home/New:** `https://sxyprn.com/` (page N -> `/main-{N}.html`)
- **Tag/Search keys:** `/{tag}.html` (page N -> `/{tag}-{N}.html`)
- **Top:** `/popular/top-pop.html`, `/popular/top-viewed.html` (page N -> `/popular/top-pop-{N}.html`)
- **Blog/Porn Wall:** `/blog/all/0.html` is page 1, `/blog/all/1.html` is page 2 (0-based!)
- **Author:** `/blog/{author_id}/0.html` (same 0-based pattern)
- **Orgasmic:** `/orgasm` (page N -> `/orgasm-{N}.html`)

`_build_list_page_url` implements all of these; search uses the tag route (`/{query}.html`), which doubles as search.

### Categories (`get_categories`)

`categories.json` seeds the sort feeds (New `/`, Top `/popular/top-pop.html`, Viewed `/popular/top-viewed.html`) plus high-traffic `/{tag}.html` keys (anal, asian, milf, onlyfans, teen, ...).

### Registration checklist for SxyPrn

Package folder: `backend/app/scrapers/sxyprn/`.

Also update:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py` (import, `_scrape_dispatch`, `_list_dispatch`, `/api/v1/categories` â€” source aliases: `sxyprn`, `sxyprn.com`, `www.sxyprn.com`)
- `backend/app/services/video_streaming.py` (scraper branch, supported-host text, `available_qualities` host list + `per_stream_format_keys` so flat `Server N` / `Server N_format` fields are emitted)
- `backend/app/models/schemas.py` (scrape allowlist incl. `trafficdeposit.com` + embed hosts; list base_url allowlist incl. `sxyprn.com`)
- `backend/app/api/endpoints/explore.py` (`sourceId="sxyprn"`)

### SxyPrn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://sxyprn.com/post/6a94662d957e8.html\"}"

curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://sxyprn.com/post/6a948fbfb23a5.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://sxyprn.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://sxyprn.com/anal.html&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=sxyprn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://sxyprn.com/post/6a94662d957e8.html"
```

Expected stream result for `6a94662d957e8` (post embeds Vidara): `Server 1` -> `https://vidara.so/e/0Kf3bhXfpwEQX`. For `6a948fbfb23a5`: `Server 1` -> `https://luluvdo.com/e/8lvf2vq7kjvg`, `Server 2` -> `https://doodstream.co/e/fvar94xwdw8f`.


## YouPerv Implementation Notes

[YouPerv](https://youperv.com/) is a **DataLife Engine (DLE)** tube site (not WordPress). Canonical video pages use `/{category}/{numeric-id}-{slug}.html` (for example `/cumshot/1972058148-hijab-mylfs-....html`). Videos play via **FluidPlayer** with a single direct MP4 `<source>` on a CDN host (`files.klubnichka-hd.com`).

### Host aliases

- `youperv.com`
- `www.youperv.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")
```

### Listing and pagination (`list_videos`)

- Parse `div.item` cards: link `a.item-link` (fallback: first `a[href]`), thumb `img.xfieldimage.poster` (or first `img`), title `.item-title h2` / img `alt`, duration `.item-meta.meta-time`, pornstars from `/xfsearch/pornstar/` links.
- Accept only URLs whose last path segment matches `^\d{2,}-.+\.html$`; skip `/tags/`, `/xfsearch/`, `/user/`, `/page/`, and static pages (`/2257.html`, `/top-porn-videos.html`, `/top-50-most-viewed-videos.html`).
- Strip DLE locale prefixes (`/es/`, `/fr/`, ...) from captured hrefs; canonical form is `https://youperv.com/{category}/{id}-{slug}.html`.
- Card titles end with ` ( DD.MM.YYYY )` — strip the suffix in `_clean_title`.
- Thumbnails are site-relative (`/uploads/posts/...`) — resolve with `urljoin`.
- Pagination: page 1 uses `base_url` unchanged; page *n* > 1 inserts `/page/{n}/` under the current path (`/` -> `/page/2/`, `/anal/` -> `/anal/page/2/`), replacing any existing `/page/{m}/` segment.
- **DLE search:** the search form is POST, but GET works: `https://youperv.com/index.php?do=search&subaction=search&story={query}`. Search results paginate with the `search_start={n}` query param (NOT `/page/{n}/`), so `_build_list_page_url` branches on `do=search` / `search_start`.

### Metadata and streams (`scrape`)

- Metadata fallback order:
  1. JSON-LD `@graph` `Movie` node (`name` is the clean title, `datePublished` is ISO upload date)
  2. `og:title` / `og:description` / `og:image`
  3. `twitter:title` / `twitter:description` / `twitter:image`
  4. `h1` (strip trailing `DD.MM.YYYY` and `HD`) / page `<title>` (split on ` » `)
- Stream extraction: `video[src]` + `video > source[src]` (FluidPlayer block), then inline `.mp4` / `.m3u8` regex scan, then `iframe[src]` embed fallback (filter ad iframes: `magsrv.com`, `mbidadm.com`, `acscdn.com`, VAST tags, etc.).
- Direct MP4 lives on `files.klubnichka-hd.com` with **spaces in the URL** — keep the URL raw (aiohttp/httpx encode it); quality label falls back to `source` (no per-resolution tiers).
- Duration from `.fmeta .fm-item` (fa-clock-o row) or `mm:ss` regex; pornstars scoped to `.fmeta` only (Related Videos also contain xfsearch links); tags from `.full-tags a`; related videos reuse the card parser on `.items .item`.
- Views are **not rendered on detail pages** (only on listing cards), so `views` may be `None` from `scrape()`.

### Categories (`get_categories`)

`categories.json` seeded from the nav: Most Viewed 30 Day (`/top-50-most-viewed-videos.html`), Top Rated 30 Day (`/top-porn-videos.html`), Tags (`/tags/`), plus the 33 category routes (`/anal/`, `/milf/`, ...). `/api/v1/categories?source=youperv` serves them.

### Registration checklist for YouPerv

Besides creating `backend/app/scrapers/youperv/`, all of these were updated:

- `backend/app/scrapers/__init__.py` (import + `__all__`)
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=youperv`)
- `backend/app/services/video_streaming.py`
  - import list + `elif youperv.can_handle(host):` branch
  - unsupported-host help text (`youperv.com`)
  - `available_qualities` host list + `per_stream_format_keys` in `get_video_info` (`youperv.com`, `files.klubnichka-hd.com`, `klubnichka-hd.com`)
  - matching `host_l` checks in `get_stream_url`
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`"youperv.com"`)
  - list base URL allowlist (`"youperv.com"`)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="youperv"`, `baseUrl="https://youperv.com/"`, search template uses the GET search route)

### YouPerv verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://youperv.com/cumshot/1972058148-hijab-mylfs-brandi-swan-boudoir-photoshoot-your-husband-will-only-see-the-nudes-not-how-you-sucked-my-cock.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://youperv.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://youperv.com/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://youperv.com/index.php?do=search&subaction=search&story=brazzers&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=youperv"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://youperv.com/cumshot/1972058148-hijab-mylfs-brandi-swan-boudoir-photoshoot-your-husband-will-only-see-the-nudes-not-how-you-sucked-my-cock.html"
```

Notes from live testing (2026-09):

- Listing, categories, search (pages 1 and 2 via `search_start`), and direct-MP4 `scrape()` all verified working.
- Deleted posts return **HTTP 410 Gone** (e.g. some URLs from older index pages); the pool raises and `/api/v1/videos` returns an empty page — clients should treat empty results as normal.
- The CDN MP4 URL contains spaces (raw title); do not percent-encode before storing — HTTP clients handle it.

### YouPerv CDN Referer requirement

The direct MP4 host `files.klubnichka-hd.com` **requires a Referer header**:

- `GET` with `Range` and **no Referer** -> **403** (text/html block page).
- With `Referer: https://youperv.com/` **or the full video page URL** -> **206** `video/mp4`.

Because the full page URL is accepted as Referer, **no backend proxy is needed**:

- **Backend** extraction (`scrape()` / `/api/v1/videos/stream`) already returns the raw CDN MP4; clients that send the video page URL as `Referer` can play it directly.
- **Flutter app** uses the generic backend flow (`ScraperApiService.fetchVideoStream` / `fetchDownloadLinks`) — BetterPlayer sends `Referer: <video page url>` via its `headers` map automatically, and `_startDownload` passes the page URL as Referer, so both playback and downloads work without any proxy or local scraper.
- Keep CDN filenames raw (they contain spaces); ExoPlayer/AVPlayer and `package:http` encode them at request time.

## Perverzija (tube.perverzija.com) Implementation Notes

[Perverzija](https://tube.perverzija.com/) is a WordPress tube site. Video posts use **root-level slugs** (`/{slug}/`, single segment). Watch pages embed a third-party **XtremeStream** player iframe; there are no direct MP4/HLS URLs in the page HTML.

### Host aliases

- `tube.perverzija.com`
- `www.tube.perverzija.com`

Do **not** match bare `perverzija.com` (different site).

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "tube.perverzija.com" or h.endswith(".tube.perverzija.com")
```

### Listing and pagination (`list_videos`)

- Cards: `div.video-item` → thumb link `a[href]` (root-level slug), title `.item-head h2 a` (`title` attr) or img `alt`, duration `span.rating-bar` (`mm:ss`), studio from `a[href*='/studio/']`
- Accept only single-segment root URLs; skip reserved paths (`/page/`, `/tag/`, `/studio/`, `/vr/`, `/featured-scenes/`, `/full-movie/`, `/category/`, `/author/`, `wp-*`, legal pages)
- Page 1 uses `base_url` unchanged; page *n* inserts `/page/{n}/` under the current path (WordPress pattern), preserving query params (e.g. `?s=`)
- Search: `https://tube.perverzija.com/?s={query}`
- Working list bases: `/`, `/tag/{slug}/`, `/studio/{slug}/`, `/vr/`, `/featured-scenes/`, `/full-movie/`, `/full-movie/erotic-movies/`

### Metadata and streams (`scrape`)

- Metadata fallback order:
  1. JSON-LD `VideoObject` (the page ships a **JSON array** from `saswp-schema-markup-output`: `headline`, `description`, `thumbnailUrl`, `duration` as ISO-8601 `PT25M26S`, `uploadDate`, `author.name`)
  2. `og:title` (prefix `Watch ` and suffix ` | Perverzija.com` stripped), `og:description`, `og:image`
  3. visible `h1` / page `<title>`
- Views: `span.post-views-count`; tags from `a[href*='/tag/']`; studio from `a[href*='/studio/']`
- **Streams: embed only.** The player iframe lives in `#player-embed iframe[src]` → `https://pervl{N}.xtremestream.xyz/player/index.php?data={32-hex-token}`. The same URL is also in JSON-LD `embedUrl` and the card `data-embed` attribute. Return it as `format="embed"` / `quality="embed"` (same pattern as teamskeettube).
- The `data=` token is a static per-video id (identical in listing quick-view and JSON-LD).

### Stream Referer requirements (why playback is local Flutter HLS)

Both player endpoints are Referer-protected (Cloudflare):

- `player/index.php?data=` fetched directly needs `Referer: https://tube.perverzija.com/` (without it: HTTP 200 with body "You can't access the video directly")
- The real HLS lives at `{player-origin}/player/xs1.php?data={token}` (the player page sets `var m3u8_loader_url = '<origin>/player/xs1.php?data='` + `var video_id = '<token>'`); the master lists `&q=480` / `&q=720` media variants
- **`xs1.php` requires `Referer:` of the player page URL itself** — the site referer (`https://tube.perverzija.com/`) returns **403**

Because an HLS proxy would be required to serve this from the backend, the backend intentionally returns embed streams only and **the Flutter app extracts HLS locally** (approach from `goon-foss/goon` `app/extractors/tubes/perverzija.py`):

- `app/lib/features/source/data/scrapers/perverzija.dart` (`PerverzijaService.fetchVideo`) fetches the video page, regex-extracts the XtremeStream iframe (`(?<host>...\.xtremestream\.[a-z]+)/player/index\.php\?data=(?<id>[0-9a-f]{16,64})`), and builds both playlist URLs: `https://{host}/player/xs1.php?data={id}&q=720` and `&q=480` (720 = preferred default `stream_url`).
- A best-effort probe GET (Referer = player URL) verifies the primary playlist contains `#EXTM3U`; failure only flags `hls_unverified` and playback still returns the URLs.
- `_videoFormat` is set to `hls`; BetterPlayer sends `Referer: <player page URL>` via its `headers` map (`_streamReferer` override in `_hlsHeaders` / `_initializePlayer` / `_startDownload`).
- The manifest path does **not** end in `.m3u8`, so the `isHls` checks in the details page also honor `_videoFormat == 'hls'` (same trap as pornhat). Segments (`.html` camouflage on `*.xspcdn*.sa.com`) stream directly from CDN — BetterPlayer HLS handles them natively.

### Registration checklist for Perverzija

Besides creating `backend/app/scrapers/perverzija/`, all of these were updated:

- `backend/app/scrapers/__init__.py` (import + `__all__`)
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=perverzija`, `tube.perverzija.com`)
- `backend/app/services/video_streaming.py`
  - import list + `elif perverzija.can_handle(host):` branch
  - unsupported-host help text (`tube.perverzija.com`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`tube.perverzija.com`, `xtremestream.xyz`)
  - list base URL allowlist (`tube.perverzija.com`)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="perverzija"`, `baseUrl="https://tube.perverzija.com/"`, search template `https://tube.perverzija.com/?s={query}`, `hasRelatedVideos=True`)
- `app/lib/features/source/data/scrapers/perverzija.dart` (local Flutter HLS extraction via `xs1.php?data={id}&q={720|480}`)
- `app/lib/features/source/ui/source_video_details_page.dart`
  - playback branch (`sourceName == 'perverzija' || baseUrl.contains('tube.perverzija.com')`) using `PerverzijaService.fetchVideo` (Brazzpw-style local extraction)
  - `_streamReferer` override in `_hlsHeaders()` / `_initializePlayer()` / `_startDownload()` (HLS needs the player page URL as Referer)
  - `isHls` checks honor `_videoFormat == 'hls'` because the manifest path does not end in `.m3u8`

### Perverzija verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://tube.perverzija.com/zerotolerance-rachel-roxxx-rachel-roxxx-office-slut/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tube.perverzija.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tube.perverzija.com/tag/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://tube.perverzija.com/?s=brazzers&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=perverzija"

curl "http://127.0.0.1:8000/api/v1/videos/info?url=https://tube.perverzija.com/zerotolerance-rachel-roxxx-rachel-roxxx-office-slut/"
```

Notes from live testing (2026-09):

- Home, tag, VR, search listing (pages 1/2), `scrape()` metadata, embed stream, related videos, and categories all verified working via the scraper module directly. Playback uses local Flutter HLS extraction (BetterPlayer with player-page Referer).
- The search page reuses the same `div.video-item` cards; `?s=` query pagination via `/page/{n}/` works.
- Duration "94:20" (an `hh:mm` card label) parses as `94:20`; JSON-LD `PT25M26S` becomes `25:26` on detail pages.

## BigWank (bigwank.com) Implementation Notes

[BigWank](https://www.bigwank.com/) is a KVS-style tube (PornPapa network). Watch URLs use a numeric ID plus 32-hex hash: `https://www.bigwank.com/videos/{id}/{hash}/` (e.g. `/videos/93380259/57780e1e4767ca34dd7c10aa791208c9/`). Embed player: `https://www.bigwank.com/embed/{id}`. Thumbnails live on `img.bigwank.com`; preview clips on `cast.bigwank.com`; resolved MP4s on `cdnawm.com`.

### Host aliases

- `bigwank.com`
- `www.bigwank.com`
- `img.bigwank.com` / `cast.bigwank.com` (media hosts, allowlisted only)
- `cdnawm.com` (resolved signed MP4 CDN, allowlisted only — not in `can_handle()`)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".bigwank.com")
```

### Listing and pagination (`list_videos`)

- Home: `https://www.bigwank.com/` (bare home has no `/{n}/` feed; page 2+ maps to `/most-popular/{n}/`)
- Feeds: `/latest-updates/`, `/most-popular/`, `/top-rated/` (page 2+ â†’ `/{feed}/{n}/`)
- Categories: `/categories/{slug}/` (page 2+ â†’ `/categories/{slug}/{n}/`)
- Models: `/models/{slug}/`
- Search: `/search/{query}/` (page 2+ â†’ `/search/{query}/{n}/`)
- **Pagination:** append `/{n}/` path segment (page 1 omits it). Do **not** use `?page=`.
- Parse cards from `div.thumb.item` blocks: link `a.thumb__top[href]`, thumb `img[src]` (`img.bigwank.com/...medium@2x/1.jpg`), title from `img[alt]` / `.thumb__title`, duration `.thumb__duration`, views from `.thumb__text` (`18K views`), uploader from `a.thumb-models__link` (skip "Suggest Pornstar" placeholders), preview from `.thumb__img[data-preview]` (`cast.bigwank.com/preview/{id}.mp4`).

Use `curl_cffi` (Chrome impersonation) as primary fetch; fall back to shared `pool.fetch_html`.

### Metadata and streams (`scrape`)

- Accepts both watch URLs and `/embed/{id}` URLs; embed URLs resolve to the canonical watch page via hash lookup in embed HTML.
- Metadata: `og:title`, `h1`, `og:image` / `video[poster]` (both point at `contents/videos_screenshots/.../preview_480m.mp4.jpg`), `meta[name=keywords]` (tags), views from `.video-info__text` (`4,124,948 views`), uploader from the "Added by:" row (fallback: first `/models/` link).
- **Duration:** watch pages expose it only inside the videojs thumbnails config â€” `var everyX = Math.floor({seconds} / 100)`. Parse that value and format as `m:ss` / `h:mm:ss`.
- **Streams:**
  1. `<video><source src=".../get_file/{token}/{dir}/{id}/{id}_480m.mp4/">` (label like `480m` in filename) plus the "Download:" row's `/get_file/` link. Deduplicate per quality label.
  2. **Resolve each `/get_file/` URL**: the site rejects **HEAD** (410) â€” use **GET with `Range: bytes=0-0`** and `Referer: <watch page URL>`; the 302 `Location` is the signed `cdnawm.com/key=...,end=.../.../{file}_480m.mp4` URL (tokens are short-lived).
  3. **Fallback rule:** if any `/get_file/` resolution fails or errors, drop that MP4 stream entirely and return **only** `https://www.bigwank.com/embed/{id}` as `format: "embed"` so playback continues in WebView.
  4. `video.default` prefers the resolved MP4; embed otherwise. `hls` is always `None` (progressive MP4 only).
- Related videos: `#list_videos_related_videos_items` cards reuse the same `div.thumb.item` parser (`.thumb.item` blocks on the watch page).

### Categories (`get_categories`)

`categories.json` has 75 entries: Home, Latest Updates, Most Popular, Top Rated, plus ~70 curated `/categories/{slug}/` feeds from the public categories index (Amateur, Anal, Asian, Big Ass, Big Tits, Blowjob, Ebony, Hardcore, Interracial, Latina, Lesbian, MILF, Teen, Threesome, Toys, ...).

### Registration checklist for BigWank

Package folder: `backend/app/scrapers/bigwank/`.

Also update:

- `backend/app/scrapers/__init__.py` (import + `__all__`)
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=bigwank`, `bigwank.com`, `www.bigwank.com`)
- `backend/app/services/video_streaming.py`
  - import list + `elif bigwank.can_handle(host):` branch
  - unsupported-host help text (`bigwank.com`)
  - `available_qualities` host list (`bigwank.com`, `cdnawm.com`) + `per_stream_format_keys` so flat `480p` / `embed` quality fields are emitted
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`bigwank.com`, `img.bigwank.com`, `cast.bigwank.com`, `cdnawm.com`)
  - list base URL allowlist (`bigwank.com`, `img.bigwank.com`, `cast.bigwank.com`)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="bigwank"`, `baseUrl="https://www.bigwank.com/"`, `pageSize=60`, `hasRelatedVideos=True`, search template `https://www.bigwank.com/search/{query}/`)

### BigWank verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.bigwank.com/videos/93380259/57780e1e4767ca34dd7c10aa791208c9/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.bigwank.com/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.bigwank.com/categories/anal/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=bigwank"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.bigwank.com/videos/93380259/57780e1e4767ca34dd7c10aa791208c9/"

curl "http://127.0.0.1:8000/api/v1/videos/info?url=https://www.bigwank.com/videos/93380259/57780e1e4767ca34dd7c10aa791208c9/"

curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.bigwank.com/embed/93380259\"}"
```

Notes from live testing (2026-09):

- Listing (latest-updates/category/search, pages 1+2), `scrape()` metadata, duration via thumbnails JS, `get_file` 302 resolution to `cdnawm.com` signed MP4, embed fallback when resolution fails (simulated), embed-URL input, related videos, categories, and `video_streaming.get_stream_url` flat quality fields all verified working via the scraper module directly.
- `/get_file/` tokens expire; resolution happens per request, so returned CDN URLs are always fresh. If the site ever stops redirecting, the scraper degrades to embed-only streams instead of exposing dead links.
- HEAD requests on `/get_file/` return 410 â€” always probe with GET + `Range` (see `_resolve_get_file_url`).

## BlackPornTube Implementation Notes

[BlackPorn.tube](https://blackporn.tube/) is **not** an HTML-rendered tube; it is a Vue SPA with a public JSON API (magma-style engine, KVS-adjacent). HTML scraping returns an empty JS shell, so this scraper is **API-first**: all listing and detail data comes from JSON endpoints, and stream URLs are decoded from a custom cipher.

Use `blackporntube` as the module/folder name (source alias `blackporntube`).

### Host aliases

- `blackporn.tube`
- `www.blackporn.tube`
- `bptn.m3pd.com` (thumbnail CDN, allowlisted for proxying)
- `ahcdn.blackporn.tube` (final redirect target of `/get_file/` links)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return h == "blackporn.tube" or h.endswith(".blackporn.tube")
```

### JSON API surface (discovered from `app.js`)

- **Listings:** `GET /api/json/videos2/86400/{gender}/{sort}/{count}/{section}.{object_id}.{page}.{type}.{duration}.{date}.json`
  - `gender` = `str`; `type`/`duration`/`date` = `all`; section empty string for plain listings.
  - `sort`: `latest-updates`, `longest`, `most-commented`, `most-popular`, `top-rated`.
  - `section`/`object_id` pairs: `categories.{category_dir}` (category pages), `model.{model_dir}` (`/pornstar/{dir}/`), `channel.{dir}` (`/pornsite/{dir}/`); `search` handled separately.
  - Section values like `all`/`video` return `invalid_params_section` -- the empty section is the plain listing.
- **Search:** `GET /api/videos2.php?params=86400/str/relevance/{count}/search.0.{page}.all.all.all&s={query}`
- **Video detail:** `GET /api/json/video/86400/{id//1000000}/{id//1000}/{id}.json`
  - Bucket path segments are required (nginx 301-redirects the unbucketed form); follow redirects or build the bucketed URL directly.
  - Response `video` object has `title`, `dir`, `duration` (`7:47` style), `post_date`, `statistics.viewed`, `user.username`, `thumb`/`thumbsrc`, plus `categories`/`tags`/`models` dicts keyed by numeric id.
- **Streams:** `GET /api/videofile.php?video_id={id}&lifetime=8640000`
  - Returns a list like `[{"format": "_sd.mp4", "is_default": 1, "video_url": "<encoded>"}]`.
  - `video_url` is encoded with a custom base64 whose alphabet is `АВСDЕFGHIJKLМNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,~`
    (the lookalikes `А` `В` `С` `Е` `М` are **Cyrillic** U+0410/U+0412/U+0421/U+0415/U+041C), with `~` (index 64) acting as `=` padding.
  - Decoded value is a KVS-style path: `/get_file/1/{hash}/{bucket_m}/{bucket_k}/{id}_sd.mp4/?d=..&br=..&ti=..` on the site origin.
  - A GET on that path 302-redirects to the signed CDN URL (`https://ahcdn.blackporn.tube/key=.../c1/videos/...`). Resolution happens per request, so returned links are always fresh; tokens expire quickly.
- **Categories:** `GET /api/json/categories/14400/str.all.en.json` -- array of `{category_id, title, dir, total_videos}` (`total_videos` is a string).

### Listing and pagination (`list_videos`)

- Page 1 uses `base_url` unchanged; page *n* is passed as the `{page}` path segment of the API call (no `?page=` URLs).
- Recognized `base_url` shapes:
  - `https://blackporn.tube/`, `/latest-updates/` -> sort `latest-updates`
  - `/most-popular/`, `/top-rated/`, `/most-commented/`, `/longest/`
  - `/categories/{slug}/` -> section `categories`
  - `/pornstar/{dir}/` -> section `model`
  - `/pornsite/{dir}/` -> section `channel`
  - `/search/1/?s={query}` (or `/search/{page}/?s=`) -> search endpoint
- Items come pre-normalized from the API: `video_id`+`dir` build the canonical `/video/{id}/{dir}/` URL, `scr`/`thumb` is the thumbnail, `duration` is normalized to `mm:ss`/`hh:mm:ss`, views from `video_viewed`, uploader from `display_name`/`username`.
- `count` (limit) is capped at 200 by the API; invalid sorts silently fall back to `latest-updates`.

### Metadata and streams (`scrape`)

- Input must be a canonical `/video/{numeric_id}/{slug}/` URL (non-matching paths raise `ValueError` so the dispatcher can 502 cleanly).
- Metadata is fetched from the bucketed `/api/json/video/...` endpoint; tags flatten `categories` + `tags` + `models` titles (deduped).
- Streams call `/api/videofile.php`, decode each `video_url`, and only keep paths starting with `/get_file/`. `format` is `mp4`; `quality` derives from the `_xx` suffix of the `format` field (`sd`, `hd`, `720p`, ...; default `source`).
- If no direct stream resolves (e.g. members-only), fall back to the native embed `https://blackporn.tube/embed/{id}` so `has_video` stays truthful.
- `video.default` = first resolved stream (API already returns the best tier first with `is_default: 1`).
- `GET /api/v1/videos/stream` returns flat `sd` / `sd_format` fields (host checks added for `blackporn.tube` / `bptn.m3pd.com` / `ahcdn.blackporn.tube`).

### Categories (`get_categories`)

`categories.json` seeds sort tabs (Latest Updates, Most Popular, Top Rated, Most Commented, Longest) plus top categories (Ebony, HD, Big Cock, Big Ass, Big Tits, Amateur, Interracial, Deepthroat, MILF, ...) with `/categories/{dir}/` URLs; `/api/v1/categories?source=blackporntube` serves them.

### Registration checklist for BlackPornTube

Besides creating `backend/app/scrapers/blackporntube/`, update all of these:

- `backend/app/scrapers/__init__.py`
  - import + `__all__` entry (`blackporntube`)
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch` branch
  - `_list_dispatch` branch
  - `/api/v1/categories` source mapping (`source=blackporntube`, `blackporn.tube`, `blackpornt`)
- `backend/app/services/video_streaming.py`
  - import list + `elif blackporntube.can_handle(host):` branch
  - unsupported-host help text (`blackporn.tube`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`blackporn.tube`, `bptn.m3pd.com`, `ahcdn.blackporn.tube`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`blackporn.tube`, `bptn.m3pd.com`, `ahcdn.blackporn.tube`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="blackporntube"`, `baseUrl="https://blackporn.tube/"`, `searchUrlTemplate="https://blackporn.tube/search/1/?s={query}"`, `pageSize=48`)

### BlackPornTube verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://blackporn.tube/video/10474939/surprising-bikini-grind-fuck-awake-kenzie-green-dr-grey-and-brandi-foxx/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blackporn.tube/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blackporn.tube/categories/ebony/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=blackporntube"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://blackporn.tube/video/10474939/surprising-bikini-grind-fuck-awake-kenzie-green-dr-grey-and-brandi-foxx/"
```

Notes from live testing (2026-09):

- Listing (home/sorts/categories/search, pages 1+2), `scrape()` metadata, base164 stream decode, and the `/get_file/` 302 to `ahcdn.blackporn.tube` were all verified end-to-end via the FastAPI TestClient.
- The API is generous to plain `User-Agent` + `Referer: https://blackporn.tube/` requests (no Cloudflare challenge observed); nothing else is required.
- Detail API requires bucketed id paths (`{id//1000000}/{id//1000}`) and the videofile `lifetime` must be the numeric `8640000` (the JS uses `864e4`).
- ~65k videos and 175 categories were exposed by the API at test time; `total_count` and `pages` come back on every listing.

## SxyLand Implementation Notes

[SxyLand](https://sxyland.com/) is a WordPress **retrotube**-theme tube site. Canonical video pages use root-level slugs (e.g. `https://sxyland.com/tiny4k-kimmy-kimm-10-positions-in-10-mins/`). Video posts embed a third-party player via `<iframe>` inside `.responsive-player` (player host `nowplay.to`); direct `.mp4`/`.m3u8` URLs are rarely present in the page HTML.

### Host aliases

- `sxyland.com`
- `www.sxyland.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("sxyland.com", "www.sxyland.com") or h.endswith(".sxyland.com")
```

### Listing and pagination (`list_videos`)

- Listing pages use `.thumb-block` article cards (`article.thumb-block`, also `.thumb-block.video-preview-item`); the title lives in `header.entry-header` inside the card anchor, duration in `span.duration`, views in `span.views` (kept verbatim as the site renders them).
- Keep only same-domain post URLs matching `/{slug}/` (single segment) and skip utility paths: `/categories/`, `/tags/`, `/actors/`, `/category/`, `/tag/`, `/actor/`, `/author/`, `/page/`, `/wp-content/`, `/wp-json/`.
- Page 1 should use `base_url` unchanged.
- For page > 1, WordPress path pagination is used: `https://sxyland.com/page/2/` (also under category paths, e.g. `/categories/...` pages are index pages; real archives are `/category/{slug}/`).
- Sort/filter tabs are query params on the home URL and combine with path pagination: `?filter=latest`, `?filter=most-viewed`, `?filter=longest`, `?filter=popular`, `?filter=random` (existing query params are preserved when appending `/page/{n}/`).
- Search: `https://sxyland.com/?s={query}` (WordPress query search).

Useful list base URLs:

- `https://sxyland.com/`
- `https://sxyland.com/?filter=most-viewed`
- `https://sxyland.com/category/amateur/`
- `https://sxyland.com/tag/brazzers/`
- `https://sxyland.com/actor/{name}/`
- `https://sxyland.com/?s=<query>`

### Metadata and streams (`scrape`)

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. `itemprop="duration"` (ISO 8601, e.g. `P0DT1H18M37S` — convert to `H:MM:SS`/`MM:SS`), `itemprop="thumbnailUrl"`, `itemprop="uploadDate"`
  4. visible `h1.entry-title` / page `<title>`
- Views: `#video-views span` (numeric). Uploader: `#video-author a`. Tags/actors: category/tag anchors inside `article .tags-list`, actor links in `#video-actors a` (do **not** scrape the sitewide studio tag cloud at the top of the page — restrict tag collection to the article block).
- Streams: the player is an `<iframe>` inside `.responsive-player` (falls back to `.video-player`), typically `https://nowplay.to/{id}`. Collect iframe embeds (filter ad iframes: `acscdn.com`, `spitefulmom.com`, `googlesyndication`, ...) and expose each as `format="embed"` with `quality="Server 1"`, `"Server 2"`, ...
- Fallback order for streams:
  1. iframe embeds in `.responsive-player` / `.video-player`
  2. inline script `.mp4` / `.m3u8` URLs (unescape `\\/` -> `/`, `\\u0026` -> `&`; skip `/wp-content/` assets)
  3. `meta[itemprop="embedURL"]`
- Set `video.default` to the first embed URL and `video.has_video=True`.

### Categories (`get_categories`)

`categories.json` is seeded from the public nav and the Categories index (`/categories/`, paginated at `/categories/page/2/`): sort/filter views (Latest, Most Viewed, Longest, Top Rated, Random), the category archive slugs (`/category/{slug}/`), and the popular studio tags from the header (`/tag/brazzers/`, `/tag/blackeed/`, etc.). Schema matches the other scraper folders so `/api/v1/categories?source=sxyland` returns valid `CategoryItem` entries.

### Registration checklist for SxyLand

Besides creating `backend/app/scrapers/sxyland/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=sxyland` or `source=sxyland.com`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif sxyland.can_handle(host)`)
  - unsupported-host help text (`sxyland.com`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`sxyland.com`, plus player host `nowplay.to`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`sxyland.com`)
  - list base URL allowlist (same host)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="sxyland"`, `baseUrl="https://sxyland.com/"`, `searchUrlTemplate="https://sxyland.com/?s={query}"`, `accentColor="#FFA500"`)

### SxyLand verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://sxyland.com/tiny4k-kimmy-kimm-10-positions-in-10-mins/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://sxyland.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://sxyland.com/?filter=most-viewed&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=sxyland"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://sxyland.com/tiny4k-kimmy-kimm-10-positions-in-10-mins/"
```

Notes from live testing (2026-09):

- Home listing, filtered listing (`?filter=most-viewed`), page-2 path pagination, and category slugs were verified against the live site.
- `scrape()` returns `nowplay.to` embed as `Server 1` with `has_video=True`; duration parses from the ISO `itemprop="duration"` value; views/uploader/tags/related videos all populate.
- The site is plain WordPress/retrotube HTML with no Cloudflare challenge for the pooled `aiohttp` fetcher (desktop `User-Agent` + `Referer` headers are sufficient).

## CamCaps Implementation Notes

[CamCaps](https://camcaps.tv/) is an AVS-script-style tube site. Canonical video pages use `/video/{numeric_id}/{slug}` (no trailing slash). Video posts embed a third-party player via `<iframe>` inside `.video-embedded` (player host `nowplay.to` â€” same player family as SxyLand); direct `.mp4`/`.m3u8` URLs are not present in the page HTML, so streams are embed-only.

### Host aliases

- `camcaps.tv`
- `www.camacaps.tv`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("camcaps.tv", "www.camacaps.tv") or h.endswith(".camcaps.tv")
```

### Listing and pagination (`list_videos`)

- Listing pages use `article.thumb` cards (inner `.inner`); title in `h3` inside the card anchor, thumbnail in `img[src]`, duration in `span.dur-icon`, views in `span.views-icon`.
- Keep only same-domain URLs matching `/video/{id}/{slug}` and skip nav/search/user links.
- Thumbnails mix absolute and relative paths and use several bucket formats (`/media/videos/tmb11/{id}/1.jpg`, older `/tmb1/`, `/tmb/`); resolve relative to the site root and use `src` as-is.
- Page 1 should use `base_url` unchanged.
- For page > 1, append/replace the `page` query param: `https://camcaps.tv/videos?page=2` (works for home, section, and search URLs alike).
- Sort tabs are query params on `/videos`: `?type=featured`, `?o=tr` (Top Rated), `?o=mv`, `?o=lg`; preserve them when adding `page`.
- Search: `https://camcaps.tv/search/videos/{query}` (also POST form to `/search/videos`).

Useful list base URLs:

- `https://camcaps.tv/`
- `https://camcaps.tv/videos`
- `https://camcaps.tv/videos?o=tr`
- `https://camcaps.tv/search/videos/onlyfans`
- `https://camcaps.tv/videos/0dayporn`

### Metadata and streams (`scrape`)

- Metadata fallback order:
  1. visible `h1` (og: tags are absent; `meta[name=description]` mirrors the title)
  2. `article.about p` for the description
  3. views from `.info span.views-icon` kept exactly as the site renders it (e.g. `12.3K views`) — no K/M expansion, the raw string is returned as-is
  4. uploader from `.video-links .group a[href*="/user/"]`
- Tags: anchors under `.video-links` pointing at `/search/videos/{tag}` (skip `/user/` links). The duration is NOT shown on the detail page (only on cards), so `scrape()` returns `duration: None`.
- Streams: the player is a single `<iframe>` inside `.video-embedded` (falls back to `.player`), typically `https://nowplay.to/emb{...}`. Expose it as `format="embed"` with `quality="Server 1"`, set `video.default` to it and `video.has_video=True`.
- Fallback order for streams:
  1. iframe embeds in `.video-embedded` / `.player`
  2. inline script `.mp4` / `.m3u8` URLs (skip `/media/videos/tmb` thumbnail assets)
- Filter ad iframes: `xhadapt.php`, `magsrv.com`, `nappyonsetstiffness.com`, `acscdn.com`, `googlesyndication`.
- The site ships obfuscated anti-devtools JS (debugger traps, key blocking) â€” irrelevant for server-side scraping.

### Categories (`get_categories`)

`categories.json` is seeded from the nav and homepage: sort views (New, Featured, Top Rated, Most Viewed, Longest), the studio/network search tags from the header (OnlyFans, ManyVids, Fansly, LoyalFans, Chaturbate, StripChat, MFC, BongaCams, Clips4Sale, ...), the `0dayporn` HD section, and generic tag searches. Schema matches the other scraper folders so `/api/v1/categories?source=camcaps` returns valid `CategoryItem` entries.

### Registration checklist for CamCaps

Besides creating `backend/app/scrapers/camcaps/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=camcaps` or `source=camcapstv`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif camcaps.can_handle(host)`)
  - unsupported-host help text (`camcaps.tv`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`camcaps.tv`; player host `nowplay.to` is already covered by SxyLand)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`camcaps.tv`)
  - list base URL allowlist (same host)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="camcaps"`, `baseUrl="https://camcaps.tv/"`, `searchUrlTemplate="https://camcaps.tv/search/videos/{query}"`, `accentColor="#7B1FA2"`)

### CamCaps verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://camcaps.tv/video/362276/kimmy-kimm-10-positions-in-10-mins-tiny4k\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://camcaps.tv/videos&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://camcaps.tv/search/videos/onlyfans&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=camcaps"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://camcaps.tv/video/362276/kimmy-kimm-10-positions-in-10-mins-tiny4k"
```

Notes from live testing (2026-09):

- Listing page 1 and page 2 (`?page=2`, sort `?o=tr` preserved), `scrape()` metadata (title/views/uploader/tags/description), the `nowplay.to` embed (`Server 1`, `has_video=True`), related videos (18 items), and `ListItem`/`ScrapeResponse` schema validation were all verified against the live site.
- Views are returned verbatim in the site's original format (`12.3K views`) — no K/M expansion.
- Plain `User-Agent` + `Referer: https://camcaps.tv/` requests are sufficient (no Cloudflare challenge); thumbnails may be absolute or relative and use several bucket folders, handled automatically.

## KoreanPornMovie Implementation Notes

[KoreanPornMovie](https://koreanpornmovie.com/) is a WordPress **retrotube**-theme tube site (same family as SxyLand) for Korean adult cinema. Canonical video pages use root-level slugs (e.g. `https://koreanpornmovie.com/married-couple-sex-sexual-chemistry-is-the-best-2026/`). Unlike SxyLand, it exposes **direct MP4** URLs via `meta[itemprop="contentUrl"]` on a CDN host (`koreanporn.stream`) plus a base64-encoded **clean-tube-player** iframe (`player-x.php?q=...`).

### Host aliases

- `koreanpornmovie.com`
- `www.koreanpornmovie.com`
- `koreanporn.stream` (MP4 CDN, allowlisted in `video_streaming.py`/`schemas.py` for passthrough)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("koreanpornmovie.com", "www.koreanpornmovie.com") or h.endswith(".koreanpornmovie.com")
```

### Listing and pagination (`list_videos`)

- Listing pages use `article.thumb-block` cards (`article.thumb-block`, also `.thumb-block.video-preview-item`); title in `header.entry-header` inside the card anchor, duration in `span.duration`.
- Keep only same-domain post URLs matching `/{slug}/` (single segment) and skip utility paths: `/tags/`, `/actors/`, `/category/`, `/tag/`, `/actor/`, `/author/`, `/page/`, `/wp-content/`, `/wp-json/`, legal pages (`/dmca/`, `/privacy-policy/`, `/2557-statement/`, `/contact-us/`, `/our-partner/`).
- Page 1 should use `base_url` unchanged.
- For page > 1, WordPress path pagination is used: `https://koreanpornmovie.com/page/2/` (56 pages at test time; also under category paths like `/category/korean/page/2/`).
- Sort/filter tabs are query params on the home URL and combine with path pagination: `?filter=latest`, `?filter=popular` (existing query params are preserved when appending `/page/{n}/`).
- Search: `https://koreanpornmovie.com/?s={query}` (WordPress query search, exposed by Yoast `SearchAction`).

Useful list base URLs:

- `https://koreanpornmovie.com/`
- `https://koreanpornmovie.com/?filter=latest`
- `https://koreanpornmovie.com/category/korean/`
- `https://koreanpornmovie.com/tag/{slug}/`
- `https://koreanpornmovie.com/actor/{name}/`
- `https://koreanpornmovie.com/?s=<query>`

### Metadata and streams (`scrape`)

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image` (Yoast SEO present)
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. `itemprop="duration"` (ISO 8601, e.g. `P0DT1H0M36S` â€” convert to `H:MM:SS`), `itemprop="thumbnailUrl"` (640x360 variant), `itemprop="uploadDate"`
  4. visible `h1.entry-title` / page `<title>`
- Uploader: `#video-author a` (`KPORN`). Tags/actors: category/tag anchors inside `article .tags-list`, actor links in `#video-actors a` (restrict to the article block; the nav has no tag cloud but future-proofing applies).
- Views are not shown on detail pages â€” `scrape()` returns `views: None`.
- **Stream extraction order** (this site gives direct MP4, prefer it over embeds):
  1. `meta[itemprop="contentUrl"]` ending in `.mp4` (direct `koreanporn.stream/{Title}.mp4`, percent-encoded) â€” expose as `format="mp4"`, `quality="source"`, and set `video.default` to it
  2. **clean-tube-player decode**: the iframe `src` is `.../plugins/clean-tube-player/public/player-x.php?q={base64}`; decode `q` (base64 â†’ percent-decode â†’ HTML) and read `<video><source src>` from the payload â€” same MP4/HLS URLs
  3. inline script `.mp4` / `.m3u8` scan (unescape `\\/` -> `/`, `\\u0026` -> `&`; skip `/wp-content/`, `_preview`, `trailer` assets)
  4. the `player-x.php` iframe URL itself as `format="embed"` fallback
- Set `video.has_video=True` when any stream exists.

### Categories (`get_categories`)

`categories.json` is seeded from the public nav and homepage: Home, Latest (`?filter=latest`), Popular (`?filter=popular`), the `Korean` category archive (`/category/korean/`), the Tags index (`/tags/`), and the Actors index (`/actors/`). Schema matches the other scraper folders so `/api/v1/categories?source=koreanpornmovie` returns valid `CategoryItem` entries.

### Registration checklist for KoreanPornMovie

Besides creating `backend/app/scrapers/koreanpornmovie/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=koreanpornmovie`, `source=koreanporn`, or `source=kpm`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif koreanpornmovie.can_handle(host)`)
  - unsupported-host help text (`koreanpornmovie.com`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`koreanpornmovie.com`, `koreanporn.stream`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`koreanpornmovie.com`, `koreanporn.stream`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="koreanpornmovie"`, `baseUrl="https://koreanpornmovie.com/"`, `searchUrlTemplate="https://koreanpornmovie.com/?s={query}"`, `accentColor="#DB0159"`)

### KoreanPornMovie verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://koreanpornmovie.com/married-couple-sex-sexual-chemistry-is-the-best-2026/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://koreanpornmovie.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://koreanpornmovie.com/category/korean/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=koreanpornmovie"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://koreanpornmovie.com/married-couple-sex-sexual-chemistry-is-the-best-2026/"
```

Notes from live testing (2026-09):

- Home listing, page-2 path pagination (`/page/2/` â€” note: cached page returned page-1 items at test time, treat as best-effort), `scrape()` metadata (title, ISO duration -> `1:00:36`, uploader `KPORN`, `uploadDate`, tags+actors, 21 related), and the direct `koreanporn.stream` MP4 (`has_video=True`, `quality="source"`) were all verified.
- The `player-x.php?q=` base64 payload decodes to a `<video><source src="https://koreanporn.stream/....mp4">` matching `itemprop="contentUrl"`; both paths yield the same playable URL.
- The site ships an obfuscated `eval(atob(...))` script (ad anti-analysis) â€” irrelevant for server-side scraping; plain `User-Agent` + `Referer` requests are sufficient (no Cloudflare challenge).

## FullPorner Implementation Notes

[FullPorner](https://fullporner.com/) is a Bootstrap/osahan-style tube site (no WordPress). Canonical video pages use `/watch/{hex_id}` (no slug, no trailing slash). The site embeds a FluidPlayer iframe on `xiaoshenke.net`. **Streams are embed-only** — the player encodes direct MP4 URLs at `xiaoshenke.net/vid/{reversed_id}/{quality}`, but those do **not** play outside the player (tested 2026-09), so the scraper returns only the player iframe.

### Host aliases

- `fullporner.com`
- `www.fullporner.com`
- `xiaoshenke.net` (player + CDN: `imgs.xiaoshenke.net` thumbs, `static.xiaoshenke.net` assets — allowlisted in `video_streaming.py`/`schemas.py` for passthrough)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("fullporner.com", "www.fullporner.com") or h.endswith(".fullporner.com")
```

### Streams (`scrape`) — embed only

- The watch page embeds `<iframe src="//xiaoshenke.net/video/{id}/{mask}">` inside `.single-video` (e.g. `https://xiaoshenke.net/video/10c8d2bff1096/4`).
- Return exactly one stream: `format="embed"`, `quality="Server 1"`, `url` = the iframe URL normalized to `https://...` (upgrade the `//` protocol-relative form).
- `video.default` = the embed URL, `video.has_video=True`.
- Do **not** reconstruct `xiaoshenke.net/vid/{reversed_id}/{quality}` MP4s — they fail to play outside the site's player context.
- Thumbnail: the watch page has no og:image; reconstruct the listing-style thumb `https://imgs.xiaoshenke.net/thumb/{reversed_id}.jpg` (the listing thumb filename is the reversed iframe id, e.g. iframe id `756edac0ba9a6` → thumb `6a9ab0cade657.jpg`).

### Listing and pagination (`list_videos`)

- Listing pages use `.video-card` blocks: link `/watch/{id}` in `.video-card-image a` and `.video-title a`, thumbnail `img[data-src]` (lazy, `imgs.xiaoshenke.net/thumb/...`), duration in `.video-card-image .time`, upload Unix timestamp in `.video-view span.create` (convert to ISO-8601 UTC).
- Page 1 should use `base_url` unchanged.
- Pagination is path-segment based:
  - home: `https://fullporner.com/home/{n}`
  - categories/pornstars: `https://fullporner.com/category/{slug}/{n}` (replace an existing trailing numeric segment)
  - search: `https://fullporner.com/search?q={query}&page={n}` (query param)
- Search: `https://fullporner.com/search?q={query}`.

Useful list base URLs:

- `https://fullporner.com/`
- `https://fullporner.com/category/{slug}` (24 popular ones seeded in `categories.json`; 85 exist under `/category`)
- `https://fullporner.com/pornstar/{slug}`
- `https://fullporner.com/search?q=<query>`

### Metadata (`scrape`)

- Title: `.single-video-title h2` (page `<title>` has a `- fullporner.com | FullPorner.com` suffix to strip; `meta[name=description]` mirrors the title)
- Duration: `.video-info` div matching `mm:ss`/`hh:mm:ss` regex
- Upload date: `.video-info span.create` Unix timestamp -> ISO-8601 UTC
- Tags: `.tag-link a[href*="/category/"]` (strip leading `#`)
- Pornstar (uploader): `.single-video-info-content a.fullname`
- Thumbnail: `https://imgs.xiaoshenke.net/thumb/{reversed_id}.jpg` (reversed iframe id; watch page has no og:image)
- Views: not exposed — `scrape()` returns `views: None`
- Related: `.video-card` grid on the watch page (24 items)

### Categories (`get_categories`)

`categories.json` is seeded from the sidebar category chips (Home + 23 popular categories such as `anal`, `big-ass`, `milf`, `creampie`, `hardcore`, `pov`, ...). Schema matches the other scraper folders so `/api/v1/categories?source=fullporner` returns valid `CategoryItem` entries.

### Registration checklist for FullPorner

Besides creating `backend/app/scrapers/fullporner/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=fullporner`, `source=fullporner.com`, or `source=fp`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif fullporner.can_handle(host)`)
  - unsupported-host help text (`fullporner.com`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`fullporner.com`, `xiaoshenke.net`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`fullporner.com`, `xiaoshenke.net`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="fullporner"`, `baseUrl="https://fullporner.com/"`, `searchUrlTemplate="https://fullporner.com/search?q={query}"`, `accentColor="#E91E63"`)

### FullPorner verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://fullporner.com/watch/6a9ab659d365af67ede04ef3\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://fullporner.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://fullporner.com/category/anal&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=fullporner"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://fullporner.com/watch/6a9ab659d365af67ede04ef3"
```

Notes from live testing (2026-09):

- Home listing (page 1), category listing (page 2, `/category/anal/2`), and `scrape()` metadata (title, duration `01:41:24`, pornstar `jasmine spice`, ISO upload date, 15 tags, 24 related) were all verified.
- `scrape()` returns exactly one embed stream `https://xiaoshenke.net/video/{id}/{mask}` (`Server 1`, `has_video=True`); the deterministic `/vid/` MP4 construction was removed because those URLs do not play outside the player.
- The watch-page thumbnail is reconstructed from the listing thumb host: `https://imgs.xiaoshenke.net/thumb/{reversed_id}.jpg`.
- The site sits behind Cloudflare but serves plain HTML to the pooled `aiohttp` fetcher with a desktop `User-Agent` + `Referer` (no challenge at test time).


## SuperPorn Implementation Notes

[SuperPorn](https://www.superporn.com/) is a Laravel/TechPump tube site (part of the servitubes network). Canonical video pages use `/video/{slug}`. The video-js player carries a direct MP4 `<source>` on `cdnst.superporn.com`, but its `?secure=` token is request-bound and short-lived, so the scraper returns the **stable same-host embed** (`https://www.superporn.com/embed/{videoId}`) instead — same approach chosen for FullPorner after their direct URLs proved unplayable.

### Host aliases

- `superporn.com`, `www.superporn.com`
- `img.superporn.com` (thumbs/previews), `cdnst.superporn.com` (direct MP4 CDN) — allowlisted in `video_streaming.py`/`schemas.py` for passthrough
- API endpoints exist at `api.superporn.com` (`/video/{id}/related`, `/videos/home`, `/search-ajax`) but are not required; everything is server-rendered

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("superporn.com", "www.superporn.com") or h.endswith(".superporn.com")
```

### Streams (`scrape`) — embed preferred

- The stable embed is `https://www.superporn.com/embed/{videoId}` (from the `#code-embed` share input, the `.votos-thumbs[data-video-id]` attribute, or `video[data-stats-video-id]`; also exposed in JSON-LD as `embedUrl`).
- Return one stream: `format="embed"`, `quality="Server 1"`, `video.default` = embed URL, `video.has_video=True`.
- The player's direct source (`https://cdnst.superporn.com/videos/{folder}/{id}/mp4/{hash}.mp4?secure={token}`) is only a last-resort fallback (embed resolution failure) — the token expires and is bound to the requesting client, so it does not survive handoff.
- Thumbnail: `og:image` = `img.superporn.com/videos/{folder}/{id}/previews/...jpg` (present on watch pages).

### Listing and pagination (`list_videos`)

- Listing pages use `.thumb-video` cards: post link `a.thumb-duracion[href*="/video/"]`, title `h3 a.thumb-video__description`, thumbnail `img[data-src]` (`img{,5,7}.superporn.com/videos/{folder}/{id}/thumbs/...`), duration `span.duracion`, views `.thumb-video-views` returned verbatim as the site renders it (`218.8k`, `7.8k`; no conversion), uploader `a.info-uploader` (series name or `/user/{name}`).
- Some listing cards may link to localized variants (`/es/video/{slug-es}`) — normalize to the canonical `/video/{slug}` only when the slug matches; better to keep the URL as returned after host normalization (both play identically).
- Page 1 should use `base_url` unchanged.
- Pagination:
  - home and search: query param — `https://www.superporn.com/?page=2`, `/search?q={query}&page={n}`
  - category/pornstar/series sections: numeric path segment — `/anal/2`, `/pornstar/{slug}/2`, `/series/{slug}/2`
  - order filters combine with both: `/anal?order=trending`, `/anal?order=popular` (preserve existing query params when adding the page segment)
- Sort tabs on sections: `?order=trending`, `?order=popular`.
- Search: `https://www.superporn.com/search?q={query}`.

Useful list base URLs:

- `https://www.superporn.com/`
- `https://www.superporn.com/{category-slug}` (35 categories in the nav dropdown, e.g. `/anal`, `/milf`, `/lesbian`; full list at `/categories`)
- `https://www.superporn.com/series/{slug}`
- `https://www.superporn.com/pornstar/{slug}`
- `https://www.superporn.com/search?q=<query>`

### Metadata (`scrape`)

- Title: `og:title` (also `h1` in `.data-video__title`; `<title>` has a ` - SuperPorn` suffix to strip)
- Description: `og:description` (strip the trailing ` - SuperPorn`)
- Thumbnail: `og:image` / `twitter:image` (previews URL)
- Duration: `video[data-video-duration]` in **seconds** (e.g. `628` → `10:28`)
- Views: `#n-views` kept exactly as the site renders it (e.g. `218.8k`) — no K/M expansion, the raw abbreviated string is returned as-is
- Uploader: `.view-more-less a.info-uploader[href*="/user/"]` (e.g. `antoine98`); series name appears separately
- Tags/categories: `ul.catlist a.chip-link` (relative hrefs like `/public`)
- Upload date: only relative text (`· 9 hours ago ·` in `.subido`) — `scrape()` returns `upload_date: None`
- Related: same `.thumb-video` cards inside `.wrapper--related-videos` (8 per page; more via AJAX `api.superporn.com/video/{id}/related` — not needed)

### Categories (`get_categories`)

`categories.json` is seeded from the nav dropdown: Home + 24 popular categories (`lesbian`, `ebony`, `big-ass`, `hentai`, `milf`, `latina`, `japanese`, `anal`, `threesome`, `creampie`, `teen`, `big-tits`, `interracial`, ...). Schema matches the other scraper folders so `/api/v1/categories?source=superporn` returns valid `CategoryItem` entries.

### Registration checklist for SuperPorn

Besides creating `backend/app/scrapers/superporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=superporn` or `source=superporn.com`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif superporn.can_handle(host)`)
  - unsupported-host help text (`superporn.com`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`superporn.com`, `img.superporn.com`, `cdnst.superporn.com`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`superporn.com`, `www.superporn.com`, `img.superporn.com`, `cdnst.superporn.com`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="superporn"`, `baseUrl="https://www.superporn.com/"`, `searchUrlTemplate="https://www.superporn.com/search?q={query}"`, `accentColor="#05FF00"`)

### SuperPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.superporn.com/video/engulfing-her-friend-s-huge-cock\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.superporn.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.superporn.com/anal&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=superporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.superporn.com/video/engulfing-her-friend-s-huge-cock"
```

Notes from live testing (2026-09):

- Home listing (page 1), category listing (page 2 via `/anal/2`), `scrape()` metadata (title, duration `10:28` from seconds, views `218.8k` (original site format), uploader `antoine98`, category chips, 4 related) and the embed stream (`https://www.superporn.com/embed/2724`, `has_video=True`) were all verified.
- Sort variants (`/anal?order=popular`) keep their query when paginating (`/anal/2?order=popular`).
- The site is behind Cloudflare but serves plain HTML to the pooled `aiohttp` fetcher with a desktop `User-Agent` + `Referer` (no challenge at test time).


## Siska Implementation Notes

[Siska](https://siska.tv/) is a plain-PHP tube site (Pure CSS grid, no CMS). Canonical video pages use query-string URLs: `https://siska.tv/video.php?videoID={numeric_id}`. Each video embeds **2-3 third-party player iframes** inside `.playerplace .videoholder` (tested hosts: `playmogo.com`, `luluvid.com`, `playmate.to`) — streams are embed-only. **The site's TLS certificate has expired**, so its `fetch_page` passes a verification-disabled `ssl` context to the pooled fetcher.

### Host aliases

- `siska.tv`, `www.siska.tv`
- `siska.video` (thumbnail CDN, not a page host)
- Player hosts allowlisted for passthrough: `playmogo.com`, `luluvid.com`, `playmate.to`

Example:

```python
import ssl

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {...}
    # The site's TLS certificate has expired; fetch with verification disabled.
    return await pool_fetch_html(url, headers=headers, ssl=_SSL_CTX)

def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("siska.tv", "www.siska.tv") or h.endswith(".siska.tv")
```

### Streams (`scrape`) — embed-only

- Collect all `iframe[src]` inside `.playerplace` / `.videoholder`, upgrade `//` to `https://`, filter ad iframes (`xads`, `pemsrv`, `tsyndicate`, `magsrv`, ...).
- Return **one stream per iframe — all servers** (`Playmogo`, `Luluvid`, `Playmate`; `Server {n}` fallback), `format="embed"`. The site's default (first) player frequently errors, so every server must be listed so the client can switch.
- `video.default` = the first iframe URL, `video.has_video=True` when any exist.
- There are no direct MP4/HLS URLs anywhere on the page (no `og:video`, no inline sources).

### Listing and pagination (`list_videos`)

- Listing cards: `div.thumb .rel` containers; post link `a.video-thumb[href*="videoID="]`, title from the anchor's `title` attribute (fallback: `img[alt]`, then the sibling `h3.title_desc`), thumbnail `img[data-src]` (lazy, `siska.video/category/{Cat}/{id}.jpg`; keep `data-src` as-is, ignore the `onError` fallback), duration `span.th_video_duration` in the site's spaced `34 : 12` format.
- There are no view counts anywhere — `views: None` in list items and `scrape()`.
- Page 1 should use `base_url` unchanged.
- Pagination: `?page=N` query param on any list URL — `https://siska.tv/best_xvideos.php?page=2`, `/search.php?s={query}&page=2`, `/chanells.php?site={studio}&page=2` (12,626 pages on the main listing at test time).
- Search: `https://siska.tv/search.php?s={query}` (also the actress-link URL format `search.php?s={Actress+Name}` — actress names double as tags).

Useful list base URLs:

- `https://siska.tv/` (home has two card sections; parser handles both)
- `https://siska.tv/best_xvideos.php` (all videos)
- `https://siska.tv/category.php?c={Category}` (e.g. `Anal`, `MILF`, `Home-made-video`)
- `https://siska.tv/chanells.php?site={studio}` (e.g. `Vixen.com`)
- `https://siska.tv/search.php?s=<query>`

### Metadata (`scrape`)

- Title: `og:title` (absent on some pages) → `h1[itemprop="name"]` (strip the trailing ` | siska.tv` / `» WATCH FREE VIDEO HD` suffixes)
- Duration: `meta[itemprop="duration"]` in the spaced format; also visible in `.video-info p` (`Duration:`). The minutes segment **can exceed 59** (`134 : 22` = 2:14:22) — normalize by computing total seconds, not by parsing `H:MM:SS` fields.
- Thumbnail: `meta[itemprop="thumbnailUrl"]` (`siska.video/category/...jpg`)
- Upload date: `meta[itemprop="datePublished"]` (`2026-09-04 21:37:01+00:00`, returned verbatim)
- Tags/actresses: `.video-info a[href*="search.php?s="]` and `a[rel="tag"]` (actress names + studio + category)
- Uploader: the studio link `.video-info a[href*="chanells.php"]` (e.g. `Vixen.com`)
- Description: `.video-description h3` text
- Related: same card structure inside `.yarpp-related` (9 items; more via AJAX `prim.php?ajax=true&page={n}&videoID={id}` — not needed)

### Categories (`get_categories`)

`categories.json` is seeded from the category index (`/category.php`): Home, All Videos, and 19 popular category slugs (`Anal`, `Asian`, `Beautiful-tits`, `Big-Cock`, `Gangbang`, `GroupSex`, `Home-made-video`, `MILF`, `Teens`, `Toys`, `Wife`, ...). Schema matches the other scraper folders so `/api/v1/categories?source=siska` returns valid `CategoryItem` entries.

### Registration checklist for Siska

Besides creating `backend/app/scrapers/siska/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=siska`, `source=siskatv`, or `source=siska.tv`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif siska.can_handle(host)`)
  - unsupported-host help text (`siska.tv`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`siska.tv`, plus player hosts `playmogo.com`, `luluvid.com`, `playmate.to`)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`siska.tv`, `www.siska.tv`, `playmogo.com`, `luluvid.com`, `playmate.to`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="siska"`, `baseUrl="https://siska.tv/"`, `searchUrlTemplate="https://siska.tv/search.php?s={query}"`, `accentColor="#00ADEE"`)

### Siska verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://siska.tv/video.php?videoID=232760\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://siska.tv/best_xvideos.php&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://siska.tv/category.php?c=Anal&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=siska"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://siska.tv/video.php?videoID=232760"
```

Notes from live testing (2026-09):

- The site's TLS certificate is **expired** — `aiohttp` fails with `SSLCertVerificationError` unless `ssl` verification is disabled; the scraper passes a module-level `_SSL_CTX` per request through the pool's `**kwargs` (verified: HTTP 200 without it).
- Home listing (page 1, both sections), all-videos listing (page 2 via `?page=2`), `scrape()` metadata (title, duration `34:12`, upload date `2026-09-04 21:37:01+00:00`, studio `Vixen.com`, actress tags, 8 related), and all 3 embed streams (`Playmogo`/`Luluvid`/`Playmate`, `has_video=True`) were verified.
- Spaced durations normalize correctly including the `134 : 22` → `2:14:22` over-59-minutes case.
- Plain desktop `User-Agent` + `Referer` requests are sufficient once SSL verification is off (no Cloudflare challenge at test time).


## ShyFap Implementation Notes

[ShyFap](https://www.shyfap.net/) is a **KVS (Kernel Video Sharing) tube site** using kt_player. Canonical video pages use `/video/{slug}/` (e.g. `/video/watching-you_2_v2/`; the slug has no numeric ID — the ID lives in the `ya:ovs:id` meta). Its kt_player flashvars expose direct `get_stream/{id}-{quality}.mp4` URLs, but those are **IP/license-locked and fail outside the player** (verified 2026-09), so the scraper returns the stable same-host embed `https://www.shyfap.net/embed/{video_id}` instead.

### Host aliases



- `shyfap.net`, `www.shyfap.net`
- Streams, thumbs, and embeds all live on `www.shyfap.net` itself (`/get_stream/...`, `/images/thumb/...`, `/embed/{id}`)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("shyfap.net", "www.shyfap.net") or h.endswith(".shyfap.net")
```

### Streams (`scrape`) — embed only

- The watch page's `og:video` meta carries the stable embed: `https://www.shyfap.net/embed/{video_id}` (fallback: build it from the `ya:ovs:id` meta).
- Return exactly one stream: `format="embed"`, `quality="Server 1"`, `video.default` = the embed URL, `video.has_video=True`.
- Do **not** return the flashvars `video_url`/`video_alt_url*` entries — the `get_stream/{id}-{quality}.mp4` URLs are license/IP-bound to the page request and do not play when handed to a client or fetched later (same failure mode as FullPorner's `/vid/` URLs and SuperPorn's `?secure=` token).
- The `license_code`/`lrc`/`rnd` flashvars and the `generate_mp4(...)` base64 block are not needed.

### Listing and pagination (`list_videos`)

- Listing pages use `.catalog_item` cards: post link `a.media-card[href*="/video/"]`, title `.media-card_title` (fallback `img[alt]`), thumbnail `img.lazy-load[data-original]` (the `src` is a 1x1 base64 placeholder — always read `data-original`).
- Duration/views live in `.stats_item` rows distinguished **only by their SVG icon**: `use[xlink:href="#i-clock"]` → duration (`28:41`), `#i-view` → views (raw digit string, e.g. `120699` — keep verbatim), `#i-like` → rating (`80%`, ignored). Read the icon with `.get("xlink:href")` **or** `.get("href")` — BeautifulSoup exposes both.
- Pagination is a **numeric path segment**, and the trailing `_{N}` in section URLs is the LIST ID, not the page:
  - home: `/` → `/videos_1/{page}/` (331 pages)
  - sections: `/most-watched-videos_1/` → `/most-watched-videos_1/2/` (append the page; NEVER rewrite the `_{listId}` suffix)
  - pornstar/tag/studio pages: `/pornstar/{slug}_p1/` → `/pornstar/{slug}_p1/2/`; if the URL already ends in a pure-numeric segment, replace it
  - search: `/search/?q={query}` paginates via `?page=N` (query param, not path)
- Sort tabs are separate section URLs (no query params): `/most-watched-videos_1/`, `/top-videos_1/`, `/longest-videos_1/`.
- Search: `https://www.shyfap.net/search/?q={query}` (GET form with `q`).

Useful list base URLs:

- `https://www.shyfap.net/` (new videos)
- `https://www.shyfap.net/most-watched-videos_1/`
- `https://www.shyfap.net/top-videos_1/`
- `https://www.shyfap.net/longest-videos_1/`
- `https://www.shyfap.net/pornstar/{slug}_p1/`
- `https://www.shyfap.net/studio/{slug}_s1/`
- `https://www.shyfap.net/tag/{slug}_t1/`
- `https://www.shyfap.net/search/?q=<query>`

### Metadata (`scrape`)

The site exposes full `og:`/`ya:ovs:` meta tags:

- Title: `og:title` (fallback `h1.title`)
- Description: `og:description`
- Thumbnail: `og:image` (`/images/thumb/{slug}.jpg`)
- Duration: `video:duration` in **seconds** (e.g. `1721` → `28:41`)
- Views: `ya:ovs:views_total` raw digits, returned verbatim
- Upload date: `ya:ovs:upload_date` ISO-8601 with offset (`2026-03-29T09:39:01+03:00` → normalized to `+0300` suffix form)
- Tags: `.datalist` rows — `Tags:` row links (19 per video; fallback: `video:tag` meta, comma-separated)
- Uploader (channel): the `Channel` datalist row (e.g. `NF Busty`)
- Models: the `Models` datalist row (e.g. `Kiara Lord`, currently not returned separately — channel is used for `uploader_name`)
- Related: `.catalog_item` cards under `Related Videos` (12 per page, same structure as listings)

### Categories (`get_categories`)

`categories.json` seeds the four sort tabs (New Videos, Most Watched, Top Rated, Longest). The site's full taxonomies are separate pages (`/categories_1/`, `/tags_1/`, `/pornstars_1/`, `/studios_1/`) but those list taxonomy items rather than videos, so they are not included as browse entries. Schema matches the other scraper folders so `/api/v1/categories?source=shyfap` returns valid `CategoryItem` entries.

### Registration checklist for ShyFap

Besides creating `backend/app/scrapers/shyfap/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=shyfap` or `source=shyfap.net`)
- `backend/app/services/video_streaming.py`
  - import list inside `get_video_info`
  - scraper selection branch (`elif shyfap.can_handle(host)`)
  - unsupported-host help text (`shyfap.net`)
  - `available_qualities` host list and `per_stream_format_keys` host list (`shyfap.net`, `www.shyfap.net` — the embed host is the site itself)
- `backend/app/models/schemas.py`
  - scrape URL allowlist (`shyfap.net`, `www.shyfap.net`)
  - list base URL allowlist (same hosts)
- `backend/app/api/endpoints/explore.py`
  - `ExploreSourceResponse` entry (`sourceId="shyfap"`, `baseUrl="https://www.shyfap.net/"`, `searchUrlTemplate="https://www.shyfap.net/search/?q={query}"`, `accentColor="#EC0040"`)

### ShyFap verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.shyfap.net/video/watching-you_2_v2/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.shyfap.net/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.shyfap.net/most-watched-videos_1/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=shyfap"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.shyfap.net/video/watching-you_2_v2/"
```

Notes from live testing (2026-09):

- Home listing (page 1 + page 2 via `/videos_1/2/`), most-watched page 2 (`/most-watched-videos_1/2/`), pornstar page 2, and `scrape()` metadata (title, duration `28:41` from 1721s, views raw digits verbatim, uploader `NF Busty`, upload date `2026-03-29T09:39:01+0300`, 19 tags, 12 related) were all verified.
- `scrape()` returns exactly one embed stream `https://www.shyfap.net/embed/{video_id}` (`Server 1`, `has_video=True`); the flashvars `/get_stream/` MP4 construction was removed after the direct URLs failed to play outside the site's player.
- Pagination gotcha: the first implementation rewrote the section LIST ID (`/most-watched-videos_1/` → `/most-watched-videos_2/`, wrong). The page is a separate path segment — append/replace the trailing numeric segment only.
- The site ships a `disable-devtool` CDN script (anti-devtools) — irrelevant for server-side scraping; plain `User-Agent` + `Referer` requests are sufficient (no Cloudflare challenge at test time).
