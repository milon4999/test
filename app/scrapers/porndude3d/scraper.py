from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

# ---------------------------------------------------------------------------
# 3DPornDude network facts (verified 2026-10):
# - KVS-style tube; canonical video pages: /video/{id}/{slug}/
# - Player: inline `var flashvars = {...}` exposes direct get_file MP4 URLs
#   (https://3dporndude.com/get_file/{slot}/{token}/{bucket}/{id}/{id}_{NNN}p.mp4/
#   ?v-acctoken=...) plus a base `{id}.mp4` tier. No HLS.
# - Tokens are time-limited; always re-fetch the page for fresh URLs.
# - Metadata lives in JSON-LD VideoObject + og/video: metas (duration and
#   uploadDate are NOT rendered in the visible body).
# - Listing pagination is path based: /latest-updates/{page}/,
#   /categories/{slug}/{page}/, /search/{q}/{page}/.
# - A CSS-only age gate (tpd_age_confirmed cookie) hides content in browsers
#   but the HTML is fully served to a plain HTTP client; the cookie is still
#   sent to mirror the interactive page.
# ---------------------------------------------------------------------------
SITE_HOST = "3dporndude.com"
SITE_ALIASES = frozenset({"3dporndude.com", "www.3dporndude.com"})
BASE_SITE = "https://3dporndude.com/"

_VIDEO_PATH_RE = re.compile(r"^/video/(\d+)/[^/]+/?$", re.IGNORECASE)
_EMBED_PATH_RE = re.compile(r"^/embed/(\d+)/?$", re.IGNORECASE)
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "tpd_age_confirmed=1",
}


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0].strip()
    if h.startswith("www."):
        h = h[4:]
    return h


def can_handle(host: str) -> bool:
    h = _normalize_host(host)
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        for imp in ("chrome124", "chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=30.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.text) > 2000:
                        return resp.text
            except Exception:
                continue
    try:
        return await pool_fetch_html(url, headers=headers)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _host_of(url: str) -> Optional[str]:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    return _normalize_host(host) if can_handle(host) else None


def _is_site_url(url: str) -> bool:
    return _host_of(url) is not None


def _absolute(url: str, page_url: str = BASE_SITE) -> Optional[str]:
    u = (url or "").strip()
    if not u or u.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return None
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(page_url, u)
    return u if u.startswith("http") else None


def _extract_video_id(url: str) -> Optional[str]:
    path = urlparse(url or "").path or ""
    for rx in (_VIDEO_PATH_RE, _EMBED_PATH_RE):
        m = rx.match(path)
        if m:
            return m.group(1)
    return None


def _is_video_page_url(url: str) -> bool:
    return bool(_VIDEO_PATH_RE.match(urlparse(url or "").path or ""))


def _is_embed_page_url(url: str) -> bool:
    return bool(_EMBED_PATH_RE.match(urlparse(url or "").path or ""))


def _normalize_video_href(href: str, page_url: str = BASE_SITE) -> Optional[str]:
    abs_url = _absolute(href, page_url)
    if not abs_url or not _is_site_url(abs_url):
        return None
    parsed = urlparse(abs_url)
    if parsed.query:
        return None
    path = parsed.path or ""
    if not _VIDEO_PATH_RE.match(path):
        return None
    if not path.endswith("/"):
        path += "/"
    return urlunparse(("https", parsed.netloc, path, "", "", ""))


def _embed_player_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/embed/{video_id}"


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
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (" - 3DPornDude", " | 3DPornDude", " - 3dporndude", " | 3dporndude"):
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


def _format_seconds(total: Any) -> Optional[str]:
    try:
        total = int(total)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


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


def _parse_json_ld_video(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            out = data
            break
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "VideoObject":
                    out = item
                    break
            if out:
                break
    return out


# ---------------------------------------------------------------------------
# Stream detection
# ---------------------------------------------------------------------------

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
        "/player/stats.php",
        "/player/html.php",
    )
    return any(marker in low for marker in blocked_markers)


def _is_preview_mp4(url: str) -> bool:
    low = (url or "").lower()
    return "_preview.mp4" in low or "preview.mp4" in low.split("?")[0]


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
        "aicandy",
        "magsrv",
        "trialhd",
        "exoclick",
        "propellerads",
        "adsterra",
        "hilltopads",
        "mgid.com",
        "zoneid=",
        "/delivery/",
    )
    return any(marker in s for marker in blocked)


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4") or ".mp4?" in low:
        return "mp4"
    return None


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    q = re.search(r"_(\d{3,4})p\.mp4", low)
    if q:
        return f"{q.group(1)}p"
    if re.search(r"/\d+\.mp4", low) and not re.search(r"_\d{3,4}p\.mp4", low):
        return "source"
    q = re.search(r"(\d{3,4})p", low)
    if q:
        return f"{q.group(1)}p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    return "source"


def _get_file_tier_key(url: str) -> Optional[str]:
    """Group get_file variants of the same file (same quality, different token)."""
    low = (url or "").lower()
    m = re.search(r"/(\d+)_(\d{3,4})p\.mp4", low)
    if m:
        return f"{m.group(1)}:{m.group(2)}p"
    m = re.search(r"/(\d+)\.mp4", low)
    if m:
        return f"{m.group(1)}:source"
    return None


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r"https?://(?:www\.)?3dporndude\.com/get_file/[^\s\"'<>]+",
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            u = m.group(0).strip().rstrip(",;)")
            if u:
                urls.append(u)
    return list(dict.fromkeys(urls))


def _collect_stream_candidates(soup: BeautifulSoup, html: str, page_url: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        url = url.strip()
        if url.startswith("http") and url not in seen:
            seen.add(url)
            candidates.append(url)

    for a in soup.select("a[href]"):
        _add(a.get("href") or "")
    for video in soup.select("video[src]"):
        _add(video.get("src") or "")
    for source in soup.select("video source[src]"):
        _add(source.get("src") or "")
    for u in _extract_inline_urls(html):
        _add(u)
    return candidates


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    video_id = _extract_video_id(page_url)

    raw_streams: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for u in _collect_stream_candidates(soup, html, page_url):
        if _is_non_video_asset_url(u) or _is_probable_ad_iframe(u):
            continue
        fmt = _detect_media_format(u)
        if fmt not in ("mp4", "hls"):
            continue
        if fmt == "mp4" and _is_preview_mp4(u):
            continue
        # On a video page only keep get_file URLs that belong to this video id
        # (related cards expose their own preview get_file links).
        if "/get_file/" in u and video_id and f"/{video_id}/" not in u and f"/{video_id}." not in u:
            continue
        if u in seen_urls:
            continue
        seen_urls.add(u)
        raw_streams.append({"url": u, "quality": _stream_quality_from_url(u), "format": fmt})

    # iframe embeds (exclude ad shells); the site's own /embed/{id} is added
    # separately below, so skip it here to avoid duplicating the shell.
    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        src = _absolute(src, page_url)
        if not src or _is_probable_ad_iframe(src):
            continue
        if _EMBED_PATH_RE.match(urlparse(src).path or ""):
            continue
        if src in seen_urls:
            continue
        seen_urls.add(src)
        raw_streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    # Dedupe get_file variants per tier, keeping the last (player config wins).
    by_tier: dict[str, list[dict[str, str]]] = {}
    for s in raw_streams:
        if s.get("format") == "mp4" and "/get_file/" in (s.get("url") or ""):
            tier = _get_file_tier_key(s["url"]) or s["url"] or ""
            by_tier.setdefault(tier, []).append(s)
    picked: list[dict[str, str]] = []
    for items in by_tier.values():
        best = items[-1]
        best["quality"] = _stream_quality_from_url(best.get("url") or "")
        picked.append(best)
    non_get_file = [s for s in raw_streams if not (s.get("format") == "mp4" and "/get_file/" in (s.get("url") or ""))]
    materialized = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in (non_get_file + picked))))
    materialized = [json.loads(s) for s in materialized]

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = re.search(r"(\d{3,4})", item.get("quality") or "")
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        m = next((s for s in materialized if s.get("format") == preferred), None)
        if m:
            default_url = m.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)

    if video_id:
        embed_url = _embed_player_url(video_id)
        if not any(s.get("url") == embed_url for s in materialized):
            materialized.append({"url": embed_url, "quality": "3dporndude", "format": "embed"})

    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


# ---------------------------------------------------------------------------
# Card / listing parsing
# ---------------------------------------------------------------------------

def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-webp", "data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _parse_card(card: Any, page_url: str) -> Optional[dict[str, Any]]:
    link = card.select_one("a.pon[href*='/video/']")
    if link is None:
        return None
    url = _normalize_video_href(link.get("href") or "", page_url)
    if not url:
        return None

    img = card.select_one(".img-holder img")
    title_el = card.select_one(".title")
    title = (
        link.get("title")
        or (img.get("alt") if img is not None else None)
        or (title_el.get_text(" ", strip=True) if title_el is not None else None)
        or link.get_text(" ", strip=True)
    )
    title = _clean_title(title) or "Unknown Video"

    duration_el = card.select_one(".item-bottom .time")
    views_el = card.select_one(".thumb-bottom-videos .thumb-item")
    artist_el = card.select_one(".card-artist")

    return {
        "url": url,
        "title": title,
        "thumbnail_url": _best_image_url(img),
        "duration": duration_el.get_text(" ", strip=True) if duration_el is not None else None,
        "views": _normalize_numberish(views_el.get_text(" ", strip=True)) if views_el is not None else None,
        "uploader_name": artist_el.get_text(" ", strip=True) if artist_el is not None else None,
    }


def _parse_list_items(soup: BeautifulSoup, page_url: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select(".thumb-itm"):
        if len(items) >= limit:
            break
        parsed = _parse_card(card, page_url)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)
    return items[:limit]


def _list_root(soup: BeautifulSoup) -> Any:
    for sel in (
        "#list_videos_latest_videos_list_items",
        "#list_videos_most_recent_videos_items",
        "#list_videos_common_videos_list_items",
        "#list_videos_videos_list_search_result",
        "#list_videos_related_videos_items",
    ):
        node = soup.select_one(sel)
        if node is not None:
            return node
    best = None
    best_count = -1
    for node in soup.select(".thumbs"):
        n = len(node.select(".thumb-itm"))
        if n > best_count:
            best, best_count = node, n
    return best


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    netloc = parsed.netloc or SITE_HOST
    path = (parsed.path or "/").rstrip("/") or "/"
    query_items = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if v}

    if page <= 1:
        final_path = f"{path}/" if not path.endswith("/") else path
        return urlunparse(("https", netloc, final_path, "", urlencode(query_items) if query_items else "", ""))

    # The homepage has no own page 2; its pagination points at latest-updates.
    if path in ("/", "/index.php"):
        return urlunparse(("https", netloc, f"/latest-updates/{page}/", "", urlencode(query_items) if query_items else "", ""))

    # Search results paginate through the KVS async block API (path-based
    # /search/{q}/{page}/ is 404 and ?page= is ignored by the site).
    if "/search" in path.lower():
        q = query_items.get("q", "")
        if not q:
            m = re.search(r"/search/([^/]+)/?", path)
            q = m.group(1) if m else ""
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
        return urlunparse(("https", netloc, "/search/", "", urlencode(params), ""))

    # KVS path pagination: /latest-updates/2/, /categories/{slug}/2/
    return urlunparse(("https", netloc, f"{path}/{page}/", "", urlencode(query_items) if query_items else "", ""))


# ---------------------------------------------------------------------------
# scrape / list_videos
# ---------------------------------------------------------------------------

def _find_canonical_video_page_url(soup: BeautifulSoup, html: str, video_id: str) -> Optional[str]:
    for a in soup.select("a[href*='/video/']"):
        href = _normalize_video_href(a.get("href") or "")
        if href and re.search(rf"/video/{re.escape(video_id)}/", href):
            return href
    m = re.search(
        rf"https?://[^\s\"'<>]*/video/{re.escape(video_id)}/[^\s\"'<>]*/?",
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return _normalize_video_href(m.group(0))
    return None


def _views_from_json_ld(ld: dict[str, Any]) -> Optional[str]:
    stat = ld.get("interactionStatistic")
    if isinstance(stat, dict):
        if stat.get("userInteractionCount") is not None:
            return str(stat.get("userInteractionCount"))
    if isinstance(stat, list):
        for item in stat:
            if isinstance(item, dict) and item.get("interactionType", "").lower().endswith("/watchaction"):
                if item.get("userInteractionCount") is not None:
                    return str(item.get("userInteractionCount"))
    return None


def _tags_from_html(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for meta in soup.find_all("meta", attrs={"property": "video:tag"}):
        tag = (meta.get("content") or "").strip()
        if tag and tag not in tags and len(tag) < 80:
            tags.append(tag)
    if not tags:
        for a in soup.select(".video-metadata-tags a.btn"):
            tag = a.get_text(" ", strip=True)
            if tag and tag not in tags and len(tag) < 80:
                tags.append(tag)
    return tags


def parse_video_page(html: str, url: str, page_url: str | None = None) -> dict[str, Any]:
    page_url = page_url or url
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_json_ld_video(html)

    title = _clean_title(
        _first_non_empty(
            ld.get("name"),
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1.title, h1").get_text(" ", strip=True) if soup.select_one("h1.title, h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        ld.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )

    thumbnail = _first_non_empty(ld.get("thumbnailUrl"), _meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = _format_duration_iso(ld.get("duration")) or _format_seconds(_meta(soup, prop="video:duration"))
    upload_date = ld.get("uploadDate") or _meta(soup, prop="video:release_date")
    views = _views_from_json_ld(ld)

    if not views:
        views_el = soup.select_one(".video-view-count")
        if views_el is not None:
            views = _normalize_numberish(views_el.get_text(" ", strip=True)) or None
    if not views:
        m = re.search(r"\b(\d[\d\s,.]*)\s*views?\b", soup.get_text(" ", strip=True), flags=re.IGNORECASE)
        if m:
            views = _normalize_numberish(m.group(1))

    category = None
    cat_el = soup.select_one(".video-metadata-categories a.btn")
    if cat_el is not None:
        category = cat_el.get_text(" ", strip=True) or None
    if not category and ld.get("genre"):
        category = str(ld.get("genre")).strip() or None

    tags = _tags_from_html(soup)
    uploader_name = None
    artist_el = soup.select_one(".card-artist-row .card-artist")
    if artist_el is not None:
        uploader_name = artist_el.get_text(" ", strip=True) or None

    related = _parse_list_items(soup, page_url, limit=24)
    related = [r for r in related if r.get("url") != url]

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "video": _extract_streams(soup, html, page_url),
        "related_videos": related,
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    raw_url = (url or "").strip()
    page_url = raw_url
    html = await fetch_page(raw_url, referer=BASE_SITE)
    if not html:
        raise ValueError(f"Failed to fetch 3DPornDude page: {raw_url}")

    video_id = _extract_video_id(raw_url)
    if _is_embed_page_url(raw_url) and video_id:
        soup = BeautifulSoup(html, "lxml")
        canonical = _find_canonical_video_page_url(soup, html, video_id)
        if canonical:
            page_url = canonical
            full_html = await fetch_page(canonical, referer=raw_url)
            if full_html:
                html = full_html
        else:
            page_url = raw_url

    data = parse_video_page(html, page_url, page_url=page_url)
    return data


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=(base_url or "").strip() or BASE_SITE)
    except Exception:
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, page_url, limit=limit)
