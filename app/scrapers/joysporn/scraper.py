from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://joysporn.io/"
SITE_HOST = "joysporn.io"
SITE_ALIASES = frozenset({"joysporn.io", "www.joysporn.io"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_VIDEO_HREF_RE = re.compile(
    r"href=\"(https://joysporn\.io/view/\d+)\"", re.IGNORECASE
)
_BLOB_RE = re.compile(r'data-c="([^"]+)"')
_DATA_N_RE = re.compile(r'data-n="([^"]+)"', re.IGNORECASE)
_THUMB_FOLDER_RE = re.compile(r"/contents/videos_screenshots/(\d+)/(\d+)/")
_VID_NUM_RE = re.compile(r"/view/(\d+)")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".joysporn.io")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _headers(referer: str | None = None) -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return headers


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    """joysporn serves EMPTY 200 bodies to plain aiohttp — curl_cffi required."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        for imp in ("chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, timeout=40.0) as client:
                    resp = await client.get(url, headers=_headers(referer))
                    if resp.status_code == 200 and len(resp.text) > 2000:
                        return resp.text
            except Exception:
                await asyncio.sleep(1.0)
    try:
        return await pool_fetch_html(url, headers=_headers(referer))
    except Exception:
        return ""


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    if t.endswith(" - JoysPorn"):
        t = t[: -len(" - JoysPorn")].strip()
    return t or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    parsed = urlparse(href.split("#", 1)[0])
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    m = re.match(r"^/view/(\d+)/?$", parsed.path or "")
    if not m:
        return None
    return f"https://{SITE_HOST}/view/{m.group(1)}"


def _build_stream_url(
    *,
    node: str,
    timestamp: str,
    token: str,
    folder: str,
    video_num: str,
    quality: str,
) -> str:
    """`data-c` blob -> https://d{node}.vstor.top/whlvid/{t}/{token}/{folder}/{vid}/{vid}_{q}.mp4/{q}.mp4"""
    return (
        f"https://{node}.vstor.top/whlvid/{timestamp}/{token}/"
        f"{folder}/{video_num}/{video_num}_{quality}.mp4/{quality}.mp4"
    )


def _streams_from_html(html: str, page_url: str) -> dict[str, Any]:
    """
    The video page stores each quality in a `data-c` blob:
        {md5};{quality};{size};{serverN};{videoNum};{timestamp};{token};{node}
    plus `data-n` (node, e.g. `d4`) on the container. The stream URL is
    constructed as https://d{node}.vstor.top/whlvid/{t}/{token}/{folder}/{vid}/{vid}_{q}.mp4/{q}.mp4
    where `folder` comes from the thumbnail path (/contents/videos_screenshots/{folder}/{vid}/...).
    """
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    folder, video_num = "0", "0"
    tm = _THUMB_FOLDER_RE.search(html)
    if tm:
        folder, video_num = tm.group(1), tm.group(2)
    nm = _VID_NUM_RE.search(page_url)
    if nm:
        video_num = nm.group(1)

    node_match = _DATA_N_RE.search(html)
    node_default = node_match.group(1) if node_match else "d4"

    for blob in _BLOB_RE.findall(html):
        parts = [p.strip() for p in blob.split(";") if p.strip()]
        if len(parts) < 7:
            continue
        quality = parts[1].lower().replace(" ", "")
        folder_num = parts[3]
        vid_num = parts[4]
        timestamp = parts[5]
        token = parts[6]
        node = parts[7] if len(parts) >= 8 else node_default

        url = _build_stream_url(
            node=node,
            timestamp=timestamp,
            token=token,
            folder=folder_num or folder,
            video_num=vid_num or video_num,
            quality=quality,
        )
        if url in seen:
            continue
        seen.add(url)

        qm = re.search(r"(\d{3,4})p", quality)
        label = qm.group(0) if qm else quality
        streams.append({"url": url, "quality": label, "format": "mp4"})

    # Sort highest-first by numeric quality
    def _q(s: dict[str, str]) -> int:
        digits = "".join(ch for ch in s.get("quality", "") if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=_q, reverse=True)
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

    for block in soup.select("#video_preview .video_c"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/view/"]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        tm = block.select_one("h2.vidtitle")
        if tm:
            title = _clean_title(tm.get_text(" ", strip=True))
        if not title:
            title = _clean_title(link.get("title"))
        img = block.select_one("img")
        dur_el = block.select_one(".vidduration")
        views_el = block.select_one(".views")

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(" ", strip=True) if dur_el else None,
                "views": views_el.get_text(strip=True) if views_el else None,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


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


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/").rstrip("/") or "/"

    if page_num <= 1:
        return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))

    # DLE pagination: /latest/page/{n}/ — strip existing page segment
    path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"
    new_path = f"{path}/page/{page_num}/" if path != "/" else f"/latest/page/{page_num}/"
    return urlunparse((parsed.scheme or "https", parsed.netloc, new_path, "", "", ""))


def _parse_views(views_text: str | None) -> Optional[str]:
    if not views_text:
        return None
    digits = re.sub(r"[^\d]", "", str(views_text))
    return digits or None


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _meta(soup, name="description")

    thumbnail = _best_image_url(soup.select_one(".vidimage img"))

    views: Optional[str] = None
    for span in soup.select(".vid_info ul li span, .vid_info span"):
        txt = span.get_text(" ", strip=True)
        if txt.lower().startswith("viewed:"):
            views = _parse_views(txt.split(":", 1)[-1])
            break
    if not views:
        vm = re.search(r"Viewed:\s*([\d\s]+)", html)
        if vm:
            views = _parse_views(vm.group(1))

    # Duration from the "Duration:" meta line (HTML tags may sit between)
    duration: Optional[str] = None
    dm = re.search(r"Duration:?\s*(?:<[^>]*>\s*)*(\d{1,2}:?\d{2}(?::\d{2})?)", html)
    if dm:
        duration = dm.group(1).strip()

    tags: list[str] = []
    for a in soup.select("a[href*='/tags/'], a[rel='tag']"):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and len(t) < 60:
            tags.append(t)

    related = _parse_list_items(soup, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or _streams_from_html(html, canon)

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
        raise ValueError(f"Unsupported JoysPorn URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    return parse_video_page(html, canon)


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
