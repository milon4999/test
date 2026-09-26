from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


BASE_SITE = "https://www.momvids.com/"
SITE_HOST = "www.momvids.com"
SITE_ALIASES = frozenset(
    {
        "momvids.com",
        "www.momvids.com",
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


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h == "momvids.com" or h.endswith(".momvids.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = BASE_SITE) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer and referer.startswith("http"):
        headers["Referer"] = referer

    # MomVids blocks the aiohttp connection-pool TLS fingerprint (403).
    # Use curl_cffi with a real browser impersonation profile first, and
    # fall back to the shared pool otherwise.
    try:
        from curl_cffi.requests import AsyncSession

        for imp in ("chrome124", "chrome120", "safari15_3"):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=30.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.text
            except Exception:
                continue
    except Exception:
        pass

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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(key)
        if v and str(v).strip():
            url = str(v).strip()
            if url.startswith("//"):
                return f"https:{url}"
            return url
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" | MomVids.com", " - MomVids.com", " | MomVids", " - MomVids"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_duration(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        total = int(v)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    raw = str(v).strip()
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", raw)
    if m:
        return m.group(0)
    return raw or None


def _clean_views_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    txt = str(v).strip()
    if not txt:
        return None
    txt = txt.replace("\u00a0", " ")
    m = re.search(r"(\d[\d\s,\.]*\s*[KMBkmb]?)", txt)
    if not m:
        return None
    val = m.group(1).replace(" ", "").replace(",", "")
    val = re.sub(r"[^0-9KMBkmb\.]", "", val)
    return val.upper() or None


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for a in soup.select('a[href*="/search/"]'):
        t = a.get_text(" ", strip=True)
        if t and t not in tags:
            tags.append(t)
    # Fall back to meta keywords when the tag list is missing.
    if not tags:
        kw = _meta(soup, name="keywords")
        if kw:
            tags = [x.strip() for x in kw.split(",") if x.strip()]
    return tags


def _stream_quality_from_url(url: str, label: str | None = None) -> str:
    if label and str(label).strip():
        low = str(label).strip().lower()
        qm = re.search(r"(\d{3,4})p?", low)
        if qm:
            return f"{qm.group(1)}p"
    low = (url or "").lower()
    qm = re.search(r"([1-9]\d{2,3})p", low)
    if qm:
        return f"{qm.group(1)}p"
    return "default"


def _extract_streams(html: str, url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # The player exposes signed get_file links plus quality labels inside flashvars.
    # Capture both escaped and unescaped forms from inline scripts.
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    for m in re.finditer(r"https?://[^\s\"'<>]+(?:\.m3u8|\.mp4)[^\s\"'<>]*", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip().rstrip(",")
        if not u:
            continue
        if "/get_file/" in u.lower() or u.lower().endswith(".m3u8"):
            if u in seen:
                continue
            seen.add(u)
            is_hls = ".m3u8" in u.lower()
            streams.append(
                {
                    "url": u,
                    "quality": "adaptive" if is_hls else "source",
                    "format": "hls" if is_hls else "mp4",
                }
            )

    # Match flashvars entries to pull quality labels from video_url_text / video_alt_url_text.
    flashvars = {}
    fv_match = re.search(r"var\s+flashvars\s*=\s*\{", unescaped)
    if fv_match:
        body = unescaped[fv_match.end():]
        end = body.find("};")
        if end != -1:
            block = body[: end + 1]
            for key, value in re.findall(r"([A-Za-z0-9_]+)\s*:\s*'([^']*)'", block):
                flashvars[key] = value

    def _label_for(url: str, key_hint: str) -> Optional[str]:
        fv_url = flashvars.get(key_hint)
        if fv_url and urlparse(fv_url).path == urlparse(url).path:
            return flashvars.get(key_hint + "_text")
        return None

    for s in streams:
        if s["format"] != "mp4":
            continue
        label = _label_for(s["url"], "video_url") or _label_for(s["url"], "video_alt_url")
        s["quality"] = _stream_quality_from_url(s["url"], label)

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    streams.sort(key=_score, reverse=True)

    hls = next((s for s in streams if s.get("format") == "hls"), None)
    default = next((s for s in streams if s.get("format") == "mp4"), None)
    if not default:
        default = hls or (streams[0] if streams else None)
    default_url = default.get("url") if default else None

    return {
        "streams": streams,
        "hls": hls.get("url") if hls else None,
        "default": default_url,
        "has_video": bool(streams),
    }


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    """
    MomVids get_file URLs are behind Cloudflare and redirect to signed CDN
    playable links. The signing token lives in the query string, so the full
    URL must be preserved. Uses curl_cffi browser impersonation to pass the
    Cloudflare challenge; falls back to the shared pool when unavailable.
    """
    if not get_file_url:
        return None
    ref = referer.strip() if referer.strip().startswith("http") else BASE_SITE
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def _attempt(url: str, method: str, range_hdr: Optional[str]) -> Optional[str]:
        h = dict(headers)
        if range_hdr:
            h["Range"] = range_hdr
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession(impersonate="chrome124", headers=h, timeout=20.0) as client:
                if method == "HEAD":
                    resp = await client.head(url, allow_redirects=False)
                else:
                    resp = await client.get(url, allow_redirects=False)
        except Exception:
            return None
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if loc and loc.startswith("http"):
                return loc
        return None

    attempts = [
        (get_file_url, "HEAD", None),
        (get_file_url, "GET", "bytes=0-"),
        (get_file_url, "GET", "bytes=0-0"),
        (get_file_url.rstrip("/") + "/", "HEAD", None),
        (get_file_url.rstrip("/") + "/", "GET", "bytes=0-"),
        (get_file_url.rstrip("/") + "/", "GET", "bytes=0-0"),
    ]
    for u, method, rng in attempts:
        try:
            resolved = await asyncio.wait_for(_attempt(u, method, rng), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if not get_file_mp4:
        return

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _get_file_to_remote_playable(stream["url"], referer=referer)
        return stream, resolved

    resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
    for stream, resolved in resolved_pairs:
        if resolved:
            stream["url"] = resolved
        else:
            streams.remove(stream)

    remote_mp4 = [s for s in streams if s.get("format") == "mp4"]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if remote_mp4:
        video["default"] = remote_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(remote_mp4) or bool(hls) or bool(embed)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

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
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    # video:duration is in seconds on this site.
    duration = _normalize_duration(_meta(soup, prop="video:duration"))
    views = _clean_views_text(_meta(soup, prop="ya:ovs:views_total"))
    upload_date = _first_non_empty(_meta(soup, prop="ya:ovs:upload_date"))
    tags = _extract_tags(soup)

    uploader_name = None
    up = soup.select_one('a[href*="/members/"]')
    if up:
        uploader_name = up.get_text(" ", strip=True) or None

    video = _extract_streams(html, url)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": None,
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    data = parse_video_page(html, url)
    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=url)
    return data


def _normalize_video_href(href: str, *, host: str = SITE_HOST) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://{host}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "momvids.com" not in parsed.netloc.lower():
        return None
    if not re.match(r"^/videos/\d+/[^/]+/?$", parsed.path or "", flags=re.IGNORECASE):
        return None
    if parsed.query:
        return None

    return urlunparse(("https", host, parsed.path.rstrip("/") + "/", "", "", ""))


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/\d+/?$", "", path).rstrip("/")
    paged_path = clean_path + f"/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
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

        # The card anchor itself carries the title/name/info blocks; fall back
        # to the nearest card container only when the anchor is a bare thumbnail.
        container = a
        img = a.find("img")
        if not img:
            card = a.find_parent(["article", "li"])
            if card is not None:
                container = card
                img = card.find("img")
            else:
                container = a
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = container.select_one(".title") if container else None
        duration_el = container.select_one(".time") if container else None

        title = (
            (title_el.get_text(" ", strip=True) if title_el else None)
            or a.get("title")
            or (img.get("alt") if img else None)
            or a.get_text(" ", strip=True)
        )
        title = _clean_title(title) or "Unknown Video"

        ctext = container.get_text(" ", strip=True) if container else ""
        duration = _normalize_duration(duration_el.get_text(" ", strip=True) if duration_el else ctext)

        views = None
        count_el = container.select_one(".count") if container else None
        if count_el:
            views = _clean_views_text(count_el.get_text(" ", strip=True))
        if not views:
            vm = re.search(r"(\d[\d\s,\.]*\s*[KMBkmb]?)\s*(?:views|view)\b", ctext, re.IGNORECASE)
            if vm:
                views = _clean_views_text(vm.group(1))

        uploader = None
        name_el = container.select_one(".name") if container else None
        if name_el:
            txt = name_el.get_text(" ", strip=True)
            if txt and txt.lower() != "unknown":
                uploader = txt

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
            }
        )

    return items[:limit]
