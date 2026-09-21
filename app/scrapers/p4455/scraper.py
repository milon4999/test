from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

# ---------------------------------------------------------------------------
# p4455.com facts (verified 2026-09):
# - WordPress (theme "kolortube") front-end for the Mydesi.net library.
# - Video pages are root-level slugs: /{slug}/ (no /video/ or /v/ prefix).
# - Streams are direct progressive MP4s on *.myd-cdn.com, exposed both as an
#   inline JSON-LD VideoObject ("contentUrl") and a <video><source src=...>.
# - There are no og:* tags; JSON-LD is the primary metadata source, with the
#   <h1> and visible meta line as fallbacks.
# - Listing cards: div.video-block > a.thumb (img.thumb-img) + a.infos[title].
# - Pagination: /page/{n}/ for sections, ?paged={n} for search; search is ?s=.
# ---------------------------------------------------------------------------

BASE_URL = "https://p4455.com/"
CANONICAL_HOST = "p4455.com"
_SUPPORTED_HOSTS = frozenset({"p4455.com", "www.p4455.com"})

# Paths that are site chrome / taxonomy, never video detail pages.
_NON_VIDEO_PATH_SEGMENTS = (
    "/wp-content/", "/wp-json/", "/wp-admin/", "/wp-includes/",
    "/category/", "/categories/", "/tag/", "/tags/", "/page/",
    "/author/", "/feed/", "/comment", "/search/",
    "/latest/", "/most/", "/best/", "/favorites/", "/enhance/",
    "/tim/", "/stories/", "/mactor/", "/photos/", "/rimg/", "/help-map/", "/cams/",
    "/actors-list/", "/countries-list/", "/onlyfans-star-list/",
    "/all-youtubers-nude-list/", "/stripchat-all-actors/", "/tango-starts-list/",
    "/xvideos-red/", "/faphouse-indian-profile-list/", "/app-actor-list/",
    "/webseries-siterps/", "/indian-actors/", "/gallery/", "/upload/",
    "/batch-upload/", "/contact/", "/privacy/", "/dmca/", "/terms/",
    "/18-u-s-c-2257/", "/18-usc-2257/",
)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h


