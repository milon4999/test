from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.nuvid.club/"
CANONICAL_HOST = "www.nuvid.club"
SITE_ALIASES = frozenset(
    {
        "nuvid.club",
        "www.nuvid.club",
        "m.nuvid.club",
        "gcdn.nuvid.club",
        "nuvid.com",
        "www.nuvid.com",
        "m.nuvid.com",
        "nuvid.tv",
        "www.nuvid.tv",
        "nuvid.org",
        "www.nuvid.org",
        "nvdst.com",
    }
)
_LIST_HOSTS = frozenset(
    {
        "nuvid.club",
        "www.nuvid.club",
        "m.nuvid.club",
        "nuvid.com",
        "www.nuvid.com",
        "m.nuvid.com",
        "nuvid.tv",
        "www.nuvid.tv",
        "nuvid.org",
        "www.nuvid.org",
    }
)
_IMPERSONATIONS = ("chrome136", "chrome131", "chrome")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.|m\.)?nuvid\.(?:club|com|tv|org))?/video/(?P<id>\d+)(?:/(?P<slug>[^/?#]+))?/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"(?:https?://(?:www\.|m\.)?nuvid\.(?:club|com|tv|org))?/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_CONFIG_VID_RE = re.compile(r"configData\s*:\s*\{[^}]*?\bvid\s*:\s*(?P<id>\d+)", re.IGNORECASE | re.S)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")

_HOME_PAGE_SEGMENTS = frozenset({"", "videos"})


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".nuvid.club") or h.endswith(".nvdst.com"):
        return True
    if h.endswith(".nuvid.com") or h.endswith(".nuvid.tv") or h.endswith(".nuvid.org"):
        return True
    return False


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _fetch_html_sync(url: str, referer: str | None = None) -> str:
    from curl_cffi.requests import Session

    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for impersonate in _IMPERSONATIONS:
        try:
            with Session(impersonate=impersonate) as client:
                resp = client.get(url, headers=headers, timeout=30.0, allow_redirects=True)
            if resp.status_code in (403, 429, 503):
                last_error = RuntimeError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            text = resp.text or ""
            if "just a moment" in text.lower()[:400]:
                last_error = RuntimeError("Cloudflare challenge")
                continue
            return text
        except Exception as e:
            last_error = e
            continue
    raise last_error or RuntimeError(f"Failed to fetch {url}")


async def fetch_html(url: str, *, referer: str | None = None) -> str:
    return await asyncio.to_thread(_fetch_html_sync, url, referer)


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    getter = getattr(el, "get_text", None)
    if callable(getter):
        return getter(strip=True) or None
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


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None
    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            if h > 0:
                return f"{h}:{mm:02d}:{s:02d}"
            return f"{mm}:{s:02d}"
        if v.isdigit():
            return _normalize_duration(int(v))
        found = _DURATION_RE.search(v)
        return found.group(0) if found else None
    return None


def _extract_video_id(url: str, html: str = "") -> Optional[str]:
    for pattern in (_VIDEO_HREF_RE, _EMBED_HREF_RE):
        m = pattern.search(url or "")
        if m:
            return m.group("id")
    m = _CONFIG_VID_RE.search(html or "")
    if m:
        return m.group("id")
    return None


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _https_url(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _normalize_video_href(href: str, *, base: str = BASE_SITE) -> Optional[str]:
    raw = (href or "").strip()
    if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
        return None
    abs_url = urljoin(base, raw)
    parsed = urlparse(abs_url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host and host not in _LIST_HOSTS:
        return None
    m = _VIDEO_HREF_RE.search(parsed.path or "")
    if not m:
        return None
    video_id = m.group("id")
    slug = (m.group("slug") or "").strip("/")
    path = f"/video/{video_id}/{slug}" if slug else f"/video/{video_id}"
    return urlunparse(("https", CANONICAL_HOST, path, "", "", ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("src", "data-src", "data-original", "data-lazy"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if url.lower().endswith(".mp4") or "/media/videos/tmb/" in url.lower() and url.lower().endswith(".webm"):
            continue
        return _https_url(urljoin(BASE_SITE, url))
    return None


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").split("/") if p]


def _build_list_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url or BASE_SITE)
    host = parsed.netloc or CANONICAL_HOST
    parts = _path_parts(parsed.path)
    page_num = max(int(page or 1), 1)

    if parts and parts[0].lower() == "video":
        return urlunparse((parsed.scheme or "https", host, parsed.path or "/", "", parsed.query, ""))

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if not parts or (len(parts) == 1 and parts[0].lower() in _HOME_PAGE_SEGMENTS):
        if page_num <= 1:
            new_path = "/"
        else:
            new_path = f"/videos/{page_num}"
    elif page_num <= 1:
        new_path = "/" + "/".join(parts)
    else:
        new_path = "/" + "/".join(parts + [str(page_num)])

    query = "&".join(f"{k}={v}" for k, v in parse_qsl(parsed.query, keep_blank_values=True))
    return urlunparse((parsed.scheme or "https", host, new_path, "", query, ""))


def _embed_url(video_id: str) -> str:
    return f"https://{CANONICAL_HOST}/embed/{video_id}"


def _embed_streams(video_id: Optional[str]) -> dict[str, Any]:
    vid = (video_id or "").strip()
    if not vid:
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    embed = _embed_url(vid)
    return {
        "streams": [{"url": embed, "quality": "Server 1", "format": "embed"}],
        "hls": None,
        "default": embed,
        "has_video": True,
    }


def _parse_card(block: Any, *, base: str, exclude_url: str | None = None) -> Optional[dict[str, Any]]:
    href = ""
    title = None
    if getattr(block, "name", None) == "a":
        href = block.get("href") or ""
        title = block.get("title")
        link = block
    else:
        link = block.select_one("a.vid_link, a[href*='/video/']") if hasattr(block, "select_one") else None
        if link is None:
            return None
        href = link.get("href") or ""
        title = link.get("title")
    abs_url = _normalize_video_href(href, base=base)
    if not abs_url:
        return None
    if exclude_url and abs_url.rstrip("/") == exclude_url.rstrip("/"):
        return None

    img = (link.find("img") if link is not None else None) or (block.find("img") if hasattr(block, "find") else None)
    thumb = _best_image_url(img)
    title = _first_non_empty(
        title,
        img.get("alt") if img else None,
        _text(block.select_one(".title") if hasattr(block, "select_one") else None),
    ) or "Unknown Video"
    duration = _normalize_duration(_text(block.select_one("i.time") if hasattr(block, "select_one") else None))
    if not duration:
        found = _DURATION_RE.search(str(block))
        if found:
            duration = _normalize_duration(found.group(0))
    return {
        "url": abs_url,
        "title": title.strip(),
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": None,
    }


def _parse_cards(
    soup: BeautifulSoup,
    *,
    base: str,
    exclude_url: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in soup.select(
        "#search_results a.vid_link, a.th.video-thumb.vid_link, a.th.vid_link, "
        ".box-tumb a.vid_link, a.related_vid[href*='/video/']"
    ):
        card = _parse_card(block, base=base, exclude_url=exclude_url)
        if not card or card["url"] in seen:
            continue
        seen.add(card["url"])
        items.append(card)
        if limit and len(items) >= limit:
            break
    return items


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url, html)
    title = _first_non_empty(
        _meta(soup, prop="og:title"),
        _text(soup.select_one("h1")),
        _text(soup.select_one(".discription")),
        _text(soup.find("title")),
    )
    if title:
        for suffix in (" at Nuvid", " | Nuvid", " - Nuvid"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _meta(soup, prop="og:image")
    if thumbnail:
        thumbnail = _https_url(str(thumbnail))
    duration = _normalize_duration(_text(soup.select_one(".runtime")))
    tags: list[str] = []
    for a in soup.select(".video-cat a, .tags-box a.button2"):
        name = a.get("title") or _text(a)
        if name:
            tags.append(name.strip())
    tags = list(dict.fromkeys(tags))
    category = tags[0] if tags else None
    uploader = _text(soup.select_one(".add-by a"))
    related = _parse_cards(soup, base=url, exclude_url=url, limit=12)
    video = _embed_streams(video_id)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "category": category,
        "tags": tags,
        "upload_date": None,
        "related_videos": related,
        "video": video,
    }


async def scrape(url: str) -> dict[str, Any]:
    fetch_url = url
    video_id = _extract_video_id(url)
    if _is_embed_url(url) and video_id:
        fetch_url = f"{BASE_SITE}video/{video_id}"
    else:
        normalized = _normalize_video_href(url)
        if normalized:
            fetch_url = normalized

    html = await fetch_html(fetch_url, referer=BASE_SITE)
    return parse_page(html, fetch_url)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url or BASE_SITE, page)
    html = await fetch_html(page_url, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    items = _parse_cards(soup, base=page_url, limit=limit)
    if not items:
        seen: set[str] = set()
        for m in _VIDEO_HREF_RE.finditer(html or ""):
            href = _normalize_video_href(m.group(0), base=page_url)
            if not href or href in seen:
                continue
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )
            if limit and len(items) >= limit:
                break
    return items[:limit] if limit else items
