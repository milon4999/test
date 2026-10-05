from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


_HOST = "cumlouder.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_VIDEO_HREF_RE = re.compile(r"^/porn-video/[^/?#]+/?$", flags=re.IGNORECASE)
_PAGE_SEGMENT_RE = re.compile(r"/(\d+)/?$")
_DURATION_RE = re.compile(r"\b(\d{1,2}:)?\d{1,2}:\d{2}\b")
_VIEWS_RE = re.compile(r"([\d\.,]+)\s*views", flags=re.IGNORECASE)
_VIDEO_ID_RES = (
    re.compile(r"/track_video\.php\?s=(\d+)"),
    re.compile(r"/embed/(\d+)/"),
    re.compile(r"/rate_video\.php\?s=(\d+)"),
)
_DIRECT_MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(?:mp4|m3u8)(?:\?[^\s\"'<>\\]*)?",
    flags=re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == _HOST or h.endswith("." + _HOST)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = f"https://www.{_HOST}/") -> str:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    return await pool_fetch_html(url, headers=headers)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" | Cumlouder.com", " | cumlouder.com", " | CumLouder"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DURATION_RE.search(text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _VIEWS_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().replace(",", "") or None


def _extract_video_id(html: str) -> Optional[str]:
    for rx in _VIDEO_ID_RES:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


def _quality_from_source(source: Any) -> str:
    label = (source.get("label") or "").strip()
    if label and re.match(r"^\d{3,4}p$", label, flags=re.IGNORECASE):
        return label.lower()
    res = (source.get("res") or "").strip()
    if res and res.isdigit():
        return f"{res}p"
    m = re.search(r"([1-9]\d{2,3})p", (source.get("src") or "").lower())
    if m:
        return f"{m.group(1)}p"
    return "source"


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    m = re.search(r"([1-9]\d{2,3})p", low)
    if m:
        return f"{m.group(1)}p"
    if low.endswith(".m3u8") or ".m3u8?" in low:
        return "adaptive"
    return "source"


def _detect_media_format(url: str) -> Optional[str]:
    path = urlparse(url).path.lower() if url else ""
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    return None


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    markers = (
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "exosrv",
        "trafficjunky",
        "tsyndicate",
        "propellerads",
        "realsrv",
        "juicyads",
        "ero-advertising",
        "hilltopads",
        "clickadu",
        "popads",
        "/promo.php",
        "dynamic_banner",
    )
    return any(marker in s for marker in markers)


def _absolute_url(href: str, base_url: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(f"https://www.{_HOST}/", href)
    if not href.startswith("http"):
        return None
    return href


def _extract_direct_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in _DIRECT_MEDIA_RE.finditer(unescaped):
        u = m.group(0).strip()
        if u and _detect_media_format(u):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = _absolute_url(source.get("src"), video_url)
            if not src or src in seen or not _detect_media_format(src):
                continue
            seen.add(src)
            streams.append(
                {
                    "url": src,
                    "quality": _quality_from_source(source),
                    "format": _detect_media_format(src),
                }
            )

    inline_mp4s = [u for u in _extract_direct_urls(html) if _detect_media_format(u) == "mp4"]
    for src in inline_mp4s:
        if src in seen:
            continue
        seen.add(src)
        streams.append(
            {"url": src, "quality": _stream_quality_from_url(src), "format": "mp4"}
        )

    for iframe in soup.select("iframe[src]"):
        src = _absolute_url(iframe.get("src"), video_url)
        if not src or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "embed", "format": "embed"})

    video_id = _extract_video_id(html)
    native_embed = f"https://www.{_HOST}/embed/{video_id}/" if video_id else None
    if native_embed and native_embed not in seen:
        seen.add(native_embed)
        streams.append({"url": native_embed, "quality": "cumlouder", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = re.search(r"(\d{3,4})", item.get("quality") or "")
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        if fmt == "embed" and native_embed and (item.get("url") or "") == native_embed:
            return (1, 1)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        m = next((s for s in materialized if s.get("format") == preferred), None)
        if m:
            default_url = m.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)
    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


async def _fetch_embed_page_fallback(html: str, video_url: str) -> dict[str, Any]:
    """
    When the main page yields no direct stream, re-extract from the native
    /embed/{id}/ page, which exposes the same signed MP4.
    """
    video_id = _extract_video_id(html)
    if not video_id:
        return {}
    embed_url = f"https://www.{_HOST}/embed/{video_id}/"
    try:
        embed_html = await fetch_page(embed_url, referer=video_url)
    except Exception:
        return {}
    soup = BeautifulSoup(embed_html, "lxml")
    return _extract_streams(soup, embed_html, embed_url)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            soup.select_one(".video-top h1").get_text(" ", strip=True)
            if soup.select_one(".video-top h1")
            else None,
            soup.h1.get_text(" ", strip=True) if soup.h1 else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    duration = _extract_duration(
        soup.select_one(".video-top .duracion").get_text(" ", strip=True)
        if soup.select_one(".video-top .duracion")
        else None
    )

    tags: list[str] = []
    for a in soup.select(".video-top ul.tags a.tag-link[href]"):
        label = a.get_text(" ", strip=True)
        if label:
            tags.append(label)

    thumbnail = None
    video_tag = soup.select_one("video[poster]")
    if video_tag:
        thumbnail = (video_tag.get("poster") or "").strip() or None
    if not thumbnail:
        m = re.search(r"var\s+urlImg\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m:
            thumbnail = m.group(1).strip()

    video = _extract_streams(soup, html, url)

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": list(dict.fromkeys(tags)),
        "upload_date": None,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    data = parse_video_page(html, url)

    if not data.get("video", {}).get("has_video"):
        fallback = await _fetch_embed_page_fallback(html, url)
        if fallback.get("has_video"):
            data["video"] = fallback

    return data


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.{_HOST}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = parsed.netloc.lower().split(":")[0]
    if host != f"www.{_HOST}" and host != _HOST:
        return None
    if not _VIDEO_HREF_RE.match(parsed.path or ""):
        return None

    return urlunparse(("https", f"www.{_HOST}", parsed.path.rstrip("/") + "/", "", "", ""))


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
        if url.startswith("http"):
            return url
        url = _absolute_url(url, f"https://www.{_HOST}/")
        if url:
            return url
    return None


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or f"www.{_HOST}"
    path = p.path or "/"
    query = p.query

    host = netloc.lower().split(":")[0].removeprefix("www.")
    # The bare root is a series/category grid, not a video listing; the
    # site's newest-videos index lives at /series/newest/ (paginates as
    # /series/newest/{n}/).
    if host == _HOST and path in ("", "/") and not query:
        path = "/series/newest/"

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", query, ""))

    clean_path = _PAGE_SEGMENT_RE.sub("/", path)
    clean_path = clean_path if clean_path.endswith("/") else clean_path + "/"
    if clean_path in ("/", ""):
        paged_path = f"/{page}/"
    else:
        paged_path = clean_path.rstrip("/") + f"/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", query, ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or f"https://www.{_HOST}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.muestra-escena[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        img = a.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = a.select_one("h2")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                a.get("title"),
                img.get("alt") if img else None,
                a.get_text(" ", strip=True),
            )
        ) or "Unknown Video"

        duration = _extract_duration(
            a.select_one(".minutos").get_text(" ", strip=True)
            if a.select_one(".minutos")
            else None
        )
        views = _extract_views(
            a.select_one(".vistas").get_text(" ", strip=True)
            if a.select_one(".vistas")
            else None
        )

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
