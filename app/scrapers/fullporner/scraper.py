from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://fullporner.com/"
SITE_HOST = "fullporner.com"
SITE_ALIASES = frozenset({"fullporner.com", "www.fullporner.com"})

_WATCH_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?fullporner\.com/watch/(?P<vid>[a-z0-9]+)/?$",
    re.IGNORECASE,
)
_EMBED_IFRAME_RE = re.compile(
    r"^(?:https?:)?//(?P<host>[^/]+)/video/(?P<vid>[a-z0-9]+)/(?P<mask>\d+)/?$",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".fullporner.com")


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
    for suffix in (" - fullporner.com | FullPorner.com", " | FullPorner.com", " - FullPorner.com"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _format_timestamp(ts: str | None) -> Optional[str]:
    """Convert a Unix timestamp string to an ISO-8601 UTC date string."""
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(str(ts).strip()), tz=timezone.utc)
    except (ValueError, OSError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


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
    return href


def _normalize_video_href(href: str) -> Optional[str]:
    href = _absolute_url(href)
    if not href:
        return None
    m = _WATCH_PAGE_RE.match(href.split("#", 1)[0])
    if not m:
        return None
    return f"https://{SITE_HOST}/watch/{m.group('vid')}"


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
        return url
    return None


def _streams_for_page(html: str, soup: BeautifulSoup) -> dict[str, Any]:
    """FullPorner's direct MP4s do not play outside the player, so only the
    player iframe is returned as an embed stream."""
    iframe = soup.select_one(".single-video iframe[src]")
    if iframe and iframe.get("src"):
        src = str(iframe.get("src")).strip()
        if src.startswith("//"):
            src = f"https:{src}"
        if src.startswith("http"):
            return {
                "streams": [{"url": src, "quality": "Server 1", "format": "embed"}],
                "hls": None,
                "default": src,
                "has_video": True,
            }

    return {"streams": [], "hls": None, "default": None, "has_video": False}


def _poster_from_iframe(iframe_src: str) -> Optional[str]:
    """Listing-style thumbnail: https://imgs.xiaoshenke.net/thumb/{reversed_id}.jpg."""
    m = _EMBED_IFRAME_RE.match(iframe_src)
    if not m:
        return None
    reversed_id = m.group("vid")[::-1]
    return f"https://imgs.xiaoshenke.net/thumb/{reversed_id}.jpg"


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select(".video-card"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href*='/watch/']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        title_el = block.select_one(".video-title a")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        img = block.select_one("img")
        dur_el = block.select_one(".video-card-image .time")
        date_el = block.select_one(".video-view span.create")

        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(title, link.get("title"), img.get("alt") if img else None)
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": None,
                "uploader_name": None,
                "tags": None,
                "upload_date": _format_timestamp(date_el.get_text(strip=True) if date_el else None),
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

    # Search endpoints paginate via ?page= query param
    path = (parsed.path or "/").rstrip("/") or "/"
    if path.startswith("/search"):
        q["page"] = str(page_num)
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", urlencode(q), ""))

    # Home paginates at /home/{n}; sections append a numeric path segment
    if path in ("/", ""):
        new_path = f"/home/{page_num}"
    else:
        segments = [s for s in path.split("/") if s]
        if segments and re.fullmatch(r"\d+", segments[-1]):
            segments[-1] = str(page_num)
            new_path = "/" + "/".join(segments)
        else:
            new_path = f"{path}/{page_num}"

    return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", urlencode(q) if q else "", ""))


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one(".single-video-title h2").get_text(" ", strip=True)
            if soup.select_one(".single-video-title h2")
            else None,
            soup.title.get_text(strip=True) if soup.title else None,
            _meta(soup, name="description"),
        )
    ) or "Unknown Video"

    iframe = soup.select_one(".single-video iframe[src]")
    thumbnail: Optional[str] = None
    if iframe and iframe.get("src"):
        thumbnail = _poster_from_iframe(str(iframe.get("src")).strip())

    tags: list[str] = []
    for a in soup.select(".tag-link a[href*='/category/']"):
        tag = a.get_text(strip=True).lstrip("#").strip()
        if tag and tag not in tags and len(tag) < 50:
            tags.append(tag)

    uploader = None
    pornstar_el = soup.select_one(".single-video-info-content a.fullname")
    if pornstar_el:
        uploader = pornstar_el.get_text(strip=True) or None

    duration: Optional[str] = None
    upload_date: Optional[str] = None
    info_el = soup.select_one(".video-info")
    if info_el:
        date_el = info_el.select_one("span.create")
        if date_el:
            upload_date = _format_timestamp(date_el.get_text(strip=True))
        for div in info_el.find_all("div", recursive=False):
            text = div.get_text(strip=True)
            if re.fullmatch(r"(?:\d{1,2}:)?\d{1,2}:\d{2}", text):
                duration = text
                break

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
        "description": _meta(soup, name="description"),
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


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported FullPorner URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _streams_for_page(html, soup)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
