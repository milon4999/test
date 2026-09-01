from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

_HOST = "latestpornvideo.com"
_BASE = f"https://{_HOST}/"


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return h == _HOST or h.endswith(f".{_HOST}")


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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": _BASE,
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


def _itemprop(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"itemprop": name})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (
        " \u2013 Watch Latest Porn Video at LatestPornVideo.com for Free.",
        " - Watch Latest Porn Video at LatestPornVideo.com for Free.",
        " \u2013 Watch Latest Porn Video at LatestPornVideo.com for Free",
        " - Watch Latest Porn Video at LatestPornVideo.com for Free",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _parse_iso_duration(value: str) -> Optional[str]:
    m = re.match(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    total = h * 3600 + mi * 60 + s
    if total <= 0:
        return None
    if h > 0:
        return f"{h}:{mi:02d}:{s:02d}"
    return f"{mi:02d}:{s:02d}"


def _normalize_duration(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        total = int(value)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        iso = _parse_iso_duration(v)
        if iso:
            return iso
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", v):
            return v
    return str(value).strip() or None


def _duration_from_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(\d{1,2}:)?\d{1,2}:\d{2}\b", text)
    if m:
        return m.group(0)
    return None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_views_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d,\.]*\s*[KMBkmb]?)\s*(?:views|view)?\b", text, re.IGNORECASE)
    if not m:
        return None
    return _clean_views_text(m.group(1))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-lazy-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _is_probable_video_post(parsed: Any) -> bool:
    path = parsed.path.rstrip("/")
    if not path:
        return False
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return False
    return bool(re.fullmatch(r"\d{2,}", segments[0]))


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{_BASE}{href.lstrip('/')}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if _HOST not in parsed.netloc.lower():
        return None
    if any(x in parsed.path.lower() for x in ("/wp-content/", "/wp-json/", "/wp-admin/", "/tag/", "/category/", "/page/", "/author/", "/date/", "/feed/", "/cdn-cgi/", "/embed")):
        return None
    if parsed.query:
        return None
    if not _is_probable_video_post(parsed):
        return None
    post_id = parsed.path.strip("/").split("/", 1)[0]
    return urlunparse(("https", _HOST, f"/{post_id}/", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            url = m.group(0).strip()
            if url:
                urls.append(url)
    return list(dict.fromkeys(urls))


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    return any(
        x in s
        for x in (
            "googlesyndication",
            "doubleclick",
            "adservice",
            "jads.co",
            "juicyads",
            "a-ads.com",
            "acceptable.a-ads",
            "adeptdoorstep",
            "exoclick",
            "exosrv",
            "vast",
            "clickadu",
            "propellerads",
            "cloudflareinsights",
            "challenges.cloudflare.com",
        )
    )


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for video in soup.select("video"):
        src = (video.get("src") or "").strip()
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(_BASE, src)
            if src.startswith("http") and src not in seen:
                seen.add(src)
                streams.append(
                    {"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"}
                )
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(_BASE, src)
            if not src.startswith("http") or src in seen:
                continue
            seen.add(src)
            streams.append(
                {"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"}
            )

    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        seen.add(src)
        streams.append(
            {"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"}
        )

    server_idx = 1
    player = soup.select_one(".video-player") or soup.select_one(".responsive-player")
    player_iframes: list[Any] = []
    if player is not None:
        player_iframes = player.select("iframe[src]")
    for iframe in player_iframes:
        src = (iframe.get("src") or "").strip()
        if not src or src in seen or _is_probable_ad_iframe(src):
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(_BASE, src)
        if not src.startswith("http"):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    if server_idx == 1:
        for iframe in soup.select("iframe[src]"):
            src = (iframe.get("src") or "").strip()
            if not src or src in seen or _is_probable_ad_iframe(src):
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(_BASE, src)
            if not src.startswith("http"):
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

    deduped = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in deduped]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for fmt in ("mp4", "hls", "embed"):
        match = next((s for s in materialized if s.get("format") == fmt), None)
        if match:
            default_url = match.get("url")
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

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            _itemprop(soup, "name"),
            soup.select_one("h1.entry-title").get_text(" ", strip=True) if soup.select_one("h1.entry-title") else None,
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _itemprop(soup, "description"),
        _meta(soup, name="description"),
    )

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _itemprop(soup, "thumbnailUrl"),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    upload_date = _first_non_empty(
        _itemprop(soup, "uploadDate"),
        _meta(soup, prop="article:published_time"),
        _meta(soup, prop="article:modified_time"),
    )

    uploader = _itemprop(soup, "author")

    duration = _normalize_duration(_itemprop(soup, "duration"))
    views: Optional[str] = None
    views_tag = soup.select_one("#video-views span")
    if views_tag is not None:
        views = _clean_views_text(views_tag.get_text(" ", strip=True))
    if not views:
        views_box = soup.select_one("#video-views")
        if views_box is not None:
            views = _extract_views_text(views_box.get_text(" ", strip=True))

    category = None
    tags: list[str] = []
    tags_list = soup.select_one(".tags-list")
    if tags_list is not None:
        for a in tags_list.select("a.label[href]"):
            href = (a.get("href") or "").lower()
            label = a.get_text(" ", strip=True)
            if not label:
                continue
            if "/category/" in href:
                category = _first_non_empty(category, label)
            elif "/tag/" in href:
                tags.append(label)
    if not category:
        current = soup.select_one("#breadcrumbs .current")
        if current is None:
            for a in soup.select("#breadcrumbs a[href*='/category/']"):
                category = a.get_text(" ", strip=True)
                break

    text_blob = soup.get_text(" ", strip=True)
    if not duration:
        duration = _duration_from_text(text_blob)
    if not views:
        views = _extract_views_text(text_blob)

    tags = list(dict.fromkeys([t for t in tags if t]))
    video = _extract_streams(soup, html)

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
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or _HOST
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path or "/")
    if clean_path in ("", "/"):
        clean_path = "/"
    else:
        clean_path = clean_path.rstrip("/") + "/"
    paged_path = clean_path.rstrip("/") + f"/page/{page}/" if clean_path != "/" else f"/page/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


