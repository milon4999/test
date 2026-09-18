from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.exeporn.net/"
SITE_ALIASES = frozenset({"exeporn.net", "www.exeporn.net"})
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
    r"(?:https?://(?:www\.)?exeporn\.net)?/video/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?exeporn\.net)?/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_PAIR_RE = re.compile(r"([\w.]+)\s*:\s*'((?:\\'|[^'])*)'", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_QUALITY_IN_URL_RE = re.compile(r"(?:-|_)(\d{3,4})p?\.mp4", re.IGNORECASE)
_RESERVED_SEGMENTS = frozenset(
    {
        "videos",
        "categories",
        "pornstars",
        "search",
        "embed",
        "login",
        "favorites",
        "blog",
        "contact",
        "abuse",
        "theme",
        "images",
        "ktplayer",
        "get_stream",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".exeporn.net")


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


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None, itemprop: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if itemprop:
        tag = soup.find("meta", attrs={"itemprop": itemprop})
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
    if host and host not in {"exeporn.net", "www.exeporn.net"}:
        return None
    m = _VIDEO_HREF_RE.search(parsed.path or "")
    if not m:
        return None
    slug = m.group("slug").strip("/")
    if not slug or slug.lower() in _RESERVED_SEGMENTS:
        return None
    return urlunparse(("https", "www.exeporn.net", f"/video/{slug}/", "", "", ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-srcset", "data-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip().split()[0]
        if not url or url.startswith("data:"):
            continue
        low = url.lower()
        if "logo" in low or "/theme/" in low:
            continue
        return urljoin(BASE_SITE, url)
    return None


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").split("/") if p]


def _build_list_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url or BASE_SITE)
    host = parsed.netloc or "www.exeporn.net"
    parts = _path_parts(parsed.path)
    page_num = max(int(page or 1), 1)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "page"]

    orig_path = parsed.path or "/"
    had_slash = orig_path.endswith("/")

    if parts and parts[0].lower() == "video":
        new_path = "/" + "/".join(parts) + "/"
        return urlunparse((parsed.scheme or "https", host, new_path, "", urlencode(query), ""))

    if not parts:
        new_path = "/videos/" if page_num > 1 else "/"
    else:
        new_path = "/" + "/".join(parts) + ("/" if had_slash else "")

    if page_num > 1:
        query.append(("page", str(page_num)))
    return urlunparse((parsed.scheme or "https", host, new_path, "", urlencode(query), ""))


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = re.search(r"var\s+flashvars\s*=\s*\{(.*?)\};", html or "", re.S)
    body = m.group(1) if m else (html or "")
    for key, value in _FLASHVARS_PAIR_RE.findall(body):
        k = key.strip().lower()
        val = value.replace("\\'", "'").strip()
        if k not in out and val:
            out[k] = val
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


def _streams_from_flashvars(flash: dict[str, str]) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str, label: Optional[str] = None) -> None:
        media = (url or "").strip().replace("\\/", "/")
        if not media or media in seen:
            return
        low = media.lower()
        if "/embed/" in low or "preview" in low or ".jpg" in low:
            return
        if "/get_stream/" not in low and ".mp4" not in low and ".m3u8" not in low:
            return
        seen.add(media)
        fmt = "hls" if (".m3u8" in low or "/hls/" in low) else "mp4"
        streams.append({"url": media, "quality": _quality_from_stream(media, label), "format": fmt})

    keys = ["video_url"] + [f"video_alt_url{n}" for n in ("", "2", "3", "4", "5")]
    for key in keys:
        url = flash.get(key)
        if not url:
            continue
        _add(url, flash.get(f"{key}_text"))

    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
    direct = next((s for s in streams if s.get("format") in {"mp4", "hls"}), None)
    return {
        "streams": streams,
        "hls": None,
        "default": direct.get("url") if direct else None,
        "has_video": bool(streams),
    }


def _finalize_video_streams(video: dict[str, Any], *, referer: str) -> None:
    """Keep signed /get_stream/ URLs. Do not resolve vkuser.net on the server:
    those redirects are IP-locked and fail in the player. The client must
    request get_stream with Referer set to the watch page (or site root).
    """
    streams: list[dict[str, str]] = video.get("streams") or []
    streams[:] = [s for s in streams if s.get("format") != "embed"]
    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    default_stream = hls or mp4
    video["default"] = default_stream.get("url") if default_stream else None
    video["hls"] = hls.get("url") if hls else None
    video["has_video"] = bool(streams)
    video["referer"] = referer if referer.startswith("http") else BASE_SITE


def _parse_card(block: Any, *, base: str, exclude_url: str | None = None) -> Optional[dict[str, Any]]:
    href = ""
    title = None
    if getattr(block, "name", None) == "a":
        href = block.get("href") or ""
        title = block.get("title")
        link = block
    else:
        link = block.select_one('a[href*="/video/"]') if hasattr(block, "select_one") else None
        if link is None:
            return None
        href = link.get("href") or ""
        title = link.get("title")
    abs_url = _normalize_video_href(href, base=base)
    if not abs_url:
        return None
    if exclude_url and abs_url.rstrip("/") == exclude_url.rstrip("/"):
        return None

    img = (link.find("img") if link is not None else None) or (block.find("img") if hasattr(block, "find") else None)
    thumb = _best_image_url(img)
    title = _first_non_empty(title, img.get("alt") if img else None, _text(block.select_one(".cards__title") if hasattr(block, "select_one") else None)) or "Unknown Video"
    duration = _normalize_duration(_text(block.select_one(".card__info_time") if hasattr(block, "select_one") else None))
    return {
        "url": abs_url,
        "title": title.strip(),
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
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
    for block in soup.select("a.cards__item.thumb, .related_videos a.cards__item"):
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
        _text(soup.select_one("h1.video-title, h1.title")),
        _text(soup.find("title")),
    )
    if title:
        for suffix in (" | eXePorn", " - eXePorn", " | EXEPorn"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()

    description = _first_non_empty(og_desc, _meta(soup, itemprop="description"), _meta(soup, name="description"))
    thumbnail = og_image or None
    thumb_link = soup.find("link", attrs={"itemprop": "thumbnailUrl"})
    if thumb_link and thumb_link.get("href"):
        thumbnail = _first_non_empty(thumbnail, thumb_link.get("href"))
    duration = _normalize_duration(_meta(soup, prop="og:duration") or _meta(soup, itemprop="duration"))
    if not duration:
        duration = _normalize_duration(_text(soup.select_one(".card__info_time")))
    views = None
    uploader = None
    tags = _as_list(_meta(soup, itemprop="keywords"))
    category = tags[0] if tags else None
    upload_date = _meta(soup, itemprop="uploadDate")

    flash = _parse_flashvars(html)
    if flash.get("video_title"):
        title = _first_non_empty(title, flash.get("video_title"))
    if flash.get("video_categories"):
        tags = list(dict.fromkeys(tags + _as_list(flash.get("video_categories"))))
        if not category and tags:
            category = tags[0]
    video = _streams_from_flashvars(flash)
    related = _parse_cards(soup, base=url, exclude_url=url, limit=12)

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
    html = await fetch_html(fetch_url, referer=BASE_SITE)
    if _is_embed_url(url):
        canonical = None
        soup = BeautifulSoup(html, "lxml")
        link = soup.find("link", attrs={"rel": "canonical"})
        if link and link.get("href"):
            canonical = _normalize_video_href(str(link.get("href")), base=BASE_SITE)
        if not canonical:
            item = soup.find("link", attrs={"itemprop": "url"})
            if item and item.get("href"):
                canonical = _normalize_video_href(str(item.get("href")), base=BASE_SITE)
        if canonical:
            fetch_url = canonical
            html = await fetch_html(fetch_url, referer=BASE_SITE)
    data = parse_page(html, fetch_url)
    _finalize_video_streams(data.get("video") or {}, referer=fetch_url)
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
