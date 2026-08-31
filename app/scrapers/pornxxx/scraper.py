from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_HOST = "pornxxx.tube"
_BASE = f"https://{BASE_HOST}"


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == BASE_HOST or h.endswith("." + BASE_HOST)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = f"{_BASE}/") -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
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
    for suffix in (" - PornXXX.tube", " - PORNXXX Tube", " | PornXXX.tube"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _format_duration(seconds: Any) -> Optional[str]:
    try:
        total = int(float(seconds))
    except Exception:
        return None
    if total <= 0:
        return None
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b", text)
    return m.group(0) if m else None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{_BASE}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if BASE_HOST not in parsed.netloc.lower():
        return None
    if not re.match(r"^/gallery/\d+/[^/?#]+/?$", parsed.path or "", flags=re.IGNORECASE):
        return None
    if parsed.query:
        return None

    return urlunparse(("https", BASE_HOST, parsed.path.rstrip("/") + "/", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip()
        if not u:
            continue
        path = urlparse(u).path.lower()
        if path.endswith(".mp4") or path.endswith(".m3u8"):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _is_preview_media_url(url: str) -> bool:
    path = urlparse(url).path.lower() if url else ""
    return "_preview.mp4" in path or path.endswith("/preview.mp4")


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    if _is_preview_media_url(url):
        return "preview"
    q = re.search(r"[_\.-](\d{3,4})p?\.mp4", low)
    if q:
        return f"{q.group(1)}p"
    q = re.search(r"[_\.-](\d{3,4})p", low)
    if q:
        return f"{q.group(1)}p"
    if low.endswith(".m3u8"):
        return "adaptive"
    return "source"


def _detect_media_format(url: str) -> Optional[str]:
    path = urlparse(url).path.lower() if url else ""
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    return None


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = f"{_BASE}{src}"
            if not src.startswith("http") or src in seen:
                continue
            fmt = _detect_media_format(src)
            if not fmt:
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        fmt = _detect_media_format(src)
        if not fmt:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum) if not _is_preview_media_url(item.get("url") or "") else (2, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls"):
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


def _parse_track_data(soup: BeautifulSoup) -> dict[str, Any]:
    script = soup.find("script", id="video-track-data")
    if not script:
        return {}
    try:
        raw = script.string or script.get_text(strip=True)
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_list_card(a: Any) -> Optional[dict[str, Any]]:
    href = _normalize_video_href(a.get("href") or "")
    if not href:
        return None

    img = a.find("img")
    thumb = _best_image_url(img)
    if not thumb:
        return None

    title_el = a.select_one(".b-thumb-item__title, .js-gallery-title")
    title = (
        a.get("title")
        or (title_el.get_text(" ", strip=True) if title_el else None)
        or (img.get("alt") if img else None)
        or a.get_text(" ", strip=True)
    )
    title = _clean_title(title) or "Unknown Video"

    duration_el = a.select_one(".b-thumb-item__duration")
    duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": None,
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    track = _parse_track_data(soup)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    thumbnail: Optional[str] = _meta(soup, prop="og:image")
    video_tag = soup.select_one("video#video") or soup.select_one("video")
    if video_tag and video_tag.get("poster"):
        thumbnail = str(video_tag.get("poster")).strip() or thumbnail
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = _first_non_empty(
        _format_duration(track.get("vd")),
        _extract_duration(soup.get_text(" ", strip=True)),
    )

    uploader_name = None
    channel_meta = soup.select_one(".b-gallery-meta__item.channel-link .b-gallery-meta__text")
    if channel_meta:
        uploader_name = channel_meta.get_text(" ", strip=True) or None

    tags: list[str] = []
    details_root = soup.select_one("#details") or soup.select_one(".b-info") or soup
    for tag_link in details_root.select("a[href^='/tags/']"):
        if tag_link.find_parent(class_="b-search-suggestions"):
            continue
        tag = tag_link.get_text(" ", strip=True)
        if tag:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    category = None
    for cat_link in details_root.select("a[href^='/videos/']"):
        label = cat_link.get_text(" ", strip=True)
        if label:
            category = label
            break

    related_videos: list[dict[str, Any]] = []
    related_root = soup.select_one(".js-related-list")
    if related_root:
        seen_urls: set[str] = set()
        for a in related_root.select("a.js-gallery-link[href]"):
            item = _parse_list_card(a)
            if not item or item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            related_videos.append(item)
            if len(related_videos) >= 24:
                break

    video = _extract_streams(soup, html)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader_name,
        "category": category,
        "tags": tags,
        "upload_date": None,
        "video": video,
        "related_videos": related_videos,
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or BASE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    query_items["page"] = str(page)
    return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=f"{_BASE}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.js-gallery-link[href]"):
        if len(items) >= limit:
            break
        item = _parse_list_card(a)
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)

    return items[:limit]
