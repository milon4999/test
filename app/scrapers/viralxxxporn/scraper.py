from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "viralxxxporn.com" or h.endswith(".viralxxxporn.com")


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
        "Referer": "https://viralxxxporn.com/",
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
            if "@graph" in parsed and isinstance(parsed["@graph"], list):
                out.extend([x for x in parsed["@graph"] if isinstance(x, dict)])
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
    return [str(value).strip()] if str(value).strip() else []


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
        return f"{m}:{s:02d}"
    if isinstance(value, str):
        v = value.strip()
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2) or 0)
            s = int(match.group(3) or 0)
            if h > 0:
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        return v or None
    return str(value).strip() or None


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (
        " - ViralXXXPorn",
        " | ViralXXXPorn",
        " - Viral XXX Porn",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


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


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _is_probable_video_post(parsed: Any) -> bool:
    path = parsed.path.rstrip("/")
    if not path or path == "":
        return False
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return False
    if segments[0] not in ("video", "short"):
        return False
    return True


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://viralxxxporn.com{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "viralxxxporn.com" not in parsed.netloc.lower():
        return None
    path = parsed.path.lower()
    if any(x in path for x in ("/wp-content/", "/wp-json/", "/tag/", "/category/", "/page/", "/author/", "/feed/", "/members/", "/models/", "/categories/", "/tags/", "/albums/", "/playlists/", "/shorts/")):
        return None
    if not _is_probable_video_post(parsed):
        return None
    return urlunparse(("https", "viralxxxporn.com", parsed.path.rstrip("/") + "/", "", "", ""))


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
            "trudigo",
            "ronracepub",
            "vast",
        )
    )


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/(?:video|embed|short)/(\d+)", url or "")
    return m.group(1) if m else None


def _embed_url(url: str) -> Optional[str]:
    vid = _extract_video_id(url)
    if vid:
        return f"https://viralxxxporn.com/embed/{vid}"
    return None


def _extract_streams(soup: BeautifulSoup, html: str, url: str = "", embed_url: str | None = None) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    video_id = _extract_video_id(url)

    def _is_current_video(src: str) -> bool:
        if "/contents/videos_screenshots/" in src:
            return False
        if not video_id:
            return True
        if "/get_file/" not in src:
            return True
        return f"/{video_id}/" in src or f"/{video_id}_" in src

    def _add(src: str, quality: str, fmt: str) -> None:
        if src in seen or not src.startswith("http"):
            return
        if not _is_current_video(src):
            return
        seen.add(src)
        streams.append({"url": src, "quality": quality, "format": fmt})

    for video in soup.select("video"):
        src = (video.get("src") or "").strip()
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin("https://viralxxxporn.com/", src)
            _add(src, _quality_from_url(src), "hls" if ".m3u8" in src.lower() else "mp4")
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin("https://viralxxxporn.com/", src)
            _add(src, _quality_from_url(src), "hls" if ".m3u8" in src.lower() else "mp4")

    for src in _extract_inline_urls(html):
        _add(src, _quality_from_url(src), "hls" if ".m3u8" in src.lower() else "mp4")

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin("https://viralxxxporn.com/", src)
        if _is_probable_ad_iframe(src):
            continue
        _add(src, f"Server {server_idx}", "embed")
        server_idx += 1

    if embed_url:
        _add(embed_url, "Embed", "embed")

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = item.get("quality") or ""
        digits = re.search(r"(\d{3,4})", q)
        quality_score = int(digits.group(1)) if digits else 0
        if fmt == "mp4":
            return (3, quality_score)
        if fmt == "hls":
            return (2, quality_score)
        return (1, -server_idx)

    streams = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in streams]
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


def parse_video_page(html: str, url: str, embed_url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)

    title = _clean_title(
        _first_non_empty(
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

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    upload_date = _first_non_empty(
        _meta(soup, prop="article:published_time"),
        _meta(soup, prop="article:modified_time"),
    )
    category = _meta(soup, prop="article:section")

    tags: list[str] = []
    for tag in soup.find_all("meta", attrs={"property": "article:tag"}):
        content = (tag.get("content") or "").strip()
        if content:
            tags.append(content)

    duration = None
    uploader = None
    views = None

    for obj in json_ld:
        types = obj.get("@type")
        type_names = [str(x).lower() for x in types] if isinstance(types, list) else [str(types).lower()]
        if "videoobject" in type_names or "blogposting" in type_names:
            title = _clean_title(_first_non_empty(title, obj.get("name"), obj.get("headline"))) or title
            description = _first_non_empty(description, obj.get("description"))

            thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
            if isinstance(thumb, list):
                thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
            thumbnail = _first_non_empty(thumbnail, thumb)

            duration = _first_non_empty(duration, _normalize_duration(obj.get("duration")))
            upload_date = _first_non_empty(upload_date, obj.get("datePublished"), obj.get("dateModified"))

            author = obj.get("author")
            if isinstance(author, dict):
                uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
            elif isinstance(author, str):
                uploader = author.strip() or None

            category = _first_non_empty(category, obj.get("articleSection"))
            tags.extend(_as_list(obj.get("keywords")))

    text_blob = soup.get_text(" ", strip=True)
    if not duration:
        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text_blob)
        if dm:
            duration = dm.group(0)
    if not views:
        views = _extract_views_text(text_blob)

    tags = list(dict.fromkeys([t for t in tags if t]))
    video = _extract_streams(soup, html, url, embed_url)

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


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    raw = (get_file_url or "").strip()
    if not raw or "/get_file/" not in raw:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Referer": referer if referer.startswith("http") else "https://viralxxxporn.com/",
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


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    result = parse_video_page(html, url, _embed_url(url))
    await _resolve_video_streams(result["video"], referer=url)
    return result


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "viralxxxporn.com"
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/\d+/?$", "/", path or "/")

    paged_path = clean_path.rstrip("/") + f"{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        container = a.find_parent(["article", "li", "div"]) or a
        img = a.find("img") or (container.find("img") if container else None)
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        ctext = container.get_text(" ", strip=True) if container else ""
        duration = None
        views = None

        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", ctext)
        if dm:
            duration = dm.group(0)

        views = _extract_views_text(ctext)

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