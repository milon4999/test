from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

# ---------------------------------------------------------------------------
# 85PO network facts (verified 2026-09):
# - 85po.com: original domain, now behind Cloudflare bot protection (403 for
#   server traffic) -> kept only as last-resort host fallback.
# - 85ro.com: mirror, TLS certificate currently expired -> fetch with SSL
#   verification disabled.
# - 85po.net: mirror, valid TLS.
# - Language prefixes: /en/ and /ja/ (zh is served from bare paths). The site
#   redirects bare URLs to /en/ when Accept-Language is en-US.
# - Video pages: /{lang}/video/{id}/{slug}/ (legacy /v/{id}/{slug}/ paths 404).
# - Streams: same-origin /{lang}/get_file/... links 302 -> signed CDN URL.
# - Pagination: path based /{page}/ for latest-updates / top-rated /
#   most-popular; KVS async block params for tag and search pages; the
#   homepage itself has no page 2 (its pagination points at latest-updates).
# ---------------------------------------------------------------------------
SITE_HOSTS = ("85ro.com", "85po.net", "85po.com")
DEFAULT_HOST = "85ro.com"
BLOCKED_HOST = "85po.com"
BASE_SITE = f"https://www.{DEFAULT_HOST}/en/"
DEFAULT_LANG = "en"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0].strip()
    if h.startswith("www."):
        h = h[4:]
    return h


def _is_site_host(host: str) -> bool:
    h = _normalize_host(host)
    return any(h == s or h.endswith(f".{s}") for s in SITE_HOSTS)


def can_handle(host: str) -> bool:
    return _is_site_host(host)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _host_of(url: str) -> Optional[str]:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    return _normalize_host(host) if _is_site_host(host) else None


def _www(host: str) -> str:
    return host if host.startswith("www.") else f"www.{host}"


def _swap_host(url: str, host: str) -> str:
    """Return `url` rewritten onto `host` (https, same path/query)."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    return urlunparse(("https", _www(host), parsed.path or "/", "", parsed.query, ""))


def _lang_of(url_or_path: str) -> str:
    path = urlparse(url_or_path).path or url_or_path
    m = re.match(r"^/([a-z]{2})(?:/|$)", path or "")
    return m.group(1) if m else ""


def _candidate_hosts(url: str) -> list[str]:
    """Hosts to try in order; 85po.com goes last (Cloudflare blocks bots)."""
    preferred = _host_of(url)
    order = [h for h in SITE_HOSTS if h != BLOCKED_HOST]
    if preferred and preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    order.append(BLOCKED_HOST)
    return order


async def fetch_page(url: str, referer: str = BASE_SITE) -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    # ssl=False: 85ro.com currently serves an expired TLS certificate and
    # 85po.com sits behind Cloudflare; neither works with verification on.
    return await pool_fetch_html(url, headers=headers, ssl=False)


async def _fetch_with_fallback(url: str, referer: str = BASE_SITE) -> tuple[str, str]:
    """Fetch `url`, retrying on the other 85PO mirrors. Returns (html, host)."""
    last_error: Optional[Exception] = None
    for host in _candidate_hosts(url):
        candidate = _swap_host(url, host)
        try:
            html = await fetch_page(candidate, referer=referer)
        except Exception as e:  # noqa: BLE001 - any failure moves to the next mirror
            last_error = e
            continue
        return html, host
    raise last_error or RuntimeError(f"Failed to fetch {url}")


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
    for suffix in (
        " - 85PO",
        " | 85PO",
        " â€“ 85PO",
        " - 85po",
        " | 85po",
        " â€“ 85po",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None



def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b", text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d[\d,\.]*\s*[KMBkmb])\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _views_from_eye_icon(container: Any) -> Optional[str]:
    """View counts may sit beside <svg class="icon-eye">."""
    if container is None:
        return None
    for svg in container.select("svg.icon-eye, svg.svg-icon.icon-eye"):
        node = svg.parent
        for _ in range(8):
            if node is None:
                break
            classes = node.get("class") or []
            if isinstance(classes, str):
                classes = [classes]
            if "thumb-item" in classes or "count-item" in classes:
                txt = node.get_text(" ", strip=True)
                if txt:
                    return _normalize_numberish(txt) or _extract_views(txt)
            node = node.parent
    return None


def _views_from_container(container: Any) -> Optional[str]:
    """Cards and detail pages render the view count in a .views div."""
    if container is None:
        return None
    for selector in (".views", ".count"):
        el = container.select_one(selector)
        if el is not None:
            txt = el.get_text(" ", strip=True)
            views = _normalize_numberish(txt) or _extract_views(txt)
            if views:
                return views
    return None


def _duration_from_container(container: Any) -> Optional[str]:
    if container is None:
        return None
    el = container.select_one(".duration")
    if el is not None:
        d = _extract_duration(el.get_text(" ", strip=True))
        if d:
            return d
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


# ---------------------------------------------------------------------------
# Video URL parsing (current: /{lang}/video/{id}/{slug}/; legacy /v/ is dead)
# ---------------------------------------------------------------------------

_VIDEO_PATH_RE = re.compile(r"^/(?:[a-z]{2}/)?video/(\d+)/[^/]+/?$", re.IGNORECASE)
_LEGACY_VIDEO_PATH_RE = re.compile(r"^/(?:[a-z]{2}/)?v/(\d+)/[^/]+/?$", re.IGNORECASE)
_EMBED_PATH_RE = re.compile(r"^/(?:[a-z]{2}/)?embed/(\d+)/?$", re.IGNORECASE)


def _extract_video_id(url: str) -> Optional[str]:
    path = urlparse(url or "").path or ""
    for rx in (_VIDEO_PATH_RE, _LEGACY_VIDEO_PATH_RE, _EMBED_PATH_RE):
        m = rx.match(path)
        if m:
            return m.group(1)
    return None


def _is_video_page_url(url: str) -> bool:
    path = urlparse(url or "").path or ""
    return bool(_VIDEO_PATH_RE.match(path) or _LEGACY_VIDEO_PATH_RE.match(path))


def _is_embed_page_url(url: str) -> bool:
    path = urlparse(url or "").path or ""
    return bool(_EMBED_PATH_RE.match(path))


def _legacy_to_video_url(url: str) -> str:
    """Rewrite the dead /v/{id}/{slug}/ paths to /video/{id}/{slug}/."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return raw
    parsed = urlparse(raw)
    new_path = re.sub(r"^(/(?:[a-z]{2}/)?)v/(\d+)/", r"\1video/\2/", parsed.path or "", flags=re.IGNORECASE)
    if new_path == (parsed.path or ""):
        return raw
    return urlunparse(("https", parsed.netloc, new_path, "", parsed.query, ""))


def _normalize_video_href(href: str, page_url: str = BASE_SITE) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(page_url, href)
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = _normalize_host(parsed.netloc)
    if not _is_site_host(host):
        return None
    path = parsed.path or ""
    if parsed.query:
        return None
    if not (_VIDEO_PATH_RE.match(path) or _LEGACY_VIDEO_PATH_RE.match(path)):
        return None
    path = re.sub(r"^(/(?:[a-z]{2}/)?)v/(\d+)/", r"\1video/\2/", path, flags=re.IGNORECASE)
    if not path.endswith("/"):
        path += "/"
    return urlunparse(("https", _www(host), path, "", "", ""))


def _embed_player_url(video_id: str, page_url: str = BASE_SITE) -> str:
    host = _host_of(page_url) or DEFAULT_HOST
    lang = _lang_of(page_url) or DEFAULT_LANG
    return f"https://www.{host}/{lang}/embed/{video_id}"


