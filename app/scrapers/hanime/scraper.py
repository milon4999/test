from __future__ import annotations

import json
import html as htmllib
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://hanime.tv/"
SITE_HOST = "hanime.tv"
SITE_ALIASES = frozenset({"hanime.tv", "www.hanime.tv"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hanime\.tv/videos/hentai/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hanime.tv")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or BASE_SITE,
    }
    return await pool_fetch_html(url, headers=headers)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    # Strip the "Watch X hentai stream online HD 1080p, 720p" card title pattern
    m = re.match(r"^Watch\s+(.*?)\s+hentai stream online", t, re.IGNORECASE)
    if m:
        t = m.group(1).strip()
    if t.endswith(" - hanime.tv"):
        t = t[: -len(" - hanime.tv")].strip()
    return t or None


def _format_duration_iso(iso: str | None) -> Optional[str]:
    """Convert ISO 8601 duration (PT21M15S) to MM:SS / H:MM:SS."""
    if not iso:
        return None
    m = _ISO_DURATION_RE.match(str(iso).strip())
    if not m:
        return None
    parts = m.groupdict()
    days = int(parts.get("days") or 0)
    hours = int(parts.get("hours") or 0) + days * 24
    minutes = int(parts.get("minutes") or 0)
    seconds = int(parts.get("seconds") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    mi, s = divmod(rem, 60)
    return f"{h}:{mi:02d}:{s:02d}" if h > 0 else f"{mi:02d}:{s:02d}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("/"):
        href = f"https://{SITE_HOST}{href}"
    m = _VIDEO_PAGE_RE.match(href.split("#", 1)[0])
    if not m:
        return None
    return f"https://{SITE_HOST}/videos/hentai/{m.group('slug')}"


def _json_ld_video_object(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return None


def _parse_card_blocks(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse .video-card blocks from listing pages (server-rendered Astro HTML).

    Cards: <a href="/videos/hentai/{slug}" title="Watch X hentai stream online...">
           with <img src=covers/...>, <h3> title, eye-icon + <span> views.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocks = re.split(r"(?=<a href=\"/videos/hentai/)", html)
    for block in blocks[1:]:
        if len(items) >= limit:
            break
        lm = re.match(r"<a href=\"/videos/hentai/([^\"?#]+)\"", block)
        if not lm:
            continue
        slug = lm.group(1)
        url = f"https://{SITE_HOST}/videos/hentai/{slug}"
        if url in seen:
            continue
        seen.add(url)

        # Only take the card's own chunk (up to the next card link or ~6KB)
        chunk = block[:6000]
        nxt = chunk.find('<a href="/videos/hentai/', 1)
        if nxt != -1:
            chunk = chunk[:nxt]

        title = None
        tm = re.search(r"<h3[^>]*>(.*?)</h3>", chunk, re.S)
        if tm:
            title = _clean_title(htmllib.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip())
        if not title:
            ttm = re.match(r"<a href=\"/videos/hentai/[^\"?#]+\"[^>]*title=\"([^\"]*)\"", chunk)
            if ttm:
                title = _clean_title(htmllib.unescape(ttm.group(1)))

        im = re.search(r"src=\"(https://hanime-cdn\.com/images/covers/[^\"]+)\"", chunk)
        cover = im.group(1) if im else None

        vm = re.search(
            r"icon=\"mdi:eye-outline\"[^>]*></iconify-icon>\s*<span>\s*([^<]+?)\s*</span>", chunk
        )
        views = vm.group(1).strip() if vm else None

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": cover,
                "duration": None,
                "views": views,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta_text(soup, "og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    ld = _json_ld_video_object(soup) or {}

    description = _first_non_empty(
        ld.get("description"), _meta_text(soup, "og:description")
    )

    thumbnail = _first_non_empty(
        ld.get("thumbnailUrl"), _meta_text(soup, "og:image")
    )

    duration = _format_duration_iso(ld.get("duration"))
    upload_date = ld.get("uploadDate")

    # Views: eye icon + span (e.g. "198.7K") — kept verbatim
    views: Optional[str] = None
    eye = soup.find("iconify-icon", attrs={"icon": "mdi:eye-outline"})
    if eye:
        span = eye.find_next_sibling("span")
        if span:
            views = span.get_text(strip=True) or None

    # Tags: /browse/tags/{slug} badge links
    tags: list[str] = []
    for a in soup.select('a[href^="/browse/tags/"]'):
        tag = a.get_text(" ", strip=True)
        if tag and tag not in tags and len(tag) < 60:
            tags.append(tag)

    # Brand: "Studio <strong>X</strong>" link
    uploader: Optional[str] = None
    for a in soup.select('a[href^="/browse/brands/"]'):
        strong = a.find("strong")
        if strong:
            uploader = strong.get_text(strip=True) or None
            break

    # Related videos: other /videos/hentai/ cards on the page (next-video + sidebar).
    # Cards inside the recommendations slider carry no h3/title attr in SSR HTML;
    # reuse the video page slug as fallback title via the JSON-LD name.
    related = _parse_card_blocks(html, 24)
    related = [r for r in related if r["url"] != canon]
    if related:
        fallback_title = title
        for r in related:
            if r["title"] == "Unknown Video":
                r["title"] = fallback_title

    # NOTE: streams are NOT extractable server-side. The site's kt_player uses an
    # AES-GCM handshake (/api/v11/handshake) gated by Cloudflare + Turnstile, and
    # the resulting HLS manifest is delivered as *text* minted for the browser
    # session. The app plays hanime via its WebView player (missav-style), so the
    # scraper returns the watch page URL as the embed source.
    video_data: dict[str, Any] = {
        "streams": [{"url": canon, "quality": "Server 1", "format": "embed"}],
        "hls": None,
        "default": canon,
        "has_video": True,
    }

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": video_data,
        "related_videos": related,
    }


def _meta_text(soup: BeautifulSoup, prop: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported hanime.tv URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    return parse_video_page(html, canon)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse_safe(raw)
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query

    # Sort sections under /browse/* keep their path; home uses /browse/trending
    if path in ("/", ""):
        path = "/browse/trending"

    # Pagination: ?page=N query param
    params = [p for p in query.split("&") if p and not p.startswith("page=")] if query else []
    if page_num > 1:
        params.append(f"page={page_num}")
    query = "&".join(params)

    page_url = f"https://{SITE_HOST}{path}" + (f"?{query}" if query else "")
    try:
        html = await fetch_page(page_url, referer=BASE_SITE)
    except Exception:
        return []
    return _parse_card_blocks(html, limit)


def urlparse_safe(url: str):
    from urllib.parse import urlparse

    return urlparse(url)