def _parse_card(article: Any, seen: set[str]) -> Optional[dict[str, Any]]:
    a = article.select_one("a[href]")
    if a is None:
        return None
    href = _normalize_video_href(a.get("href") or "")
    if not href or href in seen:
        return None

    img = article.select_one("img")
    thumb = _best_image_url(img)

    title = _clean_title(
        _first_non_empty(
            a.get("title"),
            img.get("alt") if img else None,
            article.select_one(".entry-header span").get_text(" ", strip=True) if article.select_one(".entry-header span") else None,
            a.get_text(" ", strip=True),
        )
    ) or "Unknown Video"

    duration = None
    duration_tag = article.select_one(".duration")
    if duration_tag is not None:
        duration = _duration_from_text(duration_tag.get_text(" ", strip=True)) or _normalize_duration(
            duration_tag.get_text(" ", strip=True)
        )

    views = None
    views_tag = article.select_one(".views")
    if views_tag is not None:
        views = _clean_views_text(views_tag.get_text(" ", strip=True))

    seen.add(href)
    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    cards = soup.select("article.loop-video") or soup.select("article.thumb-block")
    for card in cards:
        if len(items) >= limit:
            break
        item = _parse_card(card, seen)
        if item is not None:
            items.append(item)

    if not items:
        for a in soup.select("a[href]"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href or href in seen:
                continue
            container = a.find_parent("article") or a.find_parent("div")
            img = a.find("img") or (container.find("img") if container else None)
            thumb = _best_image_url(img)
            if not thumb:
                continue
            title = _clean_title(
                _first_non_empty(a.get("title"), img.get("alt") if img else None, a.get_text(" ", strip=True))
            ) or "Unknown Video"
            ctext = container.get_text(" ", strip=True) if container else ""
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": _duration_from_text(ctext),
                    "views": _extract_views_text(ctext),
                    "uploader_name": None,
                }
            )

    return items[:limit]
