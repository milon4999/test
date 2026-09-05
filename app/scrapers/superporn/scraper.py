from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.superporn.com/"
SITE_HOST = "www.superporn.com"
SITE_HOST_NAKED = "superporn.com"
SITE_ALIASES = frozenset({"superporn.com", "www.superporn.com"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?superporn\.com/video/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_EMBED_RE = re.compile(
    r"https://www\.superporn\.com/embed/(\d+)",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".superporn.com")


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
    for suffix in (" - SuperPorn", " | SuperPorn"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    """Keep the view count exactly as the site renders it (e.g. `218.8k`)."""
    if not text:
        return None
    t = str(text).strip()
    return t or None


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
    return f"https://{SITE_HOST}/video/{slug}"


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


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    return any(
        x in s
        for x in ("pemsrv", "tsyndicate", "magsrv", "googlesyndication", "doubleclick")
    )


def _streams_for_page(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Stream extraction. The site's video-js player carries a direct MP4
    `<source>` on cdnst.superporn.com, but the `?secure=` token is IP/UA-bound
    and short-lived, so the direct URL is unreliable from the server side.
    Prefer the stable `https://www.superporn.com/embed/{videoId}` iframe.
    """
    embed_url: Optional[str] = None

    # 1) embed URL from the share code input / JSON-LD embedUrl
    embed_input = soup.select_one("#code-embed input[name='video-embed-field']")
    if embed_input:
        m = _EMBED_RE.search(embed_input.get("value") or "")
        if m:
            embed_url = f"https://www.superporn.com/embed/{m.group(1)}"

    # 2) embed URL from the view-later button's video id (data-video-id)
    if not embed_url:
        votes = soup.select_one(".votos-thumbs[data-video-id]")
        if votes:
            vid = (votes.get("data-video-id") or "").strip()
            if vid.isdigit():
                embed_url = f"https://www.superporn.com/embed/{vid}"

    # 3) the player's video id attribute as a last resort
    if not embed_url:
        player = soup.select_one("video[data-stats-video-id]")
        if player:
            vid = (player.get("data-stats-video-id") or "").strip()
            if vid.isdigit():
                embed_url = f"https://www.superporn.com/embed/{vid}"

    if embed_url:
        return {
            "streams": [{"url": embed_url, "quality": "Server 1", "format": "embed"}],
            "hls": None,
            "default": embed_url,
            "has_video": True,
        }

    # 4) no embed resolved: expose the direct MP4 source if present (best effort)
    source = soup.select_one("video source[src*='cdnst.superporn.com']")
    if source and source.get("src"):
        direct = str(source.get("src")).strip()
        if direct.startswith("http"):
            return {
                "streams": [{"url": direct, "quality": "source", "format": "mp4"}],
                "hls": None,
                "default": direct,
                "has_video": True,
            }

    return {"streams": [], "hls": None, "default": None, "has_video": False}


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select(".thumb-video"):
        if len(items) >= limit:
            break
        link = block.select_one("a.thumb-duracion[href*='/video/']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        title_el = block.select_one("h3 a.thumb-video__description")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        img = block.select_one("img")
        dur_el = block.select_one("span.duracion")
        views_el = block.select_one(".thumb-video-views span:last-child")
        if views_el is None:
            views_el = block.select_one(".thumb-video-views")
        uploader_el = block.select_one("a.info-uploader")

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
                "uploader_name": uploader_el.get_text(strip=True) if uploader_el else None,
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

    # Sections that paginate via ?page=N (home, search, order-filtered lists)
    path = (parsed.path or "/").rstrip("/") or "/"
    if path in ("/", "") or path.startswith("/search") or "page" in q:
        q["page"] = str(page_num)
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", urlencode(q), ""))

    # Category/pornstar/series sections paginate via a numeric path segment: /anal/2
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
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _best_image_url(soup.select_one("video#superporn_player")),
        _best_image_url(soup.select_one("img#video-poster")),
    )

    duration: Optional[str] = None
    player = soup.select_one("video[data-video-duration]")
    if player:
        try:
            total = int(str(player.get("data-video-duration")).strip())
            h, rem = divmod(total, 3600)
            mi, s = divmod(rem, 60)
            duration = f"{h}:{mi:02d}:{s:02d}" if h > 0 else f"{mi:02d}:{s:02d}"
        except (ValueError, TypeError):
            duration = None

    views = None
    views_el = soup.select_one("#n-views")
    if views_el:
        views = _normalize_views(views_el.get_text())

    uploader = None
    uploader_el = soup.select_one(".view-more-less a.info-uploader[href*='/user/']")
    if uploader_el:
        uploader = uploader_el.get_text(strip=True) or None

    tags: list[str] = []
    for a in soup.select("ul.catlist a.chip-link"):
        tag = a.get_text(" ", strip=True)
        if tag and tag not in tags and len(tag) < 50:
            tags.append(tag)

    description = _first_non_empty(
        _meta(soup, prop="og:description"), _meta(soup, name="description")
    )
    # og:description has a " - SuperPorn" tail
    if description and description.endswith(" - SuperPorn"):
        description = description[: -len(" - SuperPorn")].strip()

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
        raise ValueError(f"Unsupported SuperPorn URL: {url}")

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