def can_handle(host: str) -> bool:
    return _normalize_host(host) in _SUPPORTED_HOSTS


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_URL,
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


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                out.extend([x for x in graph if isinstance(x, dict)])
            else:
                out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,|\n]", value) if x.strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_duration(value: Any) -> Optional[str]:
    """Normalize ISO-8601 (PT9M34S), seconds, or clock strings to a display value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        total = int(value)
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    text = str(value).strip()
    if not text:
        return None
    iso = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
    if iso and any(iso.groups()):
        h = int(iso.group(1) or 0)
        m = int(iso.group(2) or 0)
        s = int(float(iso.group(3) or 0))
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    clock = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    if clock:
        return clock.group(0)
    if text.isdigit():
        return _normalize_duration(int(text))
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (
        " – Mydesi.net", " - Mydesi.net", " | Mydesi.net",
        " – Mydesi", " - Mydesi", " | Mydesi",
        " – p4455.com", " - p4455.com", " | p4455.com",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb.]", "", txt)
    return txt.upper() or None


def _extract_views_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d,.]*\s*[KMBkmb]?)\s*(?:views|view)\b", text, re.IGNORECASE)
    if not m:
        return None
    return _clean_views_text(m.group(1))


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _normalize_media_url(src: str, base: str = BASE_URL) -> Optional[str]:
    u = (src or "").strip()
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(base, u)
    if not u.startswith("http"):
        return None
    return u


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
            return urljoin(BASE_URL, url)
        return url
    return None


# ---------------------------------------------------------------------------
# Video URL handling
# ---------------------------------------------------------------------------

def _is_video_path(path: str) -> bool:
    """p4455.com video pages are single-segment root slugs like /my-video-title/."""
    low = (path or "").lower()
    if not low or low == "/":
        return False
    if any(seg in low for seg in _NON_VIDEO_PATH_SEGMENTS):
        return False
    if low.endswith((".php", ".json", ".xml", ".txt", ".jpg", ".png", ".css", ".js")):
        return False
    return bool(re.fullmatch(r"/[a-z0-9](?:[a-z0-9\-_.]*[a-z0-9])?/?$", low))


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_URL, href)
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if _normalize_host(parsed.netloc) not in _SUPPORTED_HOSTS:
        return None
    if parsed.query:
        return None

    clean_path = re.sub(r"/{2,}", "/", parsed.path or "/").strip()
    if not _is_video_path(clean_path):
        return None
    if not clean_path.endswith("/"):
        clean_path += "/"
    return urlunparse(("https", CANONICAL_HOST, clean_path, "", "", ""))


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked = (
        "googlesyndication", "doubleclick", "adservice", "trafficjunky", "exoclick",
        "juicyads", "adspyglass", "zoneid=", "campaignid=", "creativeid=",
        "affid=", "/delivery/afr.php",
    )
    return any(x in s for x in blocked)


def _extract_inline_urls(html: str) -> list[str]:
    """Direct media URLs embedded in scripts / JSON-LD / inline JSON."""
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*',
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            url = m.group(0).strip().rstrip(",;)(\'")
            if url and not _is_probable_ad_iframe(url):
                urls.append(url)
    return list(dict.fromkeys(urls))


def _collect_player_iframes(soup: BeautifulSoup) -> list[Any]:
    for selector in (
        ".video-player iframe[src]",
        ".responsive-player iframe[src]",
        ".entry-content iframe[src]",
        "article iframe[src]",
    ):
        found = soup.select(selector)
        if found:
            return found
    return soup.select("iframe[src]")


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, fmt: Optional[str] = None) -> None:
        if not url or url in seen:
            return
        low = url.lower()
        if fmt is None:
            if ".m3u8" in low:
                fmt = "hls"
            elif ".mp4" in low:
                fmt = "mp4"
            else:
                return
        seen.add(url)
        streams.append({"url": url, "quality": _quality_from_url(url), "format": fmt})

    # 1. JSON-LD VideoObject contentUrl (primary source on this site)
    for obj in _parse_json_ld(soup):
        content_url = _normalize_media_url(str(obj.get("contentUrl") or ""))
        if content_url:
            _add(content_url)
        embed_url = _normalize_media_url(str(obj.get("embedUrl") or ""))
        if embed_url and not _is_probable_ad_iframe(embed_url):
            _add(embed_url, "embed")

    # 2. <video> tags and their <source> children
    for video in soup.select("video"):
        src = _normalize_media_url(video.get("src") or "")
        if src:
            _add(src)
        for source in video.select("source[src]"):
            src = _normalize_media_url(source.get("src") or "")
            if src:
                _add(src)

    # 3. Any media URL embedded in inline scripts / markup
    for url in _extract_inline_urls(html):
        _add(url)

    # 4. Player iframes / embeds (fallback when no progressive file exists)
    server_idx = 1
    for iframe in _collect_player_iframes(soup):
        src = _normalize_media_url(iframe.get("src") or "")
        if not src or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = item.get("quality") or ""
        digits = re.search(r"(\d{3,4})", q)
        quality_score = int(digits.group(1)) if digits else 0
        if fmt == "mp4":
            return (3, quality_score)
        if fmt == "hls":
            return (2, quality_score)
        return (1, 0)

    deduped = list(dict.fromkeys(json.dumps(s, sort_keys=True) for s in streams))
    materialized: list[dict[str, str]] = [json.loads(s) for s in deduped]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for fmt in ("mp4", "hls", "embed"):
        candidates = [s for s in materialized if s.get("format") == fmt]
        if candidates:
            default_url = candidates[0].get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)

    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title: Optional[str] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[str] = None
    upload_date: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []

    for obj in _parse_json_ld(soup):
        obj_type = str(obj.get("@type") or "").lower()
        if obj_type and "videoobject" not in obj_type:
            continue
        title = title or _first_non_empty(str(obj.get("name") or "")) or None
        description = description or _first_non_empty(str(obj.get("description") or "")) or None
        thumbnail = thumbnail or _first_non_empty(str(obj.get("thumbnailUrl") or "")) or None
        duration = duration or _normalize_duration(obj.get("duration"))
        upload_date = upload_date or _first_non_empty(str(obj.get("uploadDate") or "")) or None
        tags.extend(_as_list(obj.get("keywords")))

    # Meta fallbacks (og:* are absent on this site, but kept for resilience)
    title = title or _first_non_empty(
        _meta(soup, prop="og:title"),
        _meta(soup, name="twitter:title"),
        (soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None),
        (soup.title.get_text(" ", strip=True) if soup.title else None),
    )
    description = description or _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = thumbnail or _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    if not duration:
        for el in soup.select(".video-datas, .views-number"):
            duration = _normalize_duration(el.get_text(" ", strip=True))
            if duration:
                break
    if not duration:
        duration = _normalize_duration(soup.get_text(" ", strip=True))

    views = _extract_views_text(soup.get_text(" ", strip=True))
    if not views:
        views_el = soup.select_one(".views, .views-count, .video-views")
        if views_el is not None:
            views = _clean_views_text(views_el.get_text(" ", strip=True))

    for a in soup.select("a[href*='/category/']"):
        name = a.get_text(" ", strip=True)
        if name:
            category = category or name
            break

    for el in soup.select('.tags a, .video-tags a, a[href*="/tag/"]'):
        tag = el.get_text(" ", strip=True)
        if tag and len(tag) < 80:
            tags.append(tag)
    tags = list(dict.fromkeys([t for t in tags if t]))

    return {
        "url": url,
        "title": _clean_title(title) or "Unknown Video",
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "video": _extract_streams(soup, html),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


# ---------------------------------------------------------------------------
# Listing / pagination
# ---------------------------------------------------------------------------

def _build_list_page_url(base_url: str, page: int) -> str:
    """p4455.com paginates by path (/page/{n}/) for every section, including
    search (which keeps its query, e.g. /page/2/?s=desi). ?paged= is ignored
    by the site, so it is stripped rather than used."""
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or CANONICAL_HOST

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if not path.endswith("/"):
        path += "/"

    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.pop("paged", None)
    query_items.pop("page", None)
    query = urlencode(query_items)

    if page <= 1:
        clean_path = re.sub(r"/page/\d+/?$", "/", path).rstrip("/") or "/"
        return urlunparse((scheme, netloc, clean_path, "", query, ""))

    # Normalize away any existing page segment, then append the requested one.
    base_path = re.sub(r"/page/\d+/?$", "/", path).rstrip("/")
    paged_path = f"{base_path}/page/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", query, ""))



async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for div in soup.select("div.video-block"):
        if len(items) >= limit:
            break
        a_thumb = div.select_one("a.thumb[href]")
        a_infos = div.select_one("a.infos")
        img = div.select_one("img.thumb-img")
        if a_thumb is None and a_infos is None:
            continue

        href = _normalize_video_href((a_thumb or a_infos).get("href") or "")
        if not href or href in seen:
            continue

        title = None
        if a_infos is not None:
            title = (a_infos.get("title") or None) or a_infos.get_text(" ", strip=True)
        if not title and a_thumb is not None:
            title = (
                (a_thumb.get("title") or None)
                or (img.get("alt") if img is not None else None)
                or a_thumb.get_text(" ", strip=True)
            )
        title = _clean_title(title) or "Unknown Video"
        thumbnail = _best_image_url(img)

        ctext = div.get_text(" ", strip=True) if div else ""
        duration = _normalize_duration(ctext)
        views = _extract_views_text(ctext)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumbnail,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]
