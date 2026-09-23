from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_URL = "https://viralmms.com/"
CANONICAL_HOST = "viralmms.com"

_SUPPORTED_HOSTS = frozenset(
    {
        "viralmms.com",
        "www.viralmms.com",
    }
)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h


def can_handle(host: str) -> bool:
    h = _normalize_host(host)
    return h in _SUPPORTED_HOSTS or h.endswith(".viralmms.com")


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
        "Referer": BASE_URL,
    }
    # mydesi.com.co currently serves an expired TLS certificate; skip verification
    # (same workaround pattern as the mydesimms scraper) so listing/scrape still work.
    return await pool_fetch_html(url, headers=headers, ssl=False)


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


def _normalize_thumb(src: str | None) -> Optional[str]:
    """Resolve Next.js image-proxy URLs (/ _next/image?url=<encoded>) to the real CDN URL."""
    if not src:
        return None
    url = str(src).strip()
    if not url:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("/"):
        url = urljoin(BASE_URL, url)
    if "_next/image" in url or "url=" in url:
        try:
            parsed = urlparse(url)
            query = dict(parse_qsl(parsed.query))
            real = query.get("url")
            if real and real.startswith("http"):
                url = real
        except Exception:
            pass
    return url or None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (
        " - Viral MMS",
        " | Viral MMS",
        " - Viral MMS - Watch XXX Videos Online",
        " | Viral MMS - Watch XXX Videos Online",
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
    m = re.search(r"\b(\d[\d,\.]*\s*[KMBkmb])\b", text)
    if m:
        return _clean_views_text(m.group(1))
    m = re.search(r"\b(\d[\d,\.]*)\s*(?:views|view)\b", text, re.IGNORECASE)
    if m:
        return _clean_views_text(m.group(1))
    return None


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_URL, href)
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if _normalize_host(parsed.netloc) not in _SUPPORTED_HOSTS:
        return None
    path_low = (parsed.path or "").lower()
    if parsed.query:
        return None
    if any(
        x in path_low
        for x in (
            "/_next/",
            "/api/",
            "/page/",
            "/channels/",
            "/channels",
            "/explore",
            "/contact",
            "/privacy",
            "/dmca",
            "/about",
        )
    ):
        return None

    segments = [s for s in parsed.path.strip("/").split("/") if s]
    if len(segments) != 2 or segments[0].lower() != "post":
        return None
    slug = segments[1]
    return urlunparse(("https", CANONICAL_HOST, f"/post/{slug}", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            url = m.group(0).strip().rstrip("\\,;)]}")
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
            "trafficjunky",
            "exoclick",
            "juicyads",
            "adskeeper",
            "mgid.com",
            "propellerads",
            "adsterra",
            "hilltopads",
            "clickadu",
            "popads",
            "zoneid=",
            "/delivery/",
        )
    )


def _extract_streams(soup: BeautifulSoup, html: str, preferred_url: str | None = None) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    if preferred_url and preferred_url.startswith("http") and ".mp4" in preferred_url.lower():
        seen.add(preferred_url)
        streams.append({"url": preferred_url, "quality": _quality_from_url(preferred_url), "format": "mp4"})

    for video in soup.select("video"):
        src = (video.get("src") or "").strip()
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(BASE_URL, src)
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
                src = urljoin(BASE_URL, src)
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

    for a in soup.select("a[href]"):
        src = (a.get("href") or "").strip()
        if not src or ".mp4" not in src.lower():
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(BASE_URL, src)
        if not src.startswith("http") or src in seen:
            continue
        if any(x in src.lower() for x in ("/thumbnails/", "/poster/", ".jpg", ".png", ".webp")):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _quality_from_url(src), "format": "mp4"})

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(BASE_URL, src)
        if not src.startswith("http") or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    # The main video's JSON-LD contentUrl is authoritative; drop the related-video
    # MP4 URLs the inline scan also picks up so only the current video is exposed.
    if preferred_url and preferred_url in seen:
        streams = [s for s in streams if s.get("format") != "mp4" or s.get("url") == preferred_url]

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
    if preferred_url:
        match = next((s for s in materialized if s.get("url") == preferred_url), None)
        if match:
            default_url = preferred_url
    if not default_url:
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
    thumbnail = _normalize_thumb(thumbnail)

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
    preferred_url = None

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

            content_url = obj.get("contentUrl")
            if isinstance(content_url, str) and content_url.strip() and ".mp4" in content_url.lower():
                preferred_url = _first_non_empty(preferred_url, content_url.strip().rstrip("\\,;)]}"))

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
    video = _extract_streams(soup, html, preferred_url=preferred_url)

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
    netloc = parsed.netloc or CANONICAL_HOST
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = path.rstrip("/") or "/"

    m = re.match(r"^(/channels/[^/]+)/\d+$", clean_path)
    if m:
        return urlunparse((scheme, netloc, f"{m.group(1)}/{page}", "", urlencode(query_items), ""))

    m = re.match(r"^(/channels/[^/]+)$", clean_path)
    if m:
        return urlunparse((scheme, netloc, f"{m.group(1)}/{page}", "", urlencode(query_items), ""))

    if clean_path in ("/", ""):
        return urlunparse((scheme, netloc, f"/page/{page}", "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", clean_path)
    paged_path = clean_path.rstrip("/") + f"/page/{page}"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


_GENERIC_ALT = frozenset({"video thumbnail", "video", "thumbnail", "post thumbnail"})


def _extract_card_title(a: Any, container: Any, img: Any) -> Optional[str]:
    candidates: list[str] = []

    def _meaningful(t: str) -> bool:
        t = (t or "").strip()
        return bool(t) and len(t) >= 3 and t.lower() not in _GENERIC_ALT

    atext = a.get_text(" ", strip=True)
    if _meaningful(atext):
        candidates.append(atext)
    if container is not None:
        for h in container.select("h1,h2,h3,h4"):
            t = h.get_text(" ", strip=True)
            if _meaningful(t):
                candidates.append(t)
                break
        for link in container.select('a[href*="/post/"]'):
            t = link.get_text(" ", strip=True)
            if _meaningful(t):
                candidates.append(t)
                break
        for link in container.select("a[title]"):
            t = (link.get("title") or "").strip()
            if _meaningful(t):
                candidates.append(t)
                break
    at = (a.get("title") or "").strip()
    if _meaningful(at):
        candidates.append(at)
    if img is not None:
        alt = (img.get("alt") or "").strip()
        if _meaningful(alt):
            candidates.append(alt)
    for c in candidates:
        if _meaningful(c):
            return c
    return None


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
        thumb = _normalize_thumb(_best_image_url(img))
        if not thumb:
            continue

        title = _extract_card_title(a, container, img)
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
