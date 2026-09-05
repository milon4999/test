from __future__ import annotations

import json
import os
import re
import ssl
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://siska.tv/"
SITE_HOST = "siska.tv"
SITE_ALIASES = frozenset({"siska.tv", "www.siska.tv"})

# The site's TLS certificate has expired; fetch with verification disabled.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?siska\.tv/video\.php\?videoID=(?P<vid>\d+)/?$",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".siska.tv")


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
    return await pool_fetch_html(url, headers=headers, ssl=_SSL_CTX)


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
    t = str(title).strip()
    for suffix in (" | siska.tv", " \u00bb WATCH FREE VIDEO HD"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_duration(text: str | None) -> Optional[str]:
    """
    Normalize the site's spaced duration format. Two shapes exist:
    `34 : 12` (MM:SS) and `134 : 22` (minutes can exceed 59, e.g. 2:14:22).
    Returns canonical `MM:SS` / `H:MM:SS`.
    """
    if not text:
        return None
    parts = [p.strip() for p in str(text).strip().split(":") if p.strip()]
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        total = int(parts[0]) * 60 + int(parts[1])
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{int(parts[0])}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0]
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    m = _VIDEO_PAGE_RE.match(href if href.endswith("/") else href)
    if not m:
        return None
    return f"https://{SITE_HOST}/video.php?videoID={m.group('vid')}"


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
        for x in (
            "xads",
            "pemsrv",
            "tsyndicate",
            "magsrv",
            "googlesyndication",
            "doubleclick",
            "adserver",
        )
    )


_EMBED_HOSTS = ("playmogo.com", "luluvid.com", "playmate.to")


def _embed_streams(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Collect ALL player iframes from .playerplace / .videoholder — the site
    lists 2-3 servers (playmogo.com, luluvid.com, playmate.to) per video and
    the default one often errors, so every server is returned.
    """
    embed_urls: list[str] = []
    for iframe in soup.select(".playerplace iframe[src], .videoholder iframe[src]"):
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
    for u in embed_urls:
        low = u.lower()
        label = next((h for h in _EMBED_HOSTS if h in low), None)
        quality = label.split(".")[0].capitalize() if label else "Server"
        streams.append({"url": u, "quality": quality, "format": "embed"})

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

    for block in soup.select(".thumb .rel"):
        if len(items) >= limit:
            break
        link = block.select_one("a.video-thumb[href*='videoID=']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = _clean_title(_first_non_empty(link.get("title")))
        if not title:
            img = block.select_one("img")
            if img and img.get("alt"):
                title = _clean_title(str(img.get("alt")).strip())
        if not title:
            parent = block.find_parent("div", class_="back")
            title_el = parent.select_one("h3.title_desc") if parent else None
            if title_el:
                title = _clean_title(title_el.get_text(" ", strip=True))
        img = block.select_one("img")
        dur_el = block.select_one("span.th_video_duration")

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": _normalize_duration(dur_el.get_text(strip=True) if dur_el else None),
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

    if page_num <= 1:
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, parsed.path or "/", "", parsed.query, "")
        )

    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q["page"] = str(page_num)
    return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, parsed.path or "/", "", urlencode(q), ""))


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
        _itemprop(soup, "thumbnailUrl"),
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one(".video-description img, article img, img")),
    )

    duration = _normalize_duration(_itemprop(soup, "duration"))
    if not duration:
        info_el = soup.select_one(".video-info")
        if info_el:
            for p in info_el.find_all("p"):
                text = p.get_text(" ", strip=True)
                if text.lower().startswith("duration:"):
                    duration = _normalize_duration(text.split(":", 1)[-1])
                    break

    tags: list[str] = []
    for a in soup.select(".video-info a[href*='search.php?s='], .video-info a[rel='tag']"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 60:
            tags.append(tag)

    # Studio (e.g. Vixen.com) from the Movie Info block
    uploader = None
    studio_el = soup.select_one(".video-info a[href*='chanells.php']")
    if studio_el:
        uploader = studio_el.get_text(strip=True) or None

    description = None
    desc_el = soup.select_one(".video-description h3")
    if desc_el:
        description = desc_el.get_text(" ", strip=True) or None

    upload_date = _itemprop(soup, "datePublished")

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
        raise ValueError(f"Unsupported Siska URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _embed_streams(soup)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