def _list_section_id(base_url: str) -> str:
    path = (urlparse(base_url).path or "/").lower()
    path = re.sub(r"^/[a-z]{2}/", "/", path).rstrip("/") or "/"

    # Search results
    if path.startswith("/search"):
        return "list_videos_videos_list_search_result"

    # Tag detail pages (e.g. /tags/kou-jiao/) and community categories
    if path.startswith("/tags/") or path.startswith("/categories/"):
        return "list_videos_common_videos_list"

    # Latest updates (and the removed /4k/) use the primary list block
    if path in ("/latest-updates", "/4k"):
        return "list_videos_latest_videos_list"

    # Rankings / popularity listings
    if path in ("/top-rated", "/most-popular"):
        return "list_videos_common_videos_list"

    # Homepage and other fallbacks
    return "list_videos_most_recent_videos"


def _list_root(soup: BeautifulSoup, base_url: str) -> Any:
    section_id = _list_section_id(base_url)
    block = soup.select_one(f"#{section_id}") or soup.select_one(f"#{section_id}_items")
    if block is not None:
        if (block.get("id") or "").endswith("_items"):
            return block
        inner = block.select_one(f"#{section_id}_items")
        return inner or block
    # KVS async fragments contain a single items container; full pages have
    # several (sidebar blocks), so only trust this fallback when unambiguous.
    items = soup.select("[id$='_items']")
    if len(items) == 1:
        return items[0]
    return None


# ---------------------------------------------------------------------------
# Stream detection
# ---------------------------------------------------------------------------

def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if _is_blocked_stream_url(url):
        return None
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4") or ".mp4?" in low:
        return "mp4"
    return None


def _is_blocked_stream_url(url: str) -> bool:
    """Drop ads, player chrome, and same-site /embed/ page shells (added explicitly)."""
    low = (url or "").lower()
    if _is_non_video_asset_url(url) or _is_probable_ad_iframe(url):
        return True
    if any(x in low for x in ("/player/html.php", "/player/stats.php", "preview.mp4.jpg")):
        return True
    path = urlparse(url).path.lower() if url else ""
    if _EMBED_PATH_RE.match(path):
        return True
    return False


def _is_non_video_asset_url(url: str) -> bool:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")
    if path.endswith(image_exts) or ".mp4.jpg" in low or "preview_preview.mp4.jpg" in low:
        return True
    blocked_markers = (
        "/screenshots/",
        "/thumb/",
        "/thumbs/",
        "/thumbnails/",
        "/poster/",
        "/preview.jpg",
        "/contents/videos_screenshots/",
    )
    return any(marker in low for marker in blocked_markers)


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked = (
        "googlesyndication",
        "doubleclick",
        "adservice",
        "trafficjunky",
        "exoclick",
        "juicyads",
        "adspyglass",
    )
    return any(marker in s for marker in blocked)


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r"https?://(?:www\.)?(?:85ro|85po)\.(?:com|net)(?::\d+)?/(?:[a-z]{2}/)?get_file/[^\s\"'<>]+",
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            u = m.group(0).strip().rstrip(",;)")
            if u and not _is_non_video_asset_url(u):
                urls.append(u)
    return list(dict.fromkeys(urls))


def _get_file_tier_key(url: str) -> Optional[str]:
    """Group /get_file/ variants for the same file (different tokens/query)."""
    low = (url or "").lower()
    m = re.search(r"/(\d+)_(\d{3,4})p\.mp4", low)
    if m:
        return f"{m.group(1)}:{m.group(2)}p"
    m = re.search(r"/(\d+)\.mp4", low)
    if m:
        return f"{m.group(1)}:source"
    return None


def _get_file_url_priority(url: str) -> int:
    """Prefer download links (302 -> CDN). Player tokens can be picky."""
    low = (url or "").lower()
    if "download=true" in low and "download_filename" in low:
        return 100
    if "download=true" in low:
        return 80
    if re.search(r"/get_file/\d+/", low):
        return 50
    if "br=" in low and "download_filename" not in low:
        return 5
    return 20


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    q = re.search(r"_(\d{3,4})p\.mp4", low)
    if q:
        return f"{q.group(1)}p"
    if re.search(r"/\d+\.mp4", low) and not re.search(r"_\d{3,4}p\.mp4", low):
        return "source"
    q = re.search(r"(\d{3,4})p", low)
    if q and "download_filename" in low:
        return f"{q.group(1)}p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    return "source"


