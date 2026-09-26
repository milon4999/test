from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.porndish.com/"
SITE_HOST = "porndish.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

# Video detail pages live under /porn/{slug}/ (WordPress posts with format-video).
_VIDEO_HREF_RE = re.compile(r"porndish\.com/porn/([^/?#]+)/?", re.IGNORECASE)

# Player iframes are embedded as JS strings in inline <script> blocks, e.g.:
#   const doodstreamContent = "<iframe width=\"600\" ... src=\"https:\/\/playmogo.com\/e\/...\" ...>";
# The attribute delimiter may be an escaped quote (`\"`) inside the JS string, so allow
# an optional backslash before the opening/closing quote.
_IFRAME_SRC_RE = re.compile(r'<iframe\b[^>]*?\bsrc\s*=\\?["\']([^"\']+)["\']', re.IGNORECASE)

# Known ad / tracking hosts that should never be exposed as a stream.
_AD_IFRAME_KEYWORDS = (
    "whitetrafsa.com",
    "creative.whitetrafsa.com",
    "googlesyndication",
    "doubleclick",
    "adservice",
    "taboola",
    "outbrain",
    "cams.",
    "go.whitetrafsa.com",
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == SITE_HOST or h.endswith("." + SITE_HOST)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
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
    for suffix in (" - Porndish.com", " | Porndish.com", " - Porndish", " | Porndish"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _unescape_media_url(url: str) -> str:
    """Undo common script-escape sequences (\\/ -> /, \\u0026 -> &)."""
    u = (url or "").strip().replace("\\/", "/")
    u = u.replace("\\u0026", "&").replace("&amp;", "&")
    u = u.replace('\\"', '"')
    if u.endswith("\\"):
        u = u[:-1]
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    return u


def _is_probable_ad_iframe(src: str) -> bool:
    low = (src or "").lower()
    return any(k in low for k in _AD_IFRAME_KEYWORDS)


def _collect_embed_iframe_srcs(html: str) -> list[str]:
    """
    Collect player iframe src URLs from the raw HTML.

    Porndish exposes its players as JS-string iframes inside inline <script>
    blocks (Video Player 1 / Video Player 2). We scan the whole document,
    unescape script-escaped URLs, and drop obvious ad/tracking iframes.
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in _IFRAME_SRC_RE.finditer(html or ""):
        src = _unescape_media_url(m.group(1))
        if not src.startswith("http"):
            continue
        if _is_probable_ad_iframe(src):
            continue
        if src in seen:
            continue
        seen.add(src)
        found.append(src)
    return found


def _quality_from_embed_url(url: str) -> str:
    """Stable short label from the embed host (playmogo, vidara, ...)."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0] if host else "embed"


def _qualities_for_embed_urls(urls: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    out: list[str] = []
    for u in urls:
        base = _quality_from_embed_url(u)
        n = counts.get(base, 0)
        counts[base] = n + 1
        out.append(base if n == 0 else f"{base}_{n + 1}")
    return out


def _default_embed_url(embed_urls: list[str]) -> Optional[str]:
    # Prefer Doodstream/playmogo (Video Player 1) as default, else first embed.
    for u in embed_urls:
        if "playmogo.com" in u.lower() or "dood" in u.lower():
            return u
    return embed_urls[0] if embed_urls else None


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if data.get("@graph"):
                for node in data.get("@graph", []):
                    if isinstance(node, dict):
                        out.append(node)
            else:
                out.append(data)
        elif isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
    return out


def _json_ld_field(soup: BeautifulSoup, field: str) -> Optional[str]:
    for node in _parse_json_ld(soup):
        if field in node:
            val = node[field]
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()
    return None


def _json_ld_keywords(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for node in _parse_json_ld(soup):
        kw = node.get("keywords")
        if not kw:
            continue
        if isinstance(kw, str):
            parts = [p.strip() for p in re.split(r"[,|]", kw) if p.strip()]
        elif isinstance(kw, list):
            parts = [str(p).strip() for p in kw if str(p).strip()]
        else:
            continue
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                tags.append(p)
    return tags


def _best_thumbnail(soup: BeautifulSoup) -> Optional[str]:
    thumb = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _json_ld_field(soup, "thumbnailUrl"),
    )
    if not thumb:
        # Fallback to the first <img> inside the article featured media.
        media = soup.select_one(".entry-featured-media img, .g1-content-narrow img")
        if media:
            thumb = media.get("data-src") or media.get("src")
    if not thumb:
        return None
    return _unescape_media_url(str(thumb))


def _parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            _json_ld_field(soup, "headline"),
            _json_ld_field(soup, "name"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
        _json_ld_field(soup, "description"),
    )

    thumbnail = _best_thumbnail(soup)

    tags: list[str] = _json_ld_keywords(soup)
    for tag in soup.select(".entry-tags .entry-tag"):
        txt = tag.get_text(strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    upload_date = _first_non_empty(
        _json_ld_field(soup, "datePublished"),
        _meta(soup, prop="article:published_time"),
    )

    category = None
    cat_link = soup.select_one(".entry-categories .entry-category, .entry-before-title .entry-category")
    if cat_link:
        category = cat_link.get_text(strip=True) or None

    embed_urls = _collect_embed_iframe_srcs(html)
    quality_labels = _qualities_for_embed_urls(embed_urls)
    streams: list[dict[str, str]] = []
    for e, q in zip(embed_urls, quality_labels):
        streams.append({"url": e, "quality": q, "format": "embed"})
    default_url = _default_embed_url(embed_urls)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "video": {
            "streams": streams,
            "hls": None,
            "default": default_url,
            "has_video": bool(default_url),
        },
        "related_videos": [],
        "preview_url": None,
    }


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if text:
        return text

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=canon)
    return _parse_video_page(html, canon)


def _normalize_video_href(url: str) -> Optional[str]:
    """Return a canonical /porn/{slug}/ URL, or None if not a detail page."""
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    m = _VIDEO_HREF_RE.search(raw)
    if not m:
        return None
    return f"https://www.porndish.com/porn/{m.group(1)}/"


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    if page_num <= 1:
        return raw

    parsed = urlparse(raw)
    path = parsed.path or "/"
    # Strip any existing /page/{n}/ segment.
    path = re.sub(r"/page/\d+/?$", "", path)
    path = path.rstrip("/")
    if not path:
        new_path = f"/page/{page_num}/"
    else:
        new_path = f"{path}/page/{page_num}/"
    return urlunparse_safe(parsed, new_path)


def urlunparse_safe(parsed, new_path: str) -> str:
    from urllib.parse import urlunparse

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _thumb_from_img(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        raw = str(v).strip()
        if not raw or raw.startswith("data:"):
            continue
        if key == "srcset" and " " in raw:
            raw = raw.split(" ", 1)[0].strip()
        if raw.startswith("//"):
            return f"https:{raw}"
        if raw.startswith("/"):
            return urljoin(BASE_SITE, raw)
        return raw
    return None


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    frame = box.select_one("a.g1-frame[href], .entry-title a[href], .entry-featured-media a[href]")
    if not frame:
        return None
    href = _normalize_video_href(frame.get("href") or "")
    if not href:
        return None

    title = _clean_title(
        _first_non_empty(
            frame.get("title"),
            frame.get("alt"),
            (title_a.get_text(" ", strip=True) if (title_a := box.select_one(".entry-title a")) else None),
            frame.get_text(" ", strip=True),
        )
    ) or "Unknown Video"

    img = box.select_one(".entry-featured-media img, img")
    thumb = _thumb_from_img(img)

    duration = None
    dur_el = box.select_one(".mace-video-duration, .entry-duration")
    if dur_el:
        duration = dur_el.get_text(strip=True) or None

    views = None
    views_el = box.select_one(".entry-views strong")
    if views_el:
        views = views_el.get_text(strip=True) or None

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for box in soup.select(".g1-collection-item article, article.entry-tpl-grid, article.entry-tpl-list"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for a in soup.select("a[href*='/porn/']"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href or href in seen:
                continue
            seen.add(href)
            img = a.find("img")
            items.append(
                {
                    "url": href,
                    "title": _clean_title(a.get("title") or a.get_text(strip=True)) or "Unknown Video",
                    "thumbnail_url": _thumb_from_img(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )

    return items[:limit]
