from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.perfectgirls.xxx/"
SITE_ALIASES = frozenset({"perfectgirls.xxx", "www.perfectgirls.xxx", "static.perfectgirls.xxx"})
_IMPERSONATIONS = ("chrome136", "chrome131", "chrome")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?perfectgirls\.xxx)?/video/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?perfectgirls\.xxx)?/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(
    r"https?://(?:www\.)?perfectgirls\.xxx/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_QUALITY_IN_URL_RE = re.compile(r"_(\d{3,4})p\.mp4", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".perfectgirls.xxx")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _fetch_html_sync(url: str, referer: str | None = None) -> str:
    from curl_cffi.requests import Session

    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for impersonate in _IMPERSONATIONS:
        try:
            with Session(impersonate=impersonate) as client:
                resp = client.get(url, headers=headers, timeout=30.0, allow_redirects=True)
            if resp.status_code in (403, 429, 503):
                last_error = RuntimeError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            text = resp.text or ""
            if "just a moment" in text.lower()[:400]:
                last_error = RuntimeError("Cloudflare challenge")
                continue
            return text
        except Exception as e:
            last_error = e
            continue
    raise last_error or RuntimeError(f"Failed to fetch {url}")


async def fetch_html(url: str, *, referer: str | None = None) -> str:
    return await asyncio.to_thread(_fetch_html_sync, url, referer)


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    getter = getattr(el, "get_text", None)
    if callable(getter):
        return getter(strip=True) or None
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


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None
    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            if h > 0:
                return f"{h}:{mm:02d}:{s:02d}"
            return f"{mm}:{s:02d}"
        if v.isdigit():
            return _normalize_duration(int(v))
        found = _DURATION_RE.search(v)
        return found.group(0) if found else (v or None)
    return str(seconds_or_iso).strip() or None


def _quality_rank(label: Optional[str]) -> int:
    text = (label or "").lower()
    m = re.search(r"(\d{3,4})", text)
    if m:
        return int(m.group(1))
    if "4k" in text or "uhd" in text:
        return 2160
    if "hd" in text:
        return 720
    if "sd" in text or "auto" in text:
        return 360
    if text in {"hls", "mp4"}:
        return 1
    if text == "embed":
        return -1
    return 0


def _extract_video_id(url: str) -> Optional[str]:
    for pattern in (_VIDEO_HREF_RE, _EMBED_HREF_RE):
        m = pattern.search(url or "")
        if m:
            return m.group("id")
    return None


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _normalize_video_href(href: str, *, base: str = BASE_SITE) -> Optional[str]:
    raw = (href or "").strip()
    if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
        return None
    abs_url = urljoin(base, raw)
    parsed = urlparse(abs_url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host and host not in {"perfectgirls.xxx", "www.perfectgirls.xxx"}:
        return None
    m = _VIDEO_HREF_RE.search(parsed.path or "")
    if not m:
        return None
    return urlunparse(("https", "www.perfectgirls.xxx", f"/video/{m.group('id')}/", "", "", ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-src", "data-lazy", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return urljoin(BASE_SITE, url)
    return None


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").split("/") if p]


def _build_list_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url or BASE_SITE)
    host = parsed.netloc or "www.perfectgirls.xxx"
    parts = _path_parts(parsed.path)
    page_num = max(int(page or 1), 1)

    if parts and parts[-1].isdigit() and not (len(parts) >= 2 and parts[0] == "video"):
        parts = parts[:-1]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    elif not parts:
        new_path = f"/{page_num}/"
    else:
        new_path = "/" + "/".join(parts + [str(page_num)]) + "/"

    query = "&".join(f"{k}={v}" for k, v in parse_qsl(parsed.query, keep_blank_values=True))
    return urlunparse((parsed.scheme or "https", host, new_path, "", query, ""))


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
            out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _quality_from_stream(url: str, label: Optional[str] = None) -> str:
    if label:
        text = label.strip()
        if text.lower() == "auto":
            return "auto"
        if text.isdigit():
            return f"{text}p"
        if re.search(r"\d", text):
            return text
    m = _QUALITY_IN_URL_RE.search(url or "")
    if m:
        return f"{m.group(1)}p"
    return "mp4"


def _streams_from_page(html: str, soup: BeautifulSoup) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, label: Optional[str] = None) -> None:
        media = (url or "").strip().replace("\\/", "/")
        if not media or media in seen:
            return
        if "/get_file/" not in media:
            return
        low = media.lower()
        if "preview" in low or ".jpg" in low:
            return
        quality = _quality_from_stream(media, label)
        if quality.lower() == "auto":
            return
        seen.add(media)
        streams.append({"url": media, "quality": quality, "format": "mp4"})

    for source in soup.select("video source[src], video[src]"):
        _add(str(source.get("src") or ""), source.get("title") or source.get("label"))

    for media in _GET_FILE_RE.findall((html or "").replace("\\/", "/")):
        _add(media)

    best: dict[str, dict[str, str]] = {}
    for stream in streams:
        key = stream["quality"]
        if key not in best:
            best[key] = stream
    streams = list(best.values())
    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)

    direct = next((s for s in streams if s.get("format") in {"mp4", "hls"}), None)
    return {
        "streams": streams,
        "hls": None,
        "default": direct.get("url") if direct else None,
        "has_video": bool(streams),
    }


def _resolve_get_file_url_sync(get_file_url: str, referer: str) -> Optional[str]:
    raw = (get_file_url or "").strip()
    if not raw or "/get_file/" not in raw:
        return None
    headers = {
        "User-Agent": _HEADERS["User-Agent"],
        "Referer": referer if referer.startswith("http") else BASE_SITE,
        "Accept": "*/*",
        "Range": "bytes=0-0",
    }
    try:
        from curl_cffi.requests import Session

        for impersonate in _IMPERSONATIONS:
            try:
                with Session(impersonate=impersonate) as client:
                    resp = client.get(raw, headers=headers, timeout=15.0, allow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location") or resp.headers.get("location")
                    if loc and loc.startswith("http") and "/get_file/" not in loc.lower():
                        return loc
            except Exception:
                continue
    except Exception:
        return None
    return None


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    return await asyncio.to_thread(_resolve_get_file_url_sync, get_file_url, referer)


def _stream_format_for_url(url: str, original: str = "mp4") -> str:
    low = (url or "").lower()
    if ".m3u8" in low or "/hls/" in low or "mpegurl" in low:
        return "hls"
    return original


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "/get_file/" in (s.get("url") or "")
    ]
    if get_file_streams:
        get_file_streams = get_file_streams[:1]
    if not get_file_streams:
        return

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        return stream, await _resolve_get_file_url(stream["url"], referer=referer)

    pairs: list[tuple[dict[str, str], Optional[str]]] = []
    try:
        for stream in get_file_streams:
            pairs.append(await asyncio.wait_for(_resolve_one(stream), timeout=18.0))
    except Exception:
        if not pairs:
            pairs = [(s, None) for s in get_file_streams]

    seen_resolved: set[str] = set()
    for stream, resolved in pairs:
        if resolved:
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            stream["url"] = resolved
            stream["format"] = _stream_format_for_url(resolved, stream.get("format") or "mp4")

    if seen_resolved:
        streams[:] = [
            s
            for s in streams
            if s.get("format") != "mp4" or "/get_file/" not in (s.get("url") or "")
        ]

    streams[:] = [s for s in streams if s.get("format") != "embed"]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    default_stream = hls or mp4
    video["default"] = default_stream.get("url") if default_stream else None
    video["hls"] = hls.get("url") if hls else None
    video["has_video"] = bool(streams)


def _parse_card(block: Any, *, base: str, exclude_url: str | None = None) -> Optional[dict[str, Any]]:
    link = block.select_one('a[href*="/video/"]') if hasattr(block, "select_one") else None
    if link is None:
        return None
    abs_url = _normalize_video_href(link.get("href") or "", base=base)
    if not abs_url:
        return None
    if exclude_url and abs_url.rstrip("/") == exclude_url.rstrip("/"):
        return None

    img = link.find("img") or block.find("img")
    thumb = _best_image_url(img)
    title = _first_non_empty(
        link.get("title"),
        img.get("alt") if img else None,
        _text(block.select_one(".content_items a")),
    ) or "Unknown Video"
    duration = None
    views = None
    for li in block.select(".video-meta li"):
        text = _text(li) or ""
        if duration is None:
            found = _DURATION_RE.search(text)
            if found:
                duration = found.group(0)
        if views is None and li.select_one(".fa-eye"):
            views = _text(li.select_one("span")) or text
    uploader = _first_non_empty(
        _text(block.select_one(".content_items a span")),
        _text(block.select_one(".content_items a")),
    )
    return {
        "url": abs_url,
        "title": title.strip(),
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
    }


def _parse_cards(
    soup: BeautifulSoup,
    *,
    base: str,
    exclude_url: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in soup.select(".item.thumb-bl-video, .related-videos .item"):
        card = _parse_card(block, base=base, exclude_url=exclude_url)
        if not card or card["url"] in seen:
            continue
        seen.add(card["url"])
        items.append(card)
        if limit and len(items) >= limit:
            break
    return items


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    title = _first_non_empty(
        og_title,
        _text(soup.select_one("h1")),
        _text(soup.find("title")),
    )
    if title:
        for suffix in (" | PERFECTGIRLS.XXX", " - PERFECTGIRLS.XXX"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()

    description = _first_non_empty(og_desc, _meta(soup, name="description"))
    thumbnail = og_image
    duration = _normalize_duration(_meta(soup, prop="og:duration"))
    views = None
    uploader = None
    tags: list[str] = []
    category = None
    upload_date = None

    for obj in _parse_json_ld(soup):
        t = obj.get("@type")
        types = [str(x).lower() for x in t] if isinstance(t, list) else [str(t or "").lower()]
        if "videoobject" not in types:
            continue
        title = _first_non_empty(title, obj.get("name"))
        description = _first_non_empty(description, obj.get("description"))
        thumbnail = _first_non_empty(thumbnail, obj.get("thumbnailUrl"))
        duration = _normalize_duration(obj.get("duration")) or duration
        upload_date = _first_non_empty(upload_date, obj.get("uploadDate"))
        actor = obj.get("actor")
        if isinstance(actor, list) and actor:
            first = actor[0]
            if isinstance(first, dict):
                uploader = str(first.get("name") or "").strip() or None
            else:
                uploader = str(first).strip() or None
        elif isinstance(actor, str):
            uploader = actor.strip() or None
        if not uploader:
            author = obj.get("author")
            if isinstance(author, str):
                uploader = author.strip() or None
            elif isinstance(author, dict):
                uploader = str(author.get("name") or "").strip() or None
        tags = _as_list(obj.get("keywords"))
        interaction = obj.get("interactionStatistic")
        if isinstance(interaction, dict):
            count = interaction.get("userInteractionCount")
            if count is not None:
                views = str(count)
        break

    if not duration:
        holder = soup.select_one(".kt-player, .player")
        if holder and holder.get("data-duration"):
            duration = _normalize_duration(holder.get("data-duration"))

    if not tags:
        tags = _as_list(_meta(soup, name="keywords"))
    tags = list(dict.fromkeys([t for t in tags if t.lower() not in {"porn", "xxx", "hd"}]))
    if tags:
        category = tags[0]

    related = _parse_cards(soup, base=url, exclude_url=url, limit=12)
    video = _streams_from_page(html, soup)

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
        "related_videos": related,
        "video": video,
    }


async def scrape(url: str) -> dict[str, Any]:
    fetch_url = url
    if _is_embed_url(url):
        video_id = _extract_video_id(url)
        if video_id:
            fetch_url = f"{BASE_SITE}video/{video_id}/"
    html = await fetch_html(fetch_url, referer=BASE_SITE)
    data = parse_page(html, fetch_url)
    await _resolve_video_streams(data.get("video") or {}, referer=fetch_url)
    return data


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url or BASE_SITE, page)
    html = await fetch_html(page_url, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    items = _parse_cards(soup, base=page_url, limit=limit)
    if not items:
        seen: set[str] = set()
        for m in _VIDEO_HREF_RE.finditer(html or ""):
            href = _normalize_video_href(m.group(0), base=page_url)
            if not href or href in seen:
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
                }
            )
            if limit and len(items) >= limit:
                break
    return items[:limit] if limit else items
