from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://watchporn.to/"
SITE_ALIASES = frozenset({"watchporn.to", "www.watchporn.to"})

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_IMPERSONATIONS = ("chrome", "chrome120", "chrome110")

_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?watchporn\.to)?/video/(?P<id>\d+)(?:/(?P<slug>[^/?#]+))?/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?watchporn\.to)?/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_PAIR_RE = re.compile(
    r"([\w.]+)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_VIEWS_RE = re.compile(r"([\d]+(?:\.\d+)?\s*[KMB]?)", re.IGNORECASE)
_QUALITY_IN_URL_RE = re.compile(r"_(\d{3,4})p\.mp4", re.IGNORECASE)

_RESERVED_SEGMENTS = frozenset(
    {
        "latest-updates",
        "top-rated",
        "most-popular",
        "categories",
        "category",
        "models",
        "model",
        "sites",
        "channels",
        "tags",
        "tag",
        "search",
        "members",
        "playlists",
        "albums",
        "embed",
        "login",
        "login-required",
        "logout",
        "signup",
        "rss",
        "static",
        "player",
        "get_file",
        "contents",
        "terms",
        "dmca",
        "2257",
        "community",
        "private",
        "premium",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".watchporn.to")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str, *, referer: str | None = None) -> str:
    from curl_cffi.requests import AsyncSession

    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for imp in _IMPERSONATIONS:
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=25.0) as client:
                resp = await client.get(url)
                if resp.status_code in (403, 429, 503):
                    last_error = RuntimeError(f"HTTP {resp.status_code} with {imp}")
                    continue
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            last_error = e
            continue
    raise last_error or RuntimeError(f"Failed to fetch {url}")


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
    if "fhd" in text or "1080" in text:
        return 1080
    if "hd" in text:
        return 720
    if "sd" in text:
        return 480
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
    if host and host not in SITE_ALIASES:
        return None
    m = _VIDEO_HREF_RE.search(parsed.path or "")
    if not m:
        return None
    video_id = m.group("id")
    slug = (m.group("slug") or "").strip("/")
    path = f"/video/{video_id}/{slug}/" if slug else f"/video/{video_id}/"
    return urlunparse(("https", "watchporn.to", path, "", "", ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-src", "data-lazy", "data-webp", "src"):
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
    host = parsed.netloc or "watchporn.to"
    parts = _path_parts(parsed.path)
    page_num = max(int(page or 1), 1)

    if parts and parts[-1].isdigit() and parts[-1] not in {"2257"}:
        if not (len(parts) >= 2 and parts[0] == "video"):
            parts = parts[:-1]
        if len(parts) >= 2 and parts[0] == "models" and parts[1].isdigit() and len(parts) == 2:
            pass

    if page_num <= 1:
        if not parts:
            new_path = "/"
        else:
            new_path = "/" + "/".join(parts) + "/"
    else:
        if not parts:
            new_path = f"/latest-updates/{page_num}/"
        else:
            new_path = "/" + "/".join(parts + [str(page_num)]) + "/"

    query = "&".join(f"{k}={v}" for k, v in parse_qsl(parsed.query, keep_blank_values=True))
    return urlunparse((parsed.scheme or "https", host, new_path, "", query, ""))


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = re.search(r"var\s+flashvars\s*=\s*\{(.*?)\};", html or "", re.S)
    body = m.group(1) if m else (html or "")
    for key, value in _FLASHVARS_PAIR_RE.findall(body):
        k = key.strip().lower()
        if k not in out and value:
            out[k] = value.strip()
    return out


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
    if label and re.search(r"\d", label):
        text = label.strip()
        if text.isdigit():
            return f"{text}p"
        return text
    m = _QUALITY_IN_URL_RE.search(url or "")
    if m:
        return f"{m.group(1)}p"
    return "mp4"


def _streams_from_flashvars(flash: dict[str, str], video_id: Optional[str]) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, label: Optional[str]) -> None:
        media = (url or "").strip().replace("\\/", "/")
        if not media or media in seen:
            return
        if "/get_file/" not in media:
            return
        low = media.lower()
        if "preview" in low or ".jpg" in low or "screenshot" in low:
            return
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _quality_from_stream(media, label),
                "format": "mp4",
            }
        )

    keys = ["video_url"] + [f"video_alt_url{n}" for n in ("", "2", "3", "4", "5")]
    for key in keys:
        url = flash.get(key)
        if not url:
            continue
        text_key = f"{key}_text"
        _add(url, flash.get(text_key))

    if video_id:
        embed = f"{BASE_SITE}embed/{video_id}"
        streams.append({"url": embed, "quality": "embed", "format": "embed"})

    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
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
        from curl_cffi.requests import AsyncSession

        async with AsyncSession(impersonate="chrome", headers=headers, timeout=15.0) as client:
            resp = await client.get(raw, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location") or resp.headers.get("location")
                if loc and loc.startswith("http") and "/get_file/" not in loc.lower():
                    return loc
    except Exception:
        return None
    return None


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "/get_file/" in (s.get("url") or "")
    ]
    if not get_file_streams:
        return

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        return stream, await _resolve_get_file_url(stream["url"], referer=referer)

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
            streams.remove(stream)

    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    video["default"] = (mp4 or embed or {}).get("url") if (mp4 or embed) else None
    video["hls"] = None
    video["has_video"] = bool(streams)


def _parse_card(block: Any, *, base: str, exclude_url: str | None = None) -> Optional[dict[str, Any]]:
    link = block if getattr(block, "name", "") == "a" else block.find("a", href=True)
    if not link:
        return None
    href = link.get("href") or ""
    abs_url = _normalize_video_href(href, base=base)
    if not abs_url:
        return None
    if exclude_url and abs_url.rstrip("/") == exclude_url.rstrip("/"):
        return None

    img = link.find("img") or block.find("img")
    thumb = _best_image_url(img)
    title = _first_non_empty(
        _text(block.select_one(".thumb__title")),
        link.get("title"),
        img.get("alt") if img else None,
    ) or "Unknown Video"

    duration = None
    info_item = block.select_one(".thumb__info-item")
    if info_item:
        duration = _normalize_duration(_text(info_item))
    if not duration:
        duration = _normalize_duration(block.get_text(" ", strip=True))

    views = None
    for meta in block.select(".thumb__meta-item"):
        txt = _text(meta) or ""
        if _DURATION_RE.search(txt):
            continue
        if "%" in txt:
            continue
        m = _VIEWS_RE.search(txt.replace(" ", ""))
        if m and any(ch.isdigit() for ch in m.group(1)):
            views = m.group(1).replace(" ", "")
            break

    return {
        "url": abs_url,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
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
    for block in soup.select("div.thumb.item, div.item.thumb, .grid__list .thumb"):
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
    flash = _parse_flashvars(html)

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    title = _first_non_empty(
        flash.get("video_title"),
        og_title,
        _text(soup.find("h1")),
        _text(soup.find("title")),
    )
    if title:
        for suffix in (" - WatchPorn", " | WatchPorn"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()

    description = _first_non_empty(og_desc, _meta(soup, name="description"))
    thumbnail = _first_non_empty(flash.get("preview_url"), og_image)
    duration = None
    views = None
    uploader = flash.get("video_models")
    if uploader and "," in uploader:
        uploader = uploader.split(",", 1)[0].strip()
    tags = _as_list(flash.get("video_tags"))
    categories = _as_list(flash.get("video_categories"))
    category = categories[0] if categories else None
    upload_date = None

    for obj in _parse_json_ld(soup):
        t = obj.get("@type")
        types = [str(x).lower() for x in t] if isinstance(t, list) else [str(t or "").lower()]
        if "videoobject" not in types:
            continue
        title = _first_non_empty(title, obj.get("name"))
        description = _first_non_empty(description, obj.get("description"))
        thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)
        duration = _normalize_duration(obj.get("duration")) or duration
        upload_date = _first_non_empty(upload_date, obj.get("uploadDate"))
        actor = obj.get("actor")
        if not uploader and isinstance(actor, list) and actor:
            first = actor[0]
            if isinstance(first, dict):
                uploader = first.get("name")
            elif isinstance(first, str):
                uploader = first
        elif not uploader and isinstance(actor, dict):
            uploader = actor.get("name")
        if not tags:
            tags = _as_list(obj.get("keywords"))
        if not category:
            genre = obj.get("genre")
            if isinstance(genre, list) and genre:
                category = str(genre[0]).strip() or None
            elif isinstance(genre, str):
                category = genre.strip() or None
        break

    if not duration:
        holder = soup.select_one(".player, .video-holder, #kt_player")
        if holder:
            duration = _normalize_duration(holder.get_text(" ", strip=True))

    if not views:
        for item in soup.select(".single__content-meta-item"):
            if item.select_one(".icon-eye"):
                raw = _text(item.select_one("span")) or _text(item)
                m = _VIEWS_RE.search((raw or "").replace(" ", ""))
                if m:
                    views = m.group(1).replace(" ", "")
                    break

    tags = list(dict.fromkeys(tags))
    related = _parse_cards(soup, base=url, exclude_url=url, limit=12)
    video_id = flash.get("video_id") or _extract_video_id(url)
    video = _streams_from_flashvars(flash, video_id)

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
            fetch_url = f"{BASE_SITE}embed/{video_id}"

    html = await fetch_html(fetch_url, referer=BASE_SITE)
    if _is_embed_url(fetch_url):
        flash = _parse_flashvars(html)
        logo = flash.get("logo_url") or ""
        canon = _normalize_video_href(logo) if "/video/" in logo else None
        if not canon:
            soup = BeautifulSoup(html, "lxml")
            canon = _normalize_video_href(_meta(soup, prop="og:url") or "")
        if canon:
            html = await fetch_html(canon, referer=BASE_SITE)
            fetch_url = canon

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
            parts = _path_parts(urlparse(href).path)
            if parts and parts[0] in _RESERVED_SEGMENTS:
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
