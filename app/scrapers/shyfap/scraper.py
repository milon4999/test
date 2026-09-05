from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.shyfap.net/"
SITE_HOST = "www.shyfap.net"
SITE_HOST_NAKED = "shyfap.net"
SITE_ALIASES = frozenset({"shyfap.net", "www.shyfap.net"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?shyfap\.net/video/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")
_PREVIEW_MP4_RE = re.compile(r"/images/video/(\d+)\.mp4")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".shyfap.net")


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
    for suffix in (" - ShyFap",):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _format_seconds(seconds: str | None) -> Optional[str]:
    if not seconds:
        return None
    try:
        total = int(str(seconds).strip())
    except ValueError:
        return None
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def _format_upload_date(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return str(raw).strip()
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


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
    host = (parsed.netloc or "").lower()
    if host.replace("www.", "") != SITE_HOST_NAKED:
        return None
    return href


def _normalize_video_href(href: str) -> Optional[str]:
    href = _absolute_url(href)
    if not href:
        return None
    m = _VIDEO_PAGE_RE.match(href.split("#", 1)[0])
    if not m:
        return None
    slug = (m.group("slug") or "").strip()
    if not slug:
        return None
    return f"{BASE_SITE.rstrip('/')}/video/{slug}/"


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-src", "src", "srcset"):
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


def _streams_for_page(soup: BeautifulSoup) -> dict[str, Any]:
    """
    ShyFap's kt_player flashvars expose direct `get_stream/{id}-{quality}.mp4`
    URLs, but those are IP/license-locked and fail outside the player, so the
    stable same-host embed `https://www.shyfap.net/embed/{video_id}` is
    returned instead.
    """
    embed_url = _meta(soup, prop="og:video")
    if not embed_url or "/embed/" not in embed_url:
        video_id = _meta(soup, prop="ya:ovs:id")
        if video_id and video_id.isdigit():
            embed_url = f"{BASE_SITE.rstrip('/')}/embed/{video_id}"

    if embed_url and embed_url.startswith("http"):
        return {
            "streams": [{"url": embed_url, "quality": "Server 1", "format": "embed"}],
            "hls": None,
            "default": embed_url,
            "has_video": True,
        }

    return {"streams": [], "hls": None, "default": None, "has_video": False}


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select(".catalog_item"):
        if len(items) >= limit:
            break
        link = block.select_one("a.media-card[href*='/video/']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        title_el = block.select_one(".media-card_title")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        img = block.select_one("img")
        if not title and img and img.get("alt"):
            title = str(img.get("alt")).strip()

        duration: Optional[str] = None
        views: Optional[str] = None
        for item in block.select(".stats_item"):
            icon = item.select_one("use")
            icon_ref = (icon.get("xlink:href") or icon.get("href") or "") if icon else ""
            value_el = item.select_one(".stats_item_value")
            value = value_el.get_text(strip=True) if value_el else ""
            if not value:
                continue
            if icon_ref.endswith("#i-clock"):
                duration = value
            elif icon_ref.endswith("#i-view"):
                views = value

        items.append(
            {
                "url": url,
                "title": _clean_title(title) or "Unknown Video",
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

    path = (parsed.path or "/").rstrip("/")
    q = parse_qsl(parsed.query, keep_blank_values=True)

    if page_num <= 1:
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, path or "/", "", urlencode(q), "")
        )

    # Search paginates via ?page=N; sections via path segment /{name}_{page}/
    if path.startswith("/search"):
        q = [(k, v) for k, v in q if k != "page"] + [("page", str(page_num))]
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, path or "/search/", "", urlencode(q), "")
        )

    # Pagination is a separate numeric path segment: /videos_1/2/, /most-watched-videos_1/2/
    # The trailing _{N} in section URLs is the LIST ID, not the page — never replace it.
    segments = [s for s in path.split("/") if s]
    if segments and re.fullmatch(r"\d+", segments[-1]):
        # URL already carries a page segment → replace it
        segments[-1] = str(page_num)
        new_path = "/" + "/".join(segments) + "/"
    elif segments:
        # Section URL without explicit page (e.g. /most-watched-videos_1/) → append the page
        new_path = "/" + "/".join(segments) + f"/{page_num}/"
    else:
        new_path = f"/videos_1/{page_num}/"

    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", urlencode(dict(q)) if q else "", "")
    )


def _parse_datalists(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Extract Channel/Models/Tags rows from .datalist blocks."""
    out: dict[str, list[str]] = {"channel": [], "models": [], "tags": []}
    for block in soup.select(".datalist"):
        title_el = block.select_one(".datalist_title")
        if not title_el:
            continue
        key = title_el.get_text(" ", strip=True).rstrip(":").lower()
        links = [a.get_text(" ", strip=True) for a in block.select("a.datalist_link")]
        links = [l for l in links if l]
        if key in ("channel", "studio") and links:
            out["channel"] = links
        elif key == "models" and links:
            out["models"] = links
        elif key == "tags" and links:
            out["tags"] = links
    return out


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1.title").get_text(" ", strip=True) if soup.select_one("h1.title") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one(".player img, .media-card img")),
    )

    duration = _format_seconds(_meta(soup, prop="video:duration"))
    if not duration:
        micro = soup.select_one('[itemprop="duration"]')
        if micro:
            duration = micro.get_text(strip=True) or None

    views = None
    views_meta = _meta(soup, prop="ya:ovs:views_total")
    if views_meta:
        views = views_meta

    upload_date = _format_upload_date(_meta(soup, prop="ya:ovs:upload_date"))

    datalists = _parse_datalists(soup)
    tags = datalists.get("tags") or []
    # Fallback: video:tag meta (comma separated)
    if not tags:
        tag_meta = _meta(soup, prop="video:tag")
        if tag_meta:
            tags = [t.strip() for t in tag_meta.split(",") if t.strip()]

    description = _first_non_empty(
        _meta(soup, prop="og:description"), _meta(soup, name="description")
    )

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
        "duration": duration,
        "views": views,
        "uploader_name": (datalists.get("channel") or [None])[0],
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
        raise ValueError(f"Unsupported ShyFap URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _streams_for_page(soup)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
