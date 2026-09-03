from __future__ import annotations

import asyncio
import html as _html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.bigwank.com/"
SITE_HOST = "bigwank.com"
SITE_ALIASES = frozenset({"bigwank.com", "www.bigwank.com"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

# Canonical watch URL: https://www.bigwank.com/videos/{numeric_id}/{32-hex-hash}/
_VIDEO_HREF_RE = re.compile(
    r"bigwank\.com/videos/(?P<id>\d+)/(?P<hash>[0-9a-fA-F]{32})/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(r"bigwank\.com/embed/(?P<id>\d+)/?", re.IGNORECASE)
_GET_FILE_RE = re.compile(
    r"https?://(?:www\.)?bigwank\.com/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)
_DURATION_TEXT_RE = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")
_VIEWS_RE = re.compile(r"([\d,.]+(?:[KMB])?)\s*views", re.IGNORECASE)

# Root path segments that are never video pages on this KVS tube.
_RESERVED_SEGMENTS = frozenset(
    {
        "videos",
        "categories",
        "category",
        "models",
        "model",
        "sites",
        "channels",
        "tags",
        "tag",
        "search",
        "latest-updates",
        "top-rated",
        "most-popular",
        "private",
        "premium",
        "playlists",
        "members",
        "my",
        "community",
        "albums",
        "rss",
        "embed",
        "upload-video",
        "login",
        "login-required",
        "logout",
        "signup",
        "upgrade",
        "feedback",
        "dmca",
        "terms",
        "privacy",
        "2257",
        "link",
        "captcha",
        "static",
        "js",
        "css",
        "player",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".bigwank.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _unescape_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = _html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


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
    t = _unescape_text(title)
    if not t:
        return None
    for suffix in (
        " - BigWank.com",
        " | BigWank.com",
        " - BigWank",
        " | BigWank",
        " - bigwank.com",
        " | bigwank.com",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").strip("/").split("/") if p]


def _extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_HREF_RE.search(url or "")
    if m:
        return m.group("id")
    m = _EMBED_HREF_RE.search(url or "")
    if m:
        return m.group("id")
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)

    m = _VIDEO_HREF_RE.search(href.split("#", 1)[0])
    if not m:
        return None
    return f"https://www.bigwank.com/videos/{m.group('id')}/{m.group('hash').lower()}/"


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _parse_duration_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DURATION_TEXT_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return f"{int(m.group(1))}:{int(m.group(2)):02d}:{int(m.group(3)):02d}"
    return f"{int(m.group(2)):02d}:{int(m.group(3)):02d}"


def _parse_views_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _VIEWS_RE.search(text)
    if not m:
        return None
    return m.group(1)


def _normalize_quality_label(label: str | None, url: str = "") -> str:
    text = str(label or "").strip()
    if text.isdigit():
        return f"{text}p"
    mq = re.search(r"(\d{3,4})\s*p", text, re.IGNORECASE)
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"_(\d{3,4})[pm]\.mp4", url, re.IGNORECASE)
    if mq:
        return f"{mq.group(1)}p"
    if text:
        return text
    return "default"


def _quality_rank(label: str | None) -> int:
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    return int(digits) if digits else 0


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)

    parts = _path_parts(parsed.path)
    # Strip any trailing numeric page segment from the incoming base URL.
    if len(parts) >= 2 and parts[-1].isdigit() and parts[-2] != "videos":
        parts = parts[:-1]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    else:
        if not parts:
            # Bare home has no /{n}/ feed; most-popular is the paginated index.
            new_path = f"/most-popular/{page_num}/"
        else:
            new_path = "/" + "/".join(parts + [str(page_num)]) + "/"

    query = "&".join(
        f"{k}={v}" for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return urlunparse((parsed.scheme or "https", parsed.netloc or "www.bigwank.com", new_path, "", query, ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if url.endswith((".ico", ".svg")):
            continue
        return _normalize_media_url(url)
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        first = str(srcset).split(",")[0].strip().split(" ")[0]
        if first and first.startswith("http"):
            return _normalize_media_url(first)
    return None


def _parse_list_card(card: Any) -> Optional[dict[str, Any]]:
    anchor = card.select_one("a.thumb__top[href]") or card.select_one("a[href]")
    if anchor is None:
        return None

    canon = _normalize_video_href(anchor.get("href") or "")
    if not canon:
        return None

    img = anchor.find("img") or card.find("img")
    thumb = _best_image_url(img)

    title = _clean_title(
        _first_non_empty(
            img.get("alt") if img else None,
            (card.select_one(".thumb__title") or anchor).get_text(" ", strip=True),
            anchor.get("title"),
        )
    ) or "Unknown Video"

    duration = None
    dur_el = card.select_one(".thumb__duration")
    if dur_el is not None:
        duration = _parse_duration_text(dur_el.get_text(" ", strip=True))
    if duration is None:
        duration = _parse_duration_text(card.get_text(" ", strip=True))

    views = _parse_views_text(card.get_text(" ", strip=True))

    uploader = None
    model_link = card.select_one("a.thumb-models__link[href*='/models/']")
    if model_link is not None:
        uploader = _unescape_text(model_link.get_text(" ", strip=True))
    if uploader and uploader.lower().startswith("suggest"):
        uploader = None

    preview_url = None
    thumb_img = card.select_one(".thumb__img[data-preview]")
    if thumb_img is not None and thumb_img.get("data-preview"):
        preview_url = _normalize_media_url(str(thumb_img.get("data-preview")))

    return {
        "url": canon,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "preview_url": preview_url,
    }


def _parse_cards(soup: BeautifulSoup, exclude_url: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.thumb.item, div.item.thumb"):
        if len(items) >= limit:
            break
        parsed = _parse_list_card(card)
        if not parsed or parsed["url"] in seen:
            continue
        if exclude_url and parsed["url"].rstrip("/") == exclude_url.rstrip("/"):
            continue
        seen.add(parsed["url"])
        items.append(parsed)
    return items


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


def _parse_get_file_streams(html: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    """Collect same-origin /get_file/ MP4 URLs straight from page HTML.

    The URLs are kept as-is: WebView players follow the 302 redirect to the
    signed CDN file at playback time, so no redirect resolution is done here.
    """
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(raw: str, label: Optional[str] = None) -> None:
        media = _normalize_media_url(raw)
        if not media or media in seen:
            return
        if "/get_file/" not in media:
            return
        low = media.lower()
        if "preview" in low or "screenshot" in low or ".jpg" in low or ".m3u8" in low:
            return
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _normalize_quality_label(label, media),
                "format": "mp4",
            }
        )

    for source in soup.select("video source[src], video[src]"):
        _add(str(source.get("src") or ""), source.get("label"))

    for media in _GET_FILE_RE.findall((html or "").replace("\\/", "/")):
        _add(media)

    # Deduplicate per quality label, preferring the player <source> (first seen).
    best: dict[str, dict[str, str]] = {}
    for s in streams:
        key = s["quality"]
        if key not in best:
            best[key] = s
    return sorted(best.values(), key=lambda s: _quality_rank(s.get("quality")), reverse=True)


def _streams_from_html(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = _parse_get_file_streams(html, soup)

    video_id = _extract_video_id(url)
    if video_id:
        embed = f"https://www.bigwank.com/embed/{video_id}"
        if embed not in {s["url"] for s in streams}:
            streams.append({"url": embed, "quality": "embed", "format": "embed"})

    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    default = (mp4 or embed or {}).get("url") if (mp4 or embed) else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    """Resolve a /get_file/ URL to the signed CDN MP4.

    The site rejects HEAD requests (410) and requires a video-page Referer, so
    resolution uses a tiny GET with a Range header. Any failure (network
    error, non-redirect, missing Location, loop back to get_file) returns None.
    """
    raw = (get_file_url or "").strip()
    if not raw or "/get_file/" not in raw:
        return None

    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": referer if referer.startswith("http") else BASE_SITE,
        "Accept": "*/*",
        "Range": "bytes=0-0",
    }

    def _accept_location(location: Optional[str], origin: str) -> Optional[str]:
        if not location or not location.startswith("http"):
            return None
        if "bigwank.com/get_file" in location.lower():
            return None
        if "bigwank.com" in urlparse(location).netloc.lower() and ".mp4" not in location.lower():
            return None
        return location

    try:
        from curl_cffi.requests import AsyncSession

        for target in (raw, raw.rstrip("/") + "/"):
            try:
                async with AsyncSession(impersonate="chrome120", headers=headers, timeout=15.0) as client:
                    resp = await client.get(target, allow_redirects=False)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = _accept_location(
                            resp.headers.get("Location") or resp.headers.get("location"),
                            target,
                        )
                        if loc:
                            return loc
            except Exception:
                continue
    except ImportError:
        pass

    # aiohttp fallback (shared pool session style, no impersonation).
    try:
        import aiohttp

        for target in (raw, raw.rstrip("/") + "/"):
            try:
                async with aiohttp.ClientSession(
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15.0),
                ) as session:
                    async with session.get(target, allow_redirects=False) as resp:
                        if resp.status in (301, 302, 303, 307, 308):
                            loc = _accept_location(
                                resp.headers.get("Location") or resp.headers.get("location"),
                                target,
                            )
                            if loc:
                                return loc
            except Exception:
                continue
    except ImportError:
        pass

    return None


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    """Resolve /get_file/ MP4 streams to signed CDN URLs.

    Failed / unresolvable get_file streams are removed entirely; the response
    then carries only the /embed/{id} stream (WebView playback).
    """
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "/get_file/" in (s.get("url") or "")
    ]
    if not get_file_streams:
        return

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _resolve_get_file_url(stream["url"], referer=referer)
        return stream, resolved

    try:
        pairs = await asyncio.wait_for(
            asyncio.gather(*[_resolve_one(s) for s in get_file_streams]),
            timeout=25.0,
        )
    except Exception:
        pairs = [(s, None) for s in get_file_streams]

    for stream, resolved in pairs:
        if resolved:
            stream["url"] = resolved
        elif stream in streams:
            # Unresolvable get_file link: drop it and keep the embed fallback.
            streams.remove(stream)

    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    video["default"] = (mp4 or embed or {}).get("url") if (mp4 or embed) else None
    video["hls"] = None
    video["has_video"] = bool(streams)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _meta(soup, prop="og:image")
    if not thumbnail:
        video_el = soup.select_one("video[poster]")
        if video_el is not None and video_el.get("poster"):
            thumbnail = str(video_el.get("poster"))
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    description = _meta(soup, name="description") or _meta(soup, prop="og:description")

    views = None
    views_el = soup.select_one(".video-info__text")
    if views_el is not None:
        views = _parse_views_text(views_el.get_text(" ", strip=True))
    if views is None:
        views = _parse_views_text(soup.get_text(" ", strip=True))

    tags: list[str] = []
    for link in soup.select(".video-links__list_tags a[href*='/categories/']"):
        txt = _unescape_text(link.get_text(" ", strip=True))
        if txt and txt.lower() not in {t.lower() for t in tags}:
            tags.append(txt)
    if not tags:
        raw_keywords = _meta(soup, name="keywords") or ""
        for part in raw_keywords.split(","):
            txt = _unescape_text(part)
            if txt and txt.lower() not in {t.lower() for t in tags}:
                tags.append(txt)

    uploader = None
    added_row = None
    for row in soup.select(".video-links__row"):
        title_el = row.select_one(".video-links__title")
        if title_el is not None and (title_el.get_text(strip=True) or "").lower() == "added by:":
            added_row = row
            break
    if added_row is not None:
        uploader = _unescape_text(added_row.get_text(" ", strip=True))
        if uploader:
            uploader = uploader.replace("Added by:", "", 1).strip()
    if not uploader or (uploader or "").lower().startswith("suggest"):
        model_link = soup.select_one("a[href*='/models/']")
        if model_link is not None:
            uploader = _unescape_text(model_link.get_text(" ", strip=True))
    if uploader and uploader.lower().startswith("suggest"):
        uploader = None

    category = tags[0] if tags else None

    # Duration: watch pages have no visible duration node; the videojs
    # thumbnails config exposes it as Math.floor({seconds} / 100).
    duration = None
    m = re.search(r"everyX\s*=\s*Math\.floor\((\d+)\s*/\s*100\)", html or "")
    if m:
        total = int(m.group(1))
        if total > 0:
            hours, rem = divmod(total, 3600)
            minutes, seconds = divmod(rem, 60)
            duration = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
    if duration is None and soup.select_one(".video-info") is not None:
        duration = _parse_duration_text(soup.select_one(".video-info").get_text(" ", strip=True))

    related = _parse_cards(soup, exclude_url=url)

    video = _streams_from_html(html, soup, url)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": category,
        "tags": tags,
        "upload_date": None,
        "video": video,
        "related_videos": related,
        "preview_url": thumbnail,
    }


async def _resolve_scrape_url(url: str) -> str:
    """Accept watch URLs and /embed/{id} URLs; resolve both to the canonical watch page."""
    if _is_embed_url(url):
        embed_html = await fetch_page(url, referer=BASE_SITE)
        video_id = _extract_video_id(url)
        if video_id:
            m = re.search(
                rf"bigwank\.com/videos/{video_id}/([0-9a-fA-F]{{32}})/?",
                embed_html or "",
            )
            if m:
                return f"https://www.bigwank.com/videos/{video_id}/{m.group(1).lower()}/"
        return url
    canon = _normalize_video_href(url)
    if canon:
        return canon
    return url


async def scrape(url: str) -> dict[str, Any]:
    canon = await _resolve_scrape_url(url)
    html = await fetch_page(canon, referer=BASE_SITE)
    data = parse_video_page(html, canon)
    await _resolve_video_streams(data.get("video", {}), referer=canon)
    return data


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url or BASE_SITE, page)
    try:
        html = await fetch_page(page_url, referer=BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items = _parse_cards(soup, limit=limit)

    if not items:
        # Fallback: raw href scan (ad scripts can break the card grid parse).
        seen: set[str] = set()
        for m in _VIDEO_HREF_RE.finditer(html):
            href = f"https://www.bigwank.com/videos/{m.group('id')}/{m.group('hash').lower()}/"
            if href in seen:
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
                    "preview_url": None,
                }
            )
            if len(items) >= limit:
                break

    return items[:limit]
