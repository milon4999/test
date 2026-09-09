from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://motherlesss.net/"
SITE_HOST = "motherlesss.net"
SITE_HOSTS = frozenset({"motherlesss.net", "www.motherlesss.net"})
SITE_ALIASES = frozenset(
    {
        "motherlesss.net",
        "www.motherlesss.net",
        # legacy domains kept for can_handle compatibility
        "motherless.xxx",
        "www.motherless.xxx",
        "motherless.com",
        "www.motherless.com",
    }
)
CDN_HOST_MARKERS = ("motherlessmedia.com", "video.ogporn.com")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?motherlesss?\.(?:net|xxx|com)/(?P<slug>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)
_RESERVED_SLUGS = frozenset(
    {
        "category",
        "model",
        "series",
        "tags",
        "tag",
        "studio",
        "page",
        "feed",
        "privacy-policy",
        "contact",
        "login",
        "register",
        "wp-content",
        "wp-json",
        "wp-admin",
        "search",
    }
)
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def can_handle(host: str) -> bool:
    h = _normalize_host(host)
    if h in SITE_ALIASES:
        return True
    return any(marker in h for marker in CDN_HOST_MARKERS)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer)
    if text:
        return text
    html = await pool_fetch_html(url, headers=_DEFAULT_HEADERS)
    return html or ""


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (" - Motherless", " | Motherless"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _format_duration_iso(iso: str | None) -> Optional[str]:
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


def _normalize_slug_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    parsed = urlparse(href.split("#", 1)[0])
    host = _normalize_host(parsed.netloc or "")
    if host not in SITE_HOSTS:
        return None
    path = (parsed.path or "").strip("/")
    if not path:
        return None
    slug = path.split("/")[-1]
    if not slug or slug in _RESERVED_SLUGS:
        return None
    return f"https://{SITE_HOST}/{slug}/"


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _json_ld_graph(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                out.extend(g for g in graph if isinstance(g, dict))
            else:
                out.append(data)
    return out


def _video_object(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
    for g in _json_ld_graph(soup):
        if g.get("@type") == "VideoObject":
            return g
    return None


def _streams_from_html(html: str) -> dict[str, Any]:
    """Direct MP4s live in `<video><source src="https://video.ogporn.com/...">`."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "lxml")

    for source in soup.select("video source[src]"):
        src = (source.get("src") or "").strip()
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "source", "format": "mp4"})

    if not streams:
        for m in re.finditer(
            r"https?://[^\s\"'<>]*video\.ogporn\.com/[^\s\"'<>]*\.mp4", html, re.IGNORECASE
        ):
            u = m.group(0)
            if u not in seen:
                seen.add(u)
                streams.append({"url": u, "quality": "source", "format": "mp4"})

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_card_blocks(html: str, *, limit: int) -> list[dict[str, Any]]:
    """Parse `<a class="video" href=... title=...>` cards with CSS-background thumbs.

    The thumbnail lives in the inline style (`background-image: url('...webp')`),
    duration in `span.time`, relative date in `span.ago`, title in `h2.vtitle`.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for m in re.finditer(
        r'<a class="video"[^>]*style="[^"]*background-image:\s*url\(\'?([^\'\)"]+)\'?\)[^"]*"[^>]*'
        r'(?:title="([^"]*)")?[^>]*href="(https://motherlesss\.net/[^"]+)"(.*?)</a>',
        html,
        re.S,
    ):
        if len(items) >= limit:
            break
        bg, title_attr, href, inner = m.groups()
        url = _normalize_slug_href(href)
        if not url or url in seen:
            continue
        seen.add(url)

        tm = re.search(r'<h2 class="vtitle">(.*?)</h2>', inner, re.S)
        title = _clean_title(re.sub(r"<[^>]+>", "", tm.group(1)).strip()) if tm else _clean_title(title_attr)
        dm = re.search(r'<span class="time clock">([^<]+)</span>', inner)
        duration = dm.group(1).strip() if dm else None

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": bg.strip(),
                "duration": duration,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items = _parse_card_blocks(html, limit=limit)
    if len(items) < limit:
        for a in soup.select('a[href^="https://motherlesss.net/"]'):
            if len(items) >= limit:
                break
            url = _normalize_slug_href(a.get("href") or "")
            if not url or url in {i["url"] for i in items}:
                continue
            title_el = a.select_one("h2.vtitle")
            if not title_el:
                continue
            items.append(
                {
                    "url": url,
                    "title": _clean_title(title_el.get_text(" ", strip=True)) or "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
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

    # WordPress path pagination: /page/2/
    if page_num > 1:
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"
        path = f"{path}/page/{page_num}" if path != "/" else f"/page/{page_num}"
    elif re.search(r"/page/\d+$", path, re.I):
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"

    qs = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if v}
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", urlencode(qs) if qs else "", "")
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_slug_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1.stitle").get_text(" ", strip=True) if soup.select_one("h1.stitle") else None,
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    ld = _video_object(soup) or {}

    description = _first_non_empty(
        ld.get("description"), _meta(soup, prop="og:description"), _meta(soup, name="description")
    )
    thumbnail = _first_non_empty(ld.get("thumbnailUrl"), _meta(soup, prop="og:image"))
    duration = _format_duration_iso(ld.get("duration"))
    upload_date = ld.get("uploadDate")

    # Studio (author org) + models (actors)
    uploader = None
    author = ld.get("author")
    if isinstance(author, list) and author:
        uploader = author[0].get("name")
    elif isinstance(author, dict):
        uploader = author.get("name")
    if not uploader:
        up = soup.select_one('.model-list strong ~ a, a[href*="/studio/"]')
        if up:
            uploader = up.get_text(strip=True) or None

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"], a[class="cat"]'):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and len(t) < 60:
            tags.append(t)

    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or _streams_from_html(html)

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
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


def _is_missing_media_page(html: str) -> bool:
    low = (html or "").lower()
    return "file not found" in low or "the page you're looking for cannot be found" in low


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_slug_href(url)
    if not canon:
        raise ValueError(f"Unsupported Motherless URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    video_data = _streams_from_html(html)
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
    return _parse_list_items(soup, html, limit=limit)
