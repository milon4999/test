from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://pornhits.tv/"
SITE_HOST = "pornhits.tv"
SITE_ALIASES = frozenset({"pornhits.tv", "www.pornhits.tv"})
API_BASE = "https://pornhits.tv/api"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?pornhits\.tv/video/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".pornhits.tv")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    """JSON or HTML fetch via curl_cffi (Next.js site; plain aiohttp works but
    impersonation keeps Cloudflare happy)."""
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        for imp in ("chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=30.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.text:
                        return resp.text
            except Exception:
                continue
    try:
        return await pool_fetch_html(url, headers=headers)
    except Exception:
        return ""


async def _fetch_json(url: str) -> Optional[Any]:
    text = await fetch_page(url, referer=BASE_SITE)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    return re.sub(r"\s+", " ", str(title)).strip() or None


def _format_seconds(seconds: Any) -> Optional[str]:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def _format_duration_iso(iso: str | None) -> Optional[str]:
    if not iso:
        return None
    m = _ISO_DURATION_RE.match(str(iso).strip())
    if not m:
        return None
    parts = m.groupdict()
    total = (
        int(parts.get("hours") or 0) * 3600
        + int(parts.get("minutes") or 0) * 60
        + int(parts.get("seconds") or 0)
    )
    return _format_seconds(total)


def _absolute_thumb(url: str | None) -> Optional[str]:
    if not url:
        return None
    url = str(url).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{BASE_SITE.rstrip('/')}{url}"
    return url or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    m = _VIDEO_PAGE_RE.match(href.split("#", 1)[0])
    if not m:
        return None
    return f"https://{SITE_HOST}/video/{m.group('slug')}"


def _api_item_to_list_item(item: dict[str, Any]) -> dict[str, Any]:
    slug = item.get("slug") or ""
    return {
        "url": f"https://{SITE_HOST}/video/{slug}",
        "title": _clean_title(item.get("title")) or "Unknown Video",
        "thumbnail_url": _absolute_thumb(item.get("thumbnailUrl")),
        "duration": _format_seconds(item.get("duration")),
        "views": str(item.get("views")) if item.get("views") is not None else None,
        "uploader_name": None,
        "tags": None,
    }


def _build_api_url(base_url: str) -> tuple[str, bool]:
    """Map a site listing URL to its API endpoint. Returns (api_url, is_search)."""
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    path = (parsed.path or "/").strip().lower()

    if "q" in qs and qs["q"]:
        return f"{API_BASE}/search?{urlencode({'q': qs['q']})}", True

    params: dict[str, str] = {}
    if qs.get("category"):
        params["category"] = qs["category"]
    if qs.get("sort"):
        params["sort"] = qs["sort"]
    if path.startswith("/trending"):
        return f"{API_BASE}/videos/trending", False
    query = urlencode(params) if params else ""
    return f"{API_BASE}/videos{('?' + query) if query else '?sort=newest'}", False


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    api_url, is_search = _build_api_url(base_url)
    page_num = max(1, int(page) if page else 1)

    data = await _fetch_json(api_url)
    if data is None:
        return []

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    # Cursor pagination: walk forward (page-1) hops for the requested page.
    # Search responses have no cursor — only one page is available.
    for _ in range(page_num - 1):
        cursor = data.get("nextCursor") if isinstance(data, dict) else None
        if not cursor:
            return []
        sep = "&" if "?" in api_url else "?"
        data = await _fetch_json(f"{api_url}{sep}cursor={quote(str(cursor), safe='')}")
        if data is None:
            return []
        nxt = data.get("data") if isinstance(data, dict) else data
        if not isinstance(nxt, list):
            return []
        items = nxt

    result = []
    seen: set[str] = set()
    for item in items:
        if len(result) >= limit:
            break
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        li = _api_item_to_list_item(item)
        if li["url"] in seen:
            continue
        seen.add(li["url"])
        result.append(li)
    return result[:limit]


def _parse_json_ld_video(html: str) -> dict[str, Any]:
    """The watch page embeds a schema.org VideoObject (escaped inside the RSC
    payload). Extract name/description/thumbnail/uploadDate/duration/
    contentUrl/views."""
    out: dict[str, Any] = {}
    for m in re.finditer(
        r'\{"@context":"https://schema.org","@type":"VideoObject".*?\}\}', html
    ):
        try:
            raw = m.group(0).encode().decode("unicode_escape")
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("@type") == "VideoObject":
            out = data
            break
    return out


def _parse_meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


async def _search_enrich(title: str) -> dict[str, Any]:
    """Search by title words to recover tags/quality for the watch page."""
    words = re.sub(r"[-_]+", " ", title or "").strip()
    if not words:
        return {}
    data = await _fetch_json(f"{API_BASE}/search?{urlencode({'q': words})}")
    if not isinstance(data, dict):
        return {}
    items = data.get("data") or []
    return items[0] if items and isinstance(items[0], dict) else {}


async def _related_videos(slug: str, title: str, limit: int) -> list[dict[str, Any]]:
    words = re.sub(r"[-_]+", " ", title or "").strip()
    related: list[dict[str, Any]] = []
    if words:
        data = await _fetch_json(f"{API_BASE}/search?{urlencode({'q': words})}")
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("slug") and item["slug"] != slug:
                    related.append(_api_item_to_list_item(item))
    if not related:
        data = await _fetch_json(f"{API_BASE}/videos/trending")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("slug") and item["slug"] != slug:
                    related.append(_api_item_to_list_item(item))
    return related[:limit]


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported PornHits URL: {url}")
    slug = canon.rsplit("/", 1)[-1]

    html = await fetch_page(canon, referer=BASE_SITE)
    if not html:
        raise ValueError(f"Failed to fetch PornHits page: {canon}")

    ld = _parse_json_ld_video(html)
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(ld.get("name"), soup.title.get_text(strip=True) if soup.title else None)
    ) or "Unknown Video"

    description = ld.get("description") or _meta(soup, name="description")
    thumbnail = _absolute_thumb(ld.get("thumbnailUrl"))
    upload_date = ld.get("uploadDate")
    duration = _format_duration_iso(ld.get("duration"))

    views: Optional[str] = None
    stat = ld.get("interactionStatistic")
    if isinstance(stat, dict) and stat.get("userInteractionCount") is not None:
        views = str(stat.get("userInteractionCount"))

    # Streams: contentUrl is a direct unsigned MP4 on cdn.veporn.com
    streams: list[dict[str, str]] = []
    content_url = ld.get("contentUrl")
    if content_url and str(content_url).startswith("http"):
        streams.append({"url": str(content_url), "quality": "source", "format": "mp4"})

    # Enrich via search API: tags + quality + related
    tags: list[str] = []
    enrich = await _search_enrich(slug.rsplit("-", 1)[0] or title)
    if enrich and enrich.get("slug") == slug:
        for t in enrich.get("tags") or []:
            t = str(t).strip()
            if t and t not in tags and len(t) < 60:
                tags.append(t)
        quality = str(enrich.get("quality") or "").strip()
        if quality and streams:
            streams[0]["quality"] = quality
        if enrich.get("duration") and not duration:
            duration = _format_seconds(enrich.get("duration"))
        if enrich.get("views") is not None and not views:
            views = str(enrich.get("views"))

    related = await _related_videos(slug, title, limit=24)

    default = streams[0]["url"] if streams else None
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
        "upload_date": upload_date,
        "video": {
            "streams": streams,
            "hls": None,
            "default": default,
            "has_video": bool(streams),
        },
        "related_videos": related,
    }
