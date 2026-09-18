from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse

from app.core.pool import fetch_json

SITE = "hdzog.com"
BASE = f"https://{SITE}"
SITE_ALIASES = frozenset({"hdzog.com", "www.hdzog.com", "tn.hdzog.com", "hdzog.ahcdn.com"})

_ALPHABET = "АВСDЕFGHIJKLМNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,~"
_ALLOWED_CHARS = set(_ALPHABET) | set("АВСЕМ")
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}

_SORTS = ("latest-updates", "longest", "most-commented", "most-popular", "top-rated", "relevance")
_VIDEO_PATH_RE = re.compile(r"^/videos?/(\d+)(?:/([^/]+))?/?$", re.IGNORECASE)
_EMBED_PATH_RE = re.compile(r"^/embed/(\d+)/?$", re.IGNORECASE)
_QUALITY_RE = re.compile(
    r"_(tr|sd|hd|uhd|4k|720p|1080p|360p|480p|240p)(?:\.mp4)?(?:/|$|\?)",
    re.IGNORECASE,
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}


def _base164_decode(encoded: str) -> str:
    """Magma player cipher: Cyrillic-lookalike alphabet, '~' as padding."""
    s = "".join(c for c in (encoded or "") if c in _ALLOWED_CHARS)
    out: list[int] = []
    pos = 0
    n = len(s)
    while pos < n:
        chars = []
        for k in range(4):
            if pos + k < n:
                chars.append(_INDEX.get(s[pos + k], -1))
            else:
                chars.append(-1)
        a, b, c, d = chars
        if a < 0 or b < 0:
            pos += 4
            continue
        out.append((a << 2) | (b >> 4))
        if c != 64 and c >= 0:
            out.append(((15 & b) << 4) | (c >> 2))
            if d != 64 and d >= 0:
                out.append(((3 & c) << 6) | d)
        pos += 4
    return "".join(chr(x & 0xFF) for x in out)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hdzog.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (" | hdzog.com", " - hdzog.com", " | HDZog", " - HDZog"):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    return t or None


def _video_page_url(video_id: str, dir_: str) -> str:
    return f"{BASE}/videos/{video_id}/{dir_}/"


def _video_bucket_path(video_id: str) -> str:
    vid = int(video_id)
    return f"{int(1e6 * (vid // 1e6))}/{1000 * (vid // 1000)}"