def _prefer_playable_get_file_streams(streams: list[dict[str, str]]) -> list[dict[str, str]]:
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if not get_file_mp4:
        return streams
    other = [s for s in streams if s not in get_file_mp4]
    by_tier: dict[str, list[dict[str, str]]] = {}
    for s in get_file_mp4:
        tier = _get_file_tier_key(s["url"] or "") or s["url"] or ""
        by_tier.setdefault(tier, []).append(s)
    picked: list[dict[str, str]] = []
    for items in by_tier.values():
        best = max(items, key=lambda x: _get_file_url_priority(x.get("url") or ""))
        url = best.get("url") or ""
        best["quality"] = _stream_quality_from_url(url)
        picked.append(best)
    return other + picked


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = urljoin(page_url, href)
        if not href.startswith("http") or href in seen:
            continue
        if _is_blocked_stream_url(href):
            continue
        fmt = _detect_media_format(href)
        if not fmt:
            continue
        seen.add(href)
        streams.append({"url": href, "quality": _stream_quality_from_url(href), "format": fmt})

    for video in soup.select("video"):
        src = (video.get("src") or "").strip()
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(page_url, src)
            if not _is_non_video_asset_url(src):
                fmt = _detect_media_format(src)
                if src.startswith("http") and src not in seen and fmt in ("mp4", "hls"):
                    seen.add(src)
                    streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(page_url, src)
            if _is_non_video_asset_url(src):
                continue
            fmt = _detect_media_format(src)
            if not src.startswith("http") or src in seen or fmt not in ("mp4", "hls"):
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        fmt = _detect_media_format(src)
        if fmt not in ("mp4", "hls"):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(page_url, src)
        if not src.startswith("http") or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
    materialized = _prefer_playable_get_file_streams(materialized)
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        m = next((s for s in materialized if s.get("format") == preferred), None)
        if m:
            default_url = m.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)

    video_id = _extract_video_id(page_url)
    if video_id:
        embed_url = _embed_player_url(video_id, page_url)
        if not any(s.get("url") == embed_url for s in materialized):
            materialized.append({"url": embed_url, "quality": "85po", "format": "embed"})

    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    base = get_file_url.split("?", 1)[0].strip().rstrip("/")
    ref = referer.strip() if referer.strip().startswith("http") else BASE_SITE
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def _attempt(url: str, method: str, range_hdr: Optional[str], verify: bool) -> Optional[str]:
        h = dict(headers)
        if range_hdr:
            h["Range"] = range_hdr
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, verify=verify) as client:
            if method == "HEAD":
                resp = await client.head(url, headers=h)
            else:
                resp = await client.get(url, headers=h)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return None
            if _is_non_video_asset_url(loc) or _is_probable_ad_iframe(loc):
                return None
            # A 30x out of /get_file/ points at the signed CDN object (85PO uses
            # e.g. ppxdd.com/remote_control.php?file=... with no .mp4 suffix),
            # so accept any non-asset redirect, not just detectable media URLs.
            if loc.lower().startswith(("http://", "https://")):
                return loc
            fmt = _detect_media_format(loc)
            if fmt in ("mp4", "hls"):
                return loc
        return None

    attempts = [
        (get_file_url, "HEAD", None),
        (get_file_url, "GET", "bytes=0-"),
        (get_file_url, "GET", "bytes=0-0"),
        (f"{base}/", "HEAD", None),
        (f"{base}/", "GET", "bytes=0-"),
        (f"{base}/", "GET", "bytes=0-0"),
        (base, "HEAD", None),
        (base, "GET", "bytes=0-"),
        (base, "GET", "bytes=0-0"),
    ]
    # 85ro.com currently serves an expired TLS certificate: try unverified
    # first there, verified-first everywhere else.
    low = (get_file_url or "").lower()
    verify_order = (False, True) if "85ro.com" in low else (True, False)
    for verify in verify_order:
        for u, method, rng in attempts:
            try:
                resolved = await asyncio.wait_for(_attempt(u, method, rng, verify), timeout=16.0)
                if resolved:
                    return resolved
            except Exception:
                continue
    return None


