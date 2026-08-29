from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://xxxdan.com/"
SITE_HOST = "xxxdan.com"
SITE_ALIASES = frozenset(
    {
        "xxxdan.com",
        "www.xxxdan.com",
        "xxxdan2.com",
        "www.xxxdan2.com",
    }
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_LANG_CODES = frozenset(
    {
        "ar",
        "cn",
        "cs",
        "de",
        "el",
        "es",
        "fa",
        "fr",
        "it",
        "ja",
        "ko",
        "no",
        "pl",
        "pt",
        "ru",
        "th",
        "tr",
        "tw",
    }
)

_RESERVED_PATHS = frozenset(
    {
        "2257",
        "channel",
        "channels",
        "contact",
        "dmca",
        "dump",
        "embed",
        "js",
        "newest",
        "page",
        "partners",
        "policy",
        "search",
        "straight",
        "tags",
        "terms",
        "trt",
        "vstat",
    }
) | _LANG_CODES

_VIDEO_HREF_RE = re.compile(
    r"(?:xxxdan(?:2)?\.com)/(?:(?P<lang>[a-z]{2})/)?"
    r"(?:embed/)?(?P<id>[A-Za-z0-9]{4,12})"
    r"(?:/(?P<slug>[^/?#]+)\.html)?",
    re.IGNORECASE,
)
_SOURCE_PUSH_RE = re.compile(
    r"sources\.push\(\s*\{[^}]*?src\s*:\s*['\"](?P<url>https?://[^'\"]+)['\"]",
    re.IGNORECASE,
)
_ISO_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE)
_POPULAR_RE = re.compile(r"^popular(\d+)$", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".xxxdan.com") or h.endswith(".xxxdan2.com")


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
    t = re.sub(r"^❤️\s*", "", t)
    for suffix in (" | XXXDan", " | xxxDan", " » Page 1"):
        if t.endswith(suffix):
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


def _normalize_site_host(host: str) -> str:
    h = (host or SITE_HOST).lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".xxxdan.com") or h.endswith(".xxxdan2.com"):
        return h
    return SITE_HOST


def _is_video_id(video_id: str | None) -> bool:
    vid = (video_id or "").strip()
    if not vid or vid.lower() in _RESERVED_PATHS:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9]{4,12}", vid))


def _extract_video_ref(url: str) -> tuple[Optional[str], str, Optional[str]]:
    m = _VIDEO_HREF_RE.search(url or "")
    if not m:
        return None, SITE_HOST, None
    video_id = m.group("id")
    if not _is_video_id(video_id):
        return None, SITE_HOST, None
    parsed = urlparse(url)
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    slug = m.group("slug")
    if slug and slug.lower() in _RESERVED_PATHS:
        return None, host, None
    return video_id, host, slug


def _canonical_video_url(video_id: str, slug: str | None, *, host: str = SITE_HOST) -> str:
    if slug:
        slug_part = slug.strip("/")
        if not slug_part.endswith(".html"):
            slug_part = f"{slug_part}.html"
        return f"https://{host}/{video_id}/{slug_part}"
    return f"https://{host}/embed/{video_id}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "xxxdan.com" not in href.lower() and "xxxdan2.com" not in href.lower():
        return None
    video_id, host, slug = _extract_video_ref(href)
    if not video_id:
        return None
    return _canonical_video_url(video_id, slug, host=host)


def _parse_iso_duration(value: str | None) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    m = _ISO_DURATION_RE.fullmatch(raw)
    if not m:
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
            return raw
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _stream_key(url: str) -> str:
    parsed = urlparse(_normalize_media_url(url))
    return f"{parsed.netloc}{parsed.path}".lower()


def _quality_from_url(url: str, label: str = "") -> str:
    label = (label or "").strip()
    if label and label.lower() not in {"auto", "default", "html5"}:
        mq = re.search(r"(\d{3,4})p?", label, re.IGNORECASE)
        if mq:
            return f"{mq.group(1)}p"
        return label
    mq = re.search(r"(\d{3,4})p", url or "", re.IGNORECASE)
    if mq:
        return f"{mq.group(1)}p"
    return "default"


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    seen: set[str] = set()

    for match in _SOURCE_PUSH_RE.finditer(html or ""):
        src = _normalize_media_url(match.group("url"))
        if not src:
            continue
        key = _stream_key(src)
        if key in seen:
            continue
        seen.add(key)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        quality = _quality_from_url(src)
        streams.append({"quality": quality, "url": src, "format": fmt})
        if fmt == "hls":
            hls_url = hls_url or src

    if not streams:
        for match in re.finditer(
            r"https?://[a-z0-9.-]*cdn3x\.com/xd/[A-Za-z0-9_./-]+",
            html or "",
            re.IGNORECASE,
        ):
            src = _normalize_media_url(match.group(0))
            if "/xd/" not in src or any(x in src.lower() for x in (".jpg", ".png", ".webp", ".gif", "/i/")):
                continue
            key = _stream_key(src)
            if key in seen:
                continue
            seen.add(key)
            fmt = "hls" if ".m3u8" in src.lower() else "mp4"
            streams.append({"quality": _quality_from_url(src), "url": src, "format": fmt})

    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(streams),
    }


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome124", "chrome120", "chrome110"):
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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-webp", "data-original", "data-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _parse_json_ld(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict) and x.get("@type") == "VideoObject"), None)
        if isinstance(data, dict) and str(data.get("@type") or "").lower() == "videoobject":
            return data
    return {}