def _normalize_video_url(url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    if not url.startswith("http"):
        url = BASE + (url if url.startswith("/") else f"/{url}")
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "hdzog.com" not in host:
        return None
    path = parsed.path or "/"
    m = _VIDEO_PATH_RE.match(path)
    if m:
        video_id, slug = m.group(1), (m.group(2) or "").strip()
        if slug:
            return _video_page_url(video_id, slug)
        return f"{BASE}/videos/{video_id}/"
    m = _EMBED_PATH_RE.match(path)
    if m:
        return f"{BASE}/videos/{m.group(1)}/"
    return None


def _parse_video_id(url: str) -> Optional[str]:
    m = re.search(r"/(?:videos?|embed)/(\d+)(?:/|$)", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _format_duration(raw: Any) -> Optional[str]:
    if not raw:
        return None
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$", str(raw).strip())
    if not m:
        return None
    h, mnt, sec = m.group(1), m.group(2), m.group(3)
    if h:
        return f"{int(h):02d}:{int(mnt):02d}:{sec}"
    return f"{int(mnt):02d}:{sec}"


def _normalize_views(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(int(float(value)))
    except Exception:
        text = str(value)
        return text if text.strip() else None


def _quality_from(fmt: str, path: str) -> str:
    blob = f"{fmt} {path}"
    m = _QUALITY_RE.search(blob)
    return m.group(1).lower() if m else "source"


def _api_videos_url(
    *,
    sort: str = "latest-updates",
    page: int = 1,
    count: int = 100,
    section: str = "",
    object_id: str = "",
    search: str = "",
    gender: str = "str",
) -> str:
    sort = sort if sort in _SORTS else "latest-updates"
    page = max(1, int(page or 1))
    count = min(200, max(1, int(count or 100)))
    if section == "search" or search:
        return (
            f"{BASE}/api/videos2.php?params=86400/{gender}/relevance/{count}/"
            f"search.0.{page}.all.all.all&s={quote_plus(search or '')}"
        )
    sec = section or "0"
    oid = object_id or "0"
    return (
        f"{BASE}/api/json/videos2/86400/{gender}/{sort}/{count}/"
        f"{sec}.{oid}.{page}.all.all.all.json"
    )


def _video_item(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    video_id = str(raw.get("video_id") or "").strip()
    dir_ = str(raw.get("dir") or "").strip()
    if not video_id or not dir_:
        return None
    stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
    return {
        "url": _video_page_url(video_id, dir_),
        "title": _clean_title(raw.get("title")) or "Unknown Video",
        "thumbnail_url": _first_non_empty(raw.get("scr"), raw.get("thumb")),
        "duration": _format_duration(raw.get("duration")),
        "views": _normalize_views(_first_non_empty(raw.get("video_viewed"), stats.get("viewed"))),
        "uploader_name": _first_non_empty(raw.get("display_name"), raw.get("username")),
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    raw = (base_url or "").strip()
    if not raw:
        raw = BASE + "/"
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if "hdzog.com" not in host:
        return []
    path = parsed.path or "/"
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    page = max(1, int(page or 1))
    limit = min(200, max(1, int(limit or 100)))

    sort = "latest-updates"
    for s in _SORTS:
        if f"/{s}/" in path or path.rstrip("/").endswith(f"/{s}"):
            sort = s
            break

    section = ""
    object_id = ""
    search = (qs.get("s") or qs.get("q") or "").strip()

    if _VIDEO_PATH_RE.match(path) or _EMBED_PATH_RE.match(path):
        path = "/"

    m = re.search(r"/search/(?:\d+/)?", path)
    if m or search:
        section = "search"
        if not search:
            m_term = re.search(r"/search/(?:(\d+)/)?([^/?#]+)/?", path)
            if m_term and m_term.group(2) and not str(m_term.group(2)).isdigit():
                search = m_term.group(2)
    else:
        m = re.search(r"/(?:categories|c)/([^/]+)/?", path)
        if m:
            section = "categories"
            object_id = m.group(1)
        else:
            m = re.search(r"/(?:pornstar|models?)/([^/]+)/?", path)
            if m:
                section = "model"
                object_id = m.group(1)
            else:
                m = re.search(r"/(?:pornsite|channels?)/([^/]+)/?", path)
                if m:
                    section = "channel"
                    object_id = m.group(1)

    api_url = _api_videos_url(
        sort=sort,
        page=page,
        count=limit,
        section=section,
        object_id=object_id,
        search=search,
    )

    try:
        data = await fetch_json(api_url, headers={"Referer": BASE + "/"})
    except Exception:
        return []

    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in videos:
        if not isinstance(raw_item, dict) or len(items) >= limit:
            break
        item = _video_item(raw_item)
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)
    return items


async def _fetch_video_detail(video_id: str) -> Optional[dict[str, Any]]:
    api_url = f"{BASE}/api/json/video/86400/{_video_bucket_path(video_id)}/{video_id}.json"
    try:
        data = await fetch_json(api_url, headers={"Referer": BASE + "/"})
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    video = data.get("video")
    return video if isinstance(video, dict) else None


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": referer,
        "Accept": "*/*",
        "Range": "bytes=0-1",
    }
    try:
        async with AsyncSession(impersonate="chrome136", timeout=20.0) as client:
            resp = await client.get(get_file_url, headers=headers, allow_redirects=False)
            loc = resp.headers.get("Location") or resp.headers.get("location")
            if loc:
                if loc.startswith("//"):
                    loc = f"https:{loc}"
                elif loc.startswith("/"):
                    loc = BASE + loc
                if loc.startswith("http://"):
                    loc = "https://" + loc[7:]
                return loc
    except Exception:
        return None
    return None


async def _fetch_videofile_streams(video_id: str, *, referer: str) -> list[dict[str, str]]:
    api_url = f"{BASE}/api/videofile.php?{urlencode({'video_id': video_id, 'lifetime': '8640000'})}"
    try:
        data = await fetch_json(api_url, headers={"Referer": referer})
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    pending: list[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        encoded = entry.get("video_url")
        if not encoded:
            continue
        path = _base164_decode(str(encoded))
        if not path or not path.startswith("/get_file/"):
            continue
        fmt = str(entry.get("format") or "").strip()
        pending.append({
            "url": BASE + path,
            "quality": _quality_from(fmt, path),
            "format": "mp4",
        })
    if not pending:
        return []

    async def _resolve_one(stream: dict[str, str]) -> dict[str, str]:
        resolved = await _resolve_get_file_url(stream["url"], referer=referer)
        if resolved:
            stream["url"] = resolved
        return stream

    resolved_streams = await asyncio.gather(*[_resolve_one(s) for s in pending])
    return [s for s in resolved_streams if s.get("url")]


async def scrape(url: str) -> dict[str, Any]:
    canonical = _normalize_video_url(url)
    if not canonical:
        raise ValueError(f"Unsupported {SITE} video URL: {url}")

    video_id = _parse_video_id(canonical) or ""
    detail = await _fetch_video_detail(video_id) if video_id else None
    if not detail:
        raise ValueError(f"Video not found on {SITE}: {canonical}")

    dir_ = str(detail.get("dir") or "").strip()
    if dir_:
        canonical = _video_page_url(video_id, dir_)

    title = _clean_title(detail.get("title")) or "Unknown Video"
    description = _first_non_empty(detail.get("description")) or None
    thumbnail = _first_non_empty(detail.get("thumbsrc"), detail.get("thumb"), detail.get("scr"))
    duration = _format_duration(detail.get("duration"))

    stats = detail.get("statistics") if isinstance(detail.get("statistics"), dict) else {}
    views = _normalize_views(_first_non_empty(stats.get("viewed"), detail.get("video_viewed")))

    uploader_name = None
    user = detail.get("user")
    if isinstance(user, dict):
        uploader_name = _first_non_empty(user.get("username"), user.get("display_name"))
    if not uploader_name:
        uploader_name = _first_non_empty(detail.get("display_name"), detail.get("username"))

    tags: list[str] = []
    for key in ("categories", "tags", "models"):
        block = detail.get(key)
        if isinstance(block, dict):
            for entry in block.values():
                if isinstance(entry, dict) and entry.get("title"):
                    tags.append(str(entry["title"]).strip())
    tags = list(dict.fromkeys([t for t in tags if t]))

    upload_date = _first_non_empty(detail.get("post_date"))
    streams = await _fetch_videofile_streams(video_id, referer=canonical)
    default_url = streams[0].get("url") if streams else None

    return {
        "url": canonical,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": None,
        "tags": tags,
        "upload_date": upload_date,
        "video": {
            "streams": streams,
            "hls": None,
            "default": default_url,
            "has_video": bool(streams),
        },
        "related_videos": [],
        "preview_url": _first_non_empty(detail.get("thumbsrc")) if detail.get("thumbsrc") != thumbnail else None,
    }
