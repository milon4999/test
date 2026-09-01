from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://youperv.com/"
SITE_HOST = "youperv.com"

# DLE post URL: /{category}/{numeric-id}-{slug}.html (locale prefixes are stripped)
_POST_SEGMENT_RE = re.compile(r"^\d{2,}-.+\.html$", re.IGNORECASE)
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}$", re.IGNORECASE)

_SKIP_SEGMENTS = {
    "tags",
    "xfsearch",
    "user",
    "page",
    "index.php",
    "engine",
    "templates",
    "uploads",
    "backup",
}

_SKIP_LAST_SEGMENTS = {
    "2257.html",
    "index.php",
}

# Site-brand/title suffix cleanup
_TITLE_NOISE_RE = re.compile(
    r"\s*(?:»|\u00bb)\s*.*$"
)
_CARD_DATE_RE = re.compile(r"\s*\(\s*\d{2}\.\d{2}\.\d{4}\s*\)\s*$")
_TRAILING_DATE_RE = re.compile(r"\s*\d{2}\.\d{2}\.\d{4}\s*$")
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_SITE,
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


def _json_ld_movie(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        candidates: list[Any] = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            graph = data.get("@graph")
            candidates = graph if isinstance(graph, list) else [data]
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") in ("Movie", "VideoObject"):
                return node
    return {}


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
        if url.startswith("data:"):
            return None
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    # og/twitter titles look like: "TITLE » 01.09.2026 » free porn videos ... YouPerv"
    if "»" in t or "\u00bb" in t:
        t = _TITLE_NOISE_RE.sub("", t).strip()
    t = _CARD_DATE_RE.sub("", t)
    t = _TRAILING_DATE_RE.sub("", t).strip()
    t = re.sub(r"\s*HD\s*$", "", t).strip()
    return t or None


def _normalize_media_url(src: str, base: str = BASE_SITE) -> Optional[str]:
    u = (src or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(base, u)
    if not u.startswith("http"):
        return None
    return u


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _extract_inline_media_urls(html: str) -> list[str]:
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
    blocked_markers = (
        "googlesyndication",
        "doubleclick",
        "adservice",
        "trafficjunky",
        "magsrv.com",
        "mbidadm.com",
        "acscdn.com",
        "addtoany.com",
        "liveinternet.ru",
        "cloakworkroom.com",
        "googletagmanager.com",
        "/delivery/afr.php",
        "zoneid=",
        "campaignid=",
        "vast.php",
        "popads",
        "exoclick",
    )
    return any(x in s for x in blocked_markers)


def _is_probable_playable_embed(src: str) -> bool:
    s = (src or "").strip()
    if not s:
        return False
    low = s.lower()
    if _is_probable_ad_iframe(low):
        return False
    return any(
        marker in low
        for marker in (
            "/embed/",
            "/embed?",
            "player",
            "stream",
            ".m3u8",
            ".mp4",
            "video",
        )
    )


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, quality: str, fmt: str) -> None:
        if url and url not in seen:
            seen.add(url)
            streams.append({"url": url, "quality": quality, "format": fmt})

    for video in soup.select("video"):
        src = _normalize_media_url(video.get("src") or "")
        if src:
            _add(src, _quality_from_url(src), "hls" if ".m3u8" in src.lower() else "mp4")
        for source in video.select("source[src]"):
            src = _normalize_media_url(source.get("src") or "")
            if src:
                _add(src, _quality_from_url(src), "hls" if ".m3u8" in src.lower() else "mp4")

    for url in _extract_inline_media_urls(html):
        _add(url, _quality_from_url(url), "hls" if ".m3u8" in url.lower() else "mp4")

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        iframe_src = _normalize_media_url(iframe.get("src") or "")
        if not iframe_src or iframe_src in seen:
            continue
        if not _is_probable_playable_embed(iframe_src):
            continue
        seen.add(iframe_src)
        streams.append({"url": iframe_src, "quality": f"Server {server_idx}", "format": "embed"})
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

    materialized = [json.loads(s) for s in dict.fromkeys(json.dumps(s, sort_keys=True) for s in streams)]
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


def _extract_views_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d\s\u00a0,.]*\s*[KkMm]?)\s*Views", text, re.IGNORECASE)
    if not m:
        return None
    txt = m.group(1).strip()
    txt = txt.replace("\u00a0", "").replace(" ", "").replace(",", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_pornstars(soup_or_node: Any) -> list[str]:
    names: list[str] = []
    for a in soup_or_node.select('a[href*="/xfsearch/pornstar/"]'):
        name = a.get_text(" ", strip=True)
        if name and name not in names:
            names.append(name)
    return names


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    movie = _json_ld_movie(soup)

    title = _clean_title(
        _first_non_empty(
            movie.get("name"),
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
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _best_image_url(soup.select_one("video[poster]")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    # Duration: prefer the dedicated meta item (fa-clock-o), fall back to text scan.
    duration = None
    for item in soup.select(".fmeta .fm-item"):
        m = _DURATION_RE.search(item.get_text(" ", strip=True))
        if m:
            duration = m.group(0)
            break
    if not duration:
        dm = _DURATION_RE.search(soup.get_text(" ", strip=True))
        if dm:
            duration = dm.group(0)

    pornstars: list[str] = []
    meta_block = soup.select_one(".fmeta") or soup
    for a in meta_block.select('a[href*="/xfsearch/pornstar/"]'):
        name = a.get_text(" ", strip=True)
        if name and name not in pornstars:
            pornstars.append(name)

    tags: list[str] = []
    for a in soup.select(".full-tags a"):
        t = a.get_text(" ", strip=True)
        if t:
            tags.append(t)

    upload_date = movie.get("datePublished") or _meta(soup, prop="article:published_time")
    if not upload_date:
        dm = _TRAILING_DATE_RE.search(soup.select_one("h1").get_text(" ", strip=True)) if soup.select_one("h1") else None
        if dm:
            upload_date = dm.group(0).strip()

    related = _parse_cards(soup, limit=24)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": _extract_views_text(soup.get_text(" ", strip=True)),
        "uploader_name": ", ".join(pornstars) if pornstars else None,
        "category": tags[0] if tags else None,
        "tags": tags,
        "upload_date": upload_date,
        "video": _extract_streams(soup, html),
        "related_videos": related,
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _is_probable_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if not (host == SITE_HOST or host.endswith(f".{SITE_HOST}")):
        return None

    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    last = segments[-1]
    if last.lower() in _SKIP_LAST_SEGMENTS:
        return None
    if not _POST_SEGMENT_RE.match(last):
        return None
    # Drop locale prefixes (/es/, /fr/, ...) and utility sections.
    kept = [s for s in segments[:-1] if not _LOCALE_SEGMENT_RE.match(s)]
    if any(s.lower() in _SKIP_SEGMENTS for s in kept):
        return None
    if len(kept) > 1:
        return None

    return urlunparse(("https", SITE_HOST, f"/{'/'.join(kept + [last])}", "", "", ""))


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or SITE_HOST
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path or "/")
    # DLE search results paginate via the search_start query param,
    # everything else uses the /page/{n}/ path segment.
    if query_items.get("do") == "search" or "search_start" in query_items:
        query_items["search_start"] = str(page)
        return urlunparse((scheme, netloc, clean_path or "/", "", urlencode(query_items), ""))

    # DLE pagination: /page/{n}/ segment under the current section path.
    paged_path = clean_path.rstrip("/") + f"/page/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


def _parse_cards(soup: BeautifulSoup, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in soup.select("div.item"):
        if len(items) >= limit:
            break
        link = card.select_one("a.item-link") or card.select_one("a[href]")
        if link is None:
            continue
        href = _is_probable_video_href(link.get("href") or "")
        if not href or href in seen:
            continue

        img = card.select_one(".item-img img") or card.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_node = card.select_one(".item-title h2") or card.select_one(".item-title a")
        title = _clean_title(
            _first_non_empty(
                title_node.get_text(" ", strip=True) if title_node else None,
                img.get("alt") if img else None,
                img.get("title") if img else None,
                link.get("title"),
            )
        ) or "Unknown Video"

        duration = None
        meta_time = card.select_one(".item-meta.meta-time")
        if meta_time:
            dm = _DURATION_RE.search(meta_time.get_text(" ", strip=True))
            if dm:
                duration = dm.group(0)
        if not duration:
            dm = _DURATION_RE.search(card.get_text(" ", strip=True))
            if dm:
                duration = dm.group(0)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": _extract_views_text(card.get_text(" ", strip=True)),
                "uploader_name": ", ".join(_extract_pornstars(card)) or None,
            }
        )

    return items


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    return _parse_cards(soup, limit=limit)