def _parse_related(soup: BeautifulSoup, *, skip_url: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if skip_url:
        seen.add(skip_url)
    for card in soup.select("a.video-card[href]"):
        parsed = _parse_list_item(card)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)
        if len(items) >= 24:
            break
    return items


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_json_ld(html)

    title_el = soup.select_one("h1.video-title, .movie h1, h1")
    title = _clean_title(
        _first_non_empty(
            ld.get("name") if isinstance(ld.get("name"), str) else None,
            title_el.get_text(" ", strip=True) if title_el else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        ld.get("thumbnailUrl") if isinstance(ld.get("thumbnailUrl"), str) else None,
        _meta(soup, prop="og:image"),
    )
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    time_el = soup.select_one(".video-desc__stat time[datetime], .video-desc time[datetime]")
    duration = _parse_iso_duration(
        _first_non_empty(
            ld.get("duration") if isinstance(ld.get("duration"), str) else None,
            time_el.get("datetime") if time_el else None,
            time_el.get_text(strip=True) if time_el else None,
        )
    )

    tags: list[str] = []
    for link in soup.select(".tag-row a[rel='tag'], .tag-row__item, a.related-tags__item"):
        txt = link.get_text(strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    upload_date = None
    if isinstance(ld.get("uploadDate"), str):
        upload_date = ld.get("uploadDate")

    video_id_el = soup.select_one("#video-id, input[name='id']")
    video_id = None
    if video_id_el and video_id_el.get("value"):
        video_id = str(video_id_el.get("value")).strip()
    if not video_id:
        video_id, _, _ = _extract_video_ref(url)

    page_url = url
    if isinstance(ld.get("url"), str):
        page_url = _normalize_video_href(str(ld.get("url"))) or url
    elif video_id:
        slug_m = re.search(r"/[A-Za-z0-9]{4,12}/([^/?#]+\.html)", url)
        slug = slug_m.group(1)[:-5] if slug_m else None
        page_url = _canonical_video_url(video_id, slug)

    return {
        "url": page_url,
        "title": title,
        "description": _first_non_empty(_meta(soup, name="description"), _meta(soup, prop="og:description")),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "category": tags[0] if tags else None,
        "tags": tags,
        "upload_date": upload_date,
        "video": _extract_video_streams(html),
        "related_videos": _parse_related(soup, skip_url=page_url),
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id, host, slug = _extract_video_ref(url)
    if video_id:
        target = url if url.startswith("http") else _canonical_video_url(video_id, slug, host=host)
        html = await fetch_page(target, referer=BASE_SITE)
        data = parse_video_page(html, target)
        if data.get("video", {}).get("has_video") or slug:
            return data
        embed_url = f"https://{host}/embed/{video_id}"
        html = await fetch_page(embed_url, referer=BASE_SITE)
        return parse_video_page(html, embed_url)

    html = await fetch_page(url, referer=url)
    return parse_video_page(html, url)


def _strip_lang_parts(parts: list[str]) -> list[str]:
    if parts and parts[0].lower() in _LANG_CODES:
        return parts[1:]
    return parts


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    query = parse_qs(parsed.query)
    parts = _strip_lang_parts([p for p in (parsed.path or "/").strip("/").split("/") if p])

    search_q = _first_non_empty(
        *(query.get("query") or []),
        *(query.get("q") or []),
        *(query.get("s") or []),
    )
    if parts and parts[0].lower() == "search":
        if len(parts) >= 2 and parts[1] and not parts[1].isdigit():
            search_q = search_q or unquote(parts[1])
        if not search_q:
            new_path = "/search"
            return urlunparse(("https", host, new_path, "", parsed.query, ""))
        encoded = quote(search_q, safe="")
        new_path = f"/search/{encoded}" if page_num <= 1 else f"/search/{encoded}/{page_num}"
        return urlunparse(("https", host, new_path, "", "", ""))

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if parts and _POPULAR_RE.match(parts[-1]):
        parts[-1] = f"popular{page_num}"
        new_path = "/" + "/".join(parts)
        return urlunparse(("https", host, new_path, "", "", ""))

    if not parts:
        if page_num <= 1:
            return f"https://{host}/"
        return f"https://{host}/straight/trending/{page_num}"

    if page_num <= 1:
        new_path = "/" + "/".join(parts)
        return urlunparse(("https", host, new_path, "", "", ""))

    new_path = "/" + "/".join(parts + [str(page_num)])
    return urlunparse(("https", host, new_path, "", "", ""))


def _parse_list_item(card: Any) -> Optional[dict[str, Any]]:
    href = _normalize_video_href(str(card.get("href") or ""))
    if not href:
        return None

    img = card.select_one("img")
    title_el = card.select_one(".video-card__title")
    duration_el = card.select_one(".video-card__duration")
    title = _clean_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
            card.get("title"),
        )
    ) or "Unknown Video"
    duration = duration_el.get_text(strip=True) if duration_el else None
    return {
        "url": href,
        "title": title,
        "thumbnail_url": _best_image_url(img),
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "preview_url": None,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in soup.select("a.video-card[href]"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(card)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    return items[:limit]