def _url_contains_video_id(url: str, video_id: str) -> bool:
    low = (url or "").lower()
    vid = str(video_id).lower()
    return (
        f"/{vid}/" in low
        or f"/{vid}." in low
        or f"%2f{vid}%2f" in low
        or f"%2f{vid}.mp4" in low
        or f"{vid}.mp4" in low
        or f"/{vid}_" in low
    )


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if not get_file_mp4:
        return
    video_id = _extract_video_id(referer)

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _get_file_to_remote_playable(stream["url"], referer=referer)
        return stream, resolved

    resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
    for stream, resolved in resolved_pairs:
        if resolved:
            # _get_file_to_remote_playable only returns real redirect targets,
            # which are playable CDN objects even without a media filename.
            stream["url"] = resolved
        # If redirect resolution fails, keep the original /get_file/ URL (playable with Referer).

    mp4_streams = [s for s in streams if s.get("format") == "mp4"]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    def _mp4_score(item: dict[str, str]) -> int:
        q = item.get("quality") or ""
        m = re.search(r"(\d{3,4})", q)
        return int(m.group(1)) if m else 0

    if mp4_streams:
        mp4_streams.sort(key=_mp4_score, reverse=True)
        video["default"] = mp4_streams[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(mp4_streams) or bool(hls) or bool(embed)


def _find_canonical_video_page_url(soup: BeautifulSoup, html: str, video_id: str, page_url: str = BASE_SITE) -> Optional[str]:
    vid = str(video_id).strip()
    if not vid:
        return None
    for a in soup.select("a[href]"):
        href = _normalize_video_href(a.get("href") or "", page_url)
        if href and re.search(rf"/video/{re.escape(vid)}/", href, flags=re.IGNORECASE):
            return href
    m = re.search(
        rf"https?://[^\s\"'<>]*/(?:[a-z]{{2}}/)?video/{re.escape(vid)}/[^\s\"'<>]*/?",
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return _normalize_video_href(m.group(0), page_url)
    return None


def _ensure_embed_stream(video: dict[str, Any], video_id: str, page_url: str = BASE_SITE) -> None:
    """Expose the site's iframe player (e.g. https://www.85ro.com/en/embed/{id})."""
    embed_url = _embed_player_url(video_id, page_url)
    streams: list[dict[str, str]] = video.get("streams") or []
    if any(s.get("url") == embed_url for s in streams):
        return
    streams.append({"url": embed_url, "quality": "85po", "format": "embed"})
    video["streams"] = streams
    if not video.get("default"):
        video["default"] = embed_url
    video["has_video"] = True


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    text_blob = soup.get_text(" ", strip=True)
    duration = _extract_duration(text_blob)
    views = (
        _views_from_eye_icon(soup.select_one(".title-holder"))
        or _views_from_eye_icon(soup.select_one(".col-video"))
        or _views_from_container(soup.select_one(".info, .video-info, .col-video"))
    )
    if not views:
        views_el = soup.select_one(".views")
        views_text = views_el.get_text(" ", strip=True) if views_el else None
        views = _normalize_numberish(views_text) or _extract_views(views_text) or _extract_views(text_blob)

    tags: list[str] = []
    for el in soup.select(".tags a, a[href*='/tags/']"):
        tag = el.get_text(" ", strip=True)
        if tag and len(tag) < 80:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": _meta(soup, prop="article:section"),
        "tags": tags,
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": _extract_streams(soup, html, url),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    raw_url = (url or "").strip()
    url = _legacy_to_video_url(raw_url)
    video_id = _extract_video_id(url)
    html, serving_host = await _fetch_with_fallback(url, referer=BASE_SITE)
    page_url = _swap_host(url, serving_host)
    soup = BeautifulSoup(html, "lxml")

    if _is_embed_page_url(page_url) and video_id:
        canonical = _find_canonical_video_page_url(soup, html, video_id, page_url)
        if canonical:
            try:
                full_html, canonical_host = await _fetch_with_fallback(canonical, referer=page_url)
                page_url = _swap_host(canonical, canonical_host)
                data = parse_video_page(full_html, page_url)
            except Exception:
                data = parse_video_page(html, page_url)
        else:
            data = parse_video_page(html, page_url)
        if video_id:
            _ensure_embed_stream(data.get("video", {}), video_id, page_url)
    else:
        data = parse_video_page(html, page_url)

    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=page_url)

    # When opened via /embed/{id}, prefer the embed player as default if no
    # direct MP4 survived resolve.
    if _is_embed_page_url(raw_url) and video_id:
        video = data.get("video", {})
        mp4s = [s for s in (video.get("streams") or []) if s.get("format") == "mp4"]
        embed_url = _embed_player_url(video_id, page_url)
        if not mp4s:
            video["default"] = embed_url
        video["has_video"] = bool(video.get("streams"))

    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    host = _host_of(raw) or DEFAULT_HOST
    netloc = _www(host)
    path = parsed.path or "/"
    lang = _lang_of(path) or DEFAULT_LANG
    lang_dir = f"/{lang}"

    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for legacy in ("page", "from", "mode", "function", "block_id"):
        query_items.pop(legacy, None)

    # Language-stripped path for section matching (e.g. /en/latest-updates -> /latest-updates)
    bare_path = re.sub(rf"^{re.escape(lang_dir)}/?", "/", path, flags=re.IGNORECASE)
    bare_path = bare_path.rstrip("/") or "/"

    if page <= 1:
        return urlunparse(("https", netloc, path, "", urlencode(query_items), ""))

    # Homepage has no own page 2; the site's pagination points at latest-updates.
    if bare_path == "/":
        return urlunparse(("https", netloc, f"{lang_dir}/latest-updates/{page}/", "", urlencode(query_items), ""))

    # Search pages paginate through the KVS async block API
    if bare_path.startswith("/search"):
        q = ""
        m = re.search(r"/search/([^/]+)/?", bare_path)
        if m:
            q = m.group(1)
        params = dict(query_items)
        params.update(
            {
                "mode": "async",
                "function": "get_block",
                "block_id": "list_videos_videos_list_search_result",
                "q": q,
                "from_videos": str(page),
            }
        )
        return urlunparse(("https", netloc, path, "", urlencode(params), ""))

    # Tag detail pages paginate through the KVS async block API
    if bare_path.startswith("/tags"):
        params = dict(query_items)
        params.update(
            {
                "mode": "async",
                "function": "get_block",
                "block_id": "list_videos_common_videos_list",
                "sort_by": "post_date",
                "from": str(page),
            }
        )
        return urlunparse(("https", netloc, path, "", urlencode(params), ""))

    # latest-updates / top-rated / most-popular / categories: path pagination
    return urlunparse(("https", netloc, f"{path.rstrip('/')}/{page}/", "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html, serving_host = await _fetch_with_fallback(page_url, referer=BASE_SITE)
    except Exception:
        return []

    fetched_url = _swap_host(page_url, serving_host)
    soup = BeautifulSoup(html, "lxml")
    root = _list_root(soup, base_url)
    if root is None:
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in root.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "", fetched_url)
        if not href or href in seen:
            continue

        container = a.find_parent(["article", "li", "div"]) or a
        img = a.find("img") or (container.find("img") if container else None)
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = a.select_one("strong.title, .title")
        title = (
            a.get("title")
            or (img.get("alt") if img else None)
            or (title_el.get_text(" ", strip=True) if title_el else None)
            or a.get_text(" ", strip=True)
        )
        title = _clean_title(title) or "Unknown Video"

        duration = _duration_from_container(a) or _duration_from_container(container)
        if not duration:
            ctext = a.get_text(" ", strip=True)
            duration = _extract_duration(ctext)

        views = _views_from_container(a) or _views_from_eye_icon(a) or _views_from_eye_icon(container)
        if not views:
            views = _views_from_container(container)
        if not views:
            ctext = (container.get_text(" ", strip=True) if container else "") or ""
            views = _extract_views(ctext)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]
