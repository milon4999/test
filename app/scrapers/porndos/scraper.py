from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.porndos.com/"
SITE_HOST = "porndos.com"
SITE_ALIASES = frozenset({"porndos.com", "www.porndos.com"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?porndos\.com/video/(?P<slug>[^/?#]+?_v\d+)/?$",
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

_FLASHVAR_RE = re.compile(r"(\w+)\s*:\s*'([^']*)'")
_KVS_QUALITIES = (
    ("video_alt_url3", "video_alt_url3_text"),
    ("video_alt_url2", "video_alt_url2_text"),
    ("video_alt_url", "video_alt_url_text"),
    ("video_url", "video_url_text"),
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".porndos.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    """porndos sits behind bot protection: plain aiohttp gets empty 200 bodies.
    curl_cffi impersonation is required; pool fallback kept as last resort."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return await pool_fetch_html(url, headers=_headers(referer))

    for imp in ("chrome120", "chrome116"):
        try:
            async with AsyncSession(impersonate=imp, timeout=40.0) as client:
                resp = await client.get(url, headers=_headers(referer))
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            await asyncio.sleep(1.0)
    try:
        return await pool_fetch_html(url, headers=_headers(referer))
    except Exception:
        return ""


def _headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    return headers


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
    if t.endswith(" - PornDos"):
        t = t[: -len(" - PornDos")].strip()
    return t or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0]
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    m = _VIDEO_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    slug = (m.group("slug") or "").lower()
    if not slug:
        return None
    return f"https://www.porndos.com/video/{slug}/"


def _best_image_url(img: Any, base: str = BASE_SITE) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src", "srcset"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"{base.rstrip('/')}{url}"
        return url
    return None


def _streams_from_flashvars(html: str) -> dict[str, Any]:
    """Parse kt_player flashvars. Direct MP4s: /get_stream/{id}-{quality}.mp4
    (redirects to OK.ru vkuser.net signed URLs — verified playable)."""
    m = re.search(r"var\s+flashvars\s*=\s*\{(.+?)\};", html or "", re.S)
    if not m:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    vars_: dict[str, str] = dict(_FLASHVAR_RE.findall(m.group(1)))
    streams: list[dict[str, str]] = []
    default: Optional[str] = None

    for url_key, text_key in _KVS_QUALITIES:
        url = (vars_.get(url_key) or "").strip()
        label = (vars_.get(text_key) or "").strip()
        if not url or not url.startswith("http"):
            continue
        streams.append({"url": url, "quality": label or "source", "format": "mp4"})
        if default is None:
            default = url

    # _KVS_QUALITIES is ordered highest-first (alt3 -> alt2 -> alt -> base)
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _streams_for_page(html: str, soup: BeautifulSoup, video_id: str | None) -> dict[str, Any]:
    flash = _streams_from_flashvars(html)
    if flash.get("has_video"):
        return flash

    # Fallback: og:video embed (https://www.porndos.com/embed/{id}/)
    embed = _meta(soup, prop="og:video")
    if embed and embed.startswith("http"):
        return {
            "streams": [{"url": embed, "quality": "Server 1", "format": "embed"}],
            "hls": None,
            "default": embed,
            "has_video": True,
        }
    if video_id and video_id.isdigit():
        embed = f"{BASE_SITE.rstrip('/')}/embed/{video_id}/"
        return {
            "streams": [{"url": embed, "quality": "Server 1", "format": "embed"}],
            "hls": None,
            "default": embed,
            "has_video": True,
        }

    return {"streams": [], "hls": None, "default": None, "has_video": False}


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select(".thumb .item"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/video/"]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        p = block.select_one("p")
        if p:
            title = _clean_title(p.get_text(" ", strip=True))
        if not title:
            img = block.select_one("img")
            if img and img.get("alt"):
                title = _clean_title(str(img.get("alt")).strip())
        img = block.select_one("img")

        duration = None
        views = None
        for span in block.select(".meta span"):
            if span.get("class") and "right" in span.get("class"):
                duration = span.get_text(" ", strip=True) or None
            elif span.find("i", class_="fa-eye"):
                views = span.get_text(" ", strip=True) or None

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": duration,
                "views": views,
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
    q = parse_qsl(parsed.query, keep_blank_values=True)

    if page_num <= 1:
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or "www.porndos.com", path, "", urlencode(q), "")
        )

    # KVS section pagination: /{name}_{id}/{page}/
    segments = [s for s in path.split("/") if s]
    if segments and re.fullmatch(r"\d+", segments[-1]):
        segments[-1] = str(page_num)
        new_path = "/" + "/".join(segments) + "/"
    elif segments:
        new_path = "/" + "/".join(segments) + f"/{page_num}/"
    else:
        new_path = f"/videos_60/{page_num}/"

    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or "www.porndos.com", new_path, "", urlencode(dict(q)) if q else "", "")
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one(".thumb-image img, .player img, img")),
    )

    # flashvars metadata
    fm = re.search(r"var\s+flashvars\s*=\s*\{(.+?)\};", html, re.S)
    fv: dict[str, str] = dict(_FLASHVAR_RE.findall(fm.group(1))) if fm else {}
    video_id = (fv.get("video_id") or "").strip() or None

    views: Optional[str] = None
    views_el = soup.select_one(".full-meta span i.fa-eye")
    if views_el:
        parent = views_el.find_parent("span")
        if parent:
            views = parent.get_text(" ", strip=True) or None

    # Duration: not exposed in flashvars; look for meta duration patterns
    duration: Optional[str] = None

    # Tags: video_categories flashvars + .full-links category links
    tags: list[str] = []
    cats = fv.get("video_categories") or ""
    for t in cats.split(","):
        t = t.strip()
        if t and t not in tags:
            tags.append(t)
    for a in soup.select(".full-links a[href*='/category/']"):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and len(t) < 60:
            tags.append(t)

    description = _meta(soup, prop="og:description") or None

    related = _parse_list_items(soup, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or {
        "streams": [],
        "hls": None,
        "default": canon,
        "has_video": False,
    }

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": None,
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
        raise ValueError(f"Unsupported PornDos URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")

    fm = re.search(r"var\s+flashvars\s*=\s*\{(.+?)\};", html, re.S)
    fv: dict[str, str] = dict(_FLASHVAR_RE.findall(fm.group(1))) if fm else {}
    video_id = (fv.get("video_id") or "").strip() or None

    video_data = _streams_for_page(html, soup, video_id)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
