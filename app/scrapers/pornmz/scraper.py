from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://pornmz.net/"
SITE_HOST = "pornmz.net"
SITE_ALIASES = frozenset({"pornmz.net", "www.pornmz.net"})

_RESERVED_PATH_HEADS = ("pmzvideo", "page", "category", "tag", "actor", "wp-content", "wp-json", "wp-admin", "feed")

# /video/id=pmz/{category}/{numeric_id}
_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?pornmz\.net/video/id=[^/]+/[^/]+/\d+/?$",
    re.IGNORECASE,
)
_MP4_RE = re.compile(
    r"https?://[^\s\"'<>]+\.mp4(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_M3U8_RE = re.compile(
    r"https?://[^\s\"'<>]+\.m3u8(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".pornmz.net")


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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or BASE_SITE,
    }
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        for imp in ("chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.text) > 2000:
                        return resp.text
            except Exception:
                continue
    try:
        return await pool_fetch_html(url, headers=headers)
    except Exception:
        return ""


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _itemprop(soup: BeautifulSoup, prop: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"itemprop": prop})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (" - Pornmz", " | Pornmz", " Pornmz"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    href = href.split("#", 1)[0]
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    m = _VIDEO_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    # normalize without trailing slash
    return href.rstrip("/")


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _decode_player_q(q: str) -> str:
    """clean-tube-player payload: base64 -> URL-encoded video.js markup."""
    raw = (q or "").strip()
    if not raw:
        return ""
    pad = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(pad).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    decoded = re.sub(r"%25([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), decoded)
    decoded = decoded.replace("%20", " ").replace("\\/", "/")
    return decoded


def _streams_from_html(html: str, soup: BeautifulSoup) -> dict[str, Any]:
    """
    Pornmz serves HLS streams from Twitter's video CDN (video.twimg.com).
    Streams returned (all format="embed" — the app's embed/WebView path):
      1. The clean-tube-player player-x.php?q=... iframe URL itself (Server 1,
         default) — the site's own video.js player, plays reliably in WebView.
      2. The raw twimg m3u8 (from itemprop="contentUrl" or the player payload)
         as a secondary option (the native player cannot play it).
    """
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, label: str) -> None:
        url = url.strip()
        if url.startswith("http") and url not in seen:
            seen.add(url)
            streams.append({"url": url, "quality": label, "format": "embed"})

    # 1) The player-x.php embed URL itself (the playable wrapper)
    for iframe in soup.select('iframe[src*="player-x.php?q="]'):
        src = (iframe.get("src") or "").strip()
        if src.startswith("http"):
            _add(src, "Server 1")

    # 2) The raw twimg m3u8
    content_url = _itemprop(soup, "contentUrl")
    if content_url and ".m3u8" in content_url.lower():
        _add(content_url, "adaptive")

    for iframe in soup.select('iframe[src*="player-x.php?q="]'):
        m = re.search(r"q=([^&\"']+)", iframe.get("src") or "")
        if not m:
            continue
        payload = _decode_player_q(m.group(1))
        for sm in re.finditer(r'<source[^>]+src="([^"]+)"[^>]*type="[^"]*m3u8', payload):
            _add(sm.group(1), "adaptive")
        for sm in re.finditer(r'<source[^>]+src="([^"]+\.mp4)', payload):
            _add(sm.group(1), "source")

    if not streams:
        html_norm = html.replace("\\/", "/")
        for u in _M3U8_RE.findall(html_norm):
            _add(u, "adaptive")
        for u in _MP4_RE.findall(html_norm):
            if "/wp-content/" not in u.lower():
                _add(u, "source")

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("article.thumb-block"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href]")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        header = block.select_one("header.entry-header span.title")
        if header:
            title = _clean_title(header.get_text(" ", strip=True))
        if not title:
            title = _clean_title(link.get("title"))
        img = block.select_one("img")
        views_el = block.select_one("span.views")
        dur_el = block.select_one("span.duration")

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": views_el.get_text(strip=True) if views_el else None,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/").rstrip("/") or "/"

    # WordPress path pagination: /page/2/ (works for home, search, category)
    if page_num > 1:
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"
        path = f"{path}/page/{page_num}" if path != "/" else f"/page/{page_num}"
    elif re.search(r"/page/\d+$", path, re.I):
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"

    path = path if path.endswith("/") else f"{path}/"
    qs = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if v}
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", urlencode(qs) if qs else "", "")
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    # NOTE: the page carries TWO itemprop="name" metas — the site name
    # ("Pornmz") comes FIRST and the video title second, so og:title/h1 win.
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            next(
                (str(m.get("content")).strip() for m in reversed(soup.find_all("meta", attrs={"itemprop": "name"}))
                 if m.get("content") and "pornmz" not in str(m.get("content")).lower()),
                None,
            ),
        )
    ) or "Unknown Video"

    description = _first_non_empty(_itemprop(soup, "description"), _meta(soup, prop="og:description"))
    thumbnail = _first_non_empty(_itemprop(soup, "thumbnailUrl"), _meta(soup, prop="og:image"))
    upload_date = _itemprop(soup, "uploadDate")

    # Views: .title-views span.views (fa-eye + count)
    views: Optional[str] = None
    views_el = soup.select_one(".title-views span.views")
    if views_el:
        views = views_el.get_text(strip=True) or None

    # Tags: .tags-list a.label links (categories + tags)
    tags: list[str] = []
    for a in soup.select(".tags-list a.label"):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and len(t) < 60:
            tags.append(t)

    related = _parse_list_items(soup, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or _streams_from_html(html, soup)

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": views,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported Pornmz URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _streams_from_html(html, soup)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or BASE_SITE
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
