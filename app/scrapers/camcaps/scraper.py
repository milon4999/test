from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://camcaps.tv/"
SITE_HOST = "camcaps.tv"
SITE_ALIASES = frozenset({"camcaps.tv", "www.camacaps.tv"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?camcaps\.tv/video/(?P<vid>\d+)/(?P<slug>[^/?#]+)/?$",
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
_VIEWS_RE = re.compile(r"([\d.,]+)\s*([KkMm]?)")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".camcaps.tv")


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
    t = str(title).strip()
    for suffix in (" - CamCaps.TV", " | CamCaps.TV", " - CamCaps", " | CamCaps"):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    """Parse `12.3K views` / `1,182 views` / `102` into a plain digit string."""
    if not text:
        return None
    m = _VIEWS_RE.search(str(text))
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        value = float(num)
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    if value <= 0:
        return None
    return str(int(value))


def _absolute_url(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    if not href.startswith("http"):
        return None
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = _absolute_url(href)
    if not href:
        return None
    m = _VIDEO_PAGE_RE.match(href)
    if not m:
        return None
    return f"https://{SITE_HOST}/video/{m.group('vid')}/{m.group('slug')}"


def _best_image_url(img: Any) -> Optional[str]:
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
            return f"{BASE_SITE.rstrip('/')}{url}"
        return url
    return None


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    return any(
        x in s
        for x in (
            "xhadapt.php",
            "googlesyndication",
            "doubleclick",
            "adservice",
            "acscdn.com",
            "magsrv.com",
            "nappyonsetstiffness.com",
            "trudigo.com/banner",
        )
    )


def _streams_from_html(html: str) -> dict[str, Any]:
    html_norm = html.replace("\\/", "/").replace("\\u0026", "&")
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    for pat, fmt in ((_M3U8_RE, "hls"), (_MP4_RE, "mp4")):
        for raw in pat.findall(html_norm):
            url = raw.strip().rstrip("/")
            if not url.startswith("http") or url in seen:
                continue
            if "/media/videos/tmb" in url.lower():
                continue
            seen.add(url)
            entry = {"url": url, "quality": "adaptive", "format": fmt}
            streams.append(entry)
            if fmt == "hls" and not hls_url:
                hls_url = url

    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _embed_streams(soup: BeautifulSoup) -> dict[str, Any]:
    """Collect player iframes from .video-embedded (filter ad iframes)."""
    embed_urls: list[str] = []
    container = soup.select_one(".video-embedded, .player")
    for iframe in (container.select("iframe[src]") if container else soup.select("iframe[src]")):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if not src.startswith("http"):
            continue
        if _is_probable_ad_iframe(src):
            continue
        if src not in embed_urls:
            embed_urls.append(src)

    streams: list[dict[str, str]] = []
    for i, u in enumerate(embed_urls, start=1):
        streams.append({"url": u, "quality": f"Server {i}", "format": "embed"})

    default = embed_urls[0] if embed_urls else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(default),
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    blocks = soup.select("article.thumb, .thumb .inner")
    if not blocks:
        blocks = soup.select("article.thumb .inner")

    for block in blocks:
        if len(items) >= limit:
            break
        link = block.select_one("a[href*='/video/']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        title_el = block.select_one("h3")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        img = block.select_one("img")
        dur_el = block.select_one("span.dur-icon")
        views_el = block.select_one("span.views-icon")

        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(title, link.get("title"), img.get("alt") if img else None)
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": _normalize_views(views_el.get_text() if views_el else None),
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

    if page_num <= 1:
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, parsed.path or "/", "", parsed.query, "")
        )

    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q["page"] = str(page_num)
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            parsed.path or "/",
            "",
            urlencode(q),
            "",
        )
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
            _meta(soup, name="description"),
        )
    ) or "Unknown Video"

    thumbnail = _best_image_url(soup.select_one(".video-embedded img, .player img, article.thumb img"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views = None
    views_el = soup.select_one(".info span.views-icon")
    if views_el:
        views = _normalize_views(views_el.get_text())

    uploader = None
    uploader_el = soup.select_one(".video-links .group a[href*='/user/']")
    if uploader_el:
        uploader = uploader_el.get_text(strip=True) or None

    tags: list[str] = []
    for a in soup.select(".video-links .group a[href*='/search/videos/']"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 60:
            tags.append(tag)

    description = None
    about_el = soup.select_one("article.about p")
    if about_el:
        description = about_el.get_text(" ", strip=True) or None

    related = _parse_list_items(soup, limit=30)
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
        "duration": None,
        "views": views,
        "uploader_name": uploader,
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
        raise ValueError(f"Unsupported CamCaps URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _embed_streams(soup)
    if not video_data.get("has_video"):
        video_data = _streams_from_html(html)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
