
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_SITE = "https://www.fpo.xxx/"
SITE_ALIASES = frozenset({"fpo.xxx", "www.fpo.xxx"})

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

# fpo.xxx blocks plain httpx/aiohttp TLS fingerprints (ConnectTimeout).
# Only browser-impersonated clients (curl_cffi) get through.
_IMPERSONATIONS = ("chrome", "chrome120", "chrome110", "safari15_3")

# KVS flashvars (var flashvars = { video_url: '...', video_alt_url_text: 'HQ', ... })
_FLASHVARS_PAIR_RE = re.compile(
    r"[\w.]*?(video_id|video_title|video_categories|video_tags|video_models|"
    r"video_url_text|video_url|video_alt_url_text|video_alt_url|"
    r"video_alt_url2_text|video_alt_url2|video_alt_url3_text|video_alt_url3|"
    r"logo_url|preview_url)\s*"
    r"[:=]\s*['\"]([^'\"]*)['\"]",
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(r"https?://(?:www\.)?fpo\.xxx/get_file/[^\s\"'<>\\]+", re.IGNORECASE)
_M3U8_RE = re.compile(r"https?://[^\s\"'<>\\]+\.m3u8[^\s\"'<>\\]*", re.IGNORECASE)

# URL kind detection: /video/{id}/{slug}/, /embed/{id}, /get_file/.../{folder}/{id}/{file}.mp4
_EMBED_URL_RE = re.compile(r"fpo\.xxx/embed/(\d+)", re.IGNORECASE)
_VIDEO_PAGE_ID_RE = re.compile(r"fpo\.xxx/video/(\d+)", re.IGNORECASE)
_GET_FILE_ID_RE = re.compile(r"fpo\.xxx/get_file/\d+/[\w]+/\d+/(\d+)", re.IGNORECASE)
_GET_FILE_FILENAME_ID_RE = re.compile(r"get_file/[^\s?&]+/(\d{4,})\.(?:mp4|m3u8)", re.IGNORECASE)

_STREAM_FIELD_PAIRS = (
    ("video_url", "video_url_text"),
    ("video_alt_url", "video_alt_url_text"),
    ("video_alt_url2", "video_alt_url2_text"),
    ("video_alt_url3", "video_alt_url3_text"),
)

# Views text like "242 515" / "242,515" / "1.2M"
_VIEWS_RE = re.compile(r"(\d[\d\s,.]*)(\s*[KMB])?", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".fpo.xxx")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    """Fetch HTML with browser TLS impersonation (plain httpx gets blocked)."""
    from curl_cffi.requests import AsyncSession

    last_error: Exception | None = None
    for imp in _IMPERSONATIONS:
        try:
            async with AsyncSession(impersonate=imp, headers=_HEADERS, timeout=25.0) as client:
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


def _extract_video_id(url: str) -> Optional[str]:
    """Video id from a /video/, /embed/ or /get_file/ fpo.xxx URL."""
    for pattern in (_VIDEO_PAGE_ID_RE, _EMBED_URL_RE, _GET_FILE_ID_RE, _GET_FILE_FILENAME_ID_RE):
        m = pattern.search(url or "")
        if m:
            return m.group(1)
    return None


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_URL_RE.search(url or ""))


def _is_get_file_url(url: str) -> bool:
    return bool(_GET_FILE_RE.search(url or ""))


async def _canonical_from_embed(video_id: str) -> Optional[str]:
    """
    Resolve the canonical /video/{id}/{slug}/ URL for a video id.

    /video/{id}/ without the slug returns 404 on fpo.xxx, but the embed page
    exposes the canonical URL as its `logo_url` flashvar.
    """
    try:
        html = await fetch_html(f"{BASE_SITE}embed/{video_id}/")
    except Exception:
        return None
    flash = _parse_flashvars(html)
    logo = flash.get("logo_url")
    if logo and "/video/" in logo:
        return logo
    return None


def _direct_stream_result(url: str) -> dict[str, Any]:
    """Minimal result for a direct /get_file/ media link that cannot be canonicalized."""
    return {
        "url": url,
        "title": None,
        "description": None,
        "thumbnail_url": None,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": [],
        "upload_date": None,
        "related_videos": [],
        "video": {
            "streams": [{"quality": "default", "url": url, "format": "mp4"}],
            "hls": None,
            "default": url,
            "has_video": True,
        },
    }


def _parse_embed_page(html: str, url: str) -> dict[str, Any]:
    """Parse the /embed/{id}/ player page (has flashvars but no page metadata)."""
    flash = _parse_flashvars(html)
    video_id = flash.get("video_id") or _extract_video_id(url)

    streams: list[dict[str, Any]] = []
    raw = flash.get("video_url")
    if raw:
        media = raw.replace("\\/", "/")
        streams.append(
            {
                "quality": _normalize_quality_label(flash.get("video_url_text"), media),
                "url": media,
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )
    if video_id:
        streams.append(
            {"quality": "embed", "url": f"{BASE_SITE}embed/{video_id}/", "format": "embed"}
        )

    return {
        "url": url,
        "title": flash.get("video_title"),
        "description": None,
        "thumbnail_url": flash.get("preview_url"),
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": [],
        "upload_date": None,
        "related_videos": [],
        "video": {
            "streams": streams,
            "hls": None,
            "default": streams[0]["url"] if streams else None,
            "has_video": bool(streams),
        },
    }


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
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


def _itemprop(soup: BeautifulSoup, prop: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"itemprop": prop})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def _parse_flashvars(html: str) -> dict[str, str]:
    """Parse KVS flashvars pairs (video_url, video_alt_url*, *_text, tags...)."""
    out: dict[str, str] = {}
    for key, value in _FLASHVARS_PAIR_RE.findall(html or ""):
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
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
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
        return v or None
    return str(seconds_or_iso).strip() or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-original", "data-src", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip() and not str(v).startswith("data:"):
            return str(v).strip()
    return None


def _find_duration_like_text(text: str) -> Optional[str]:
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _clean_count(text: str) -> Optional[str]:
    """Normalize '242 515' / '242,515' / '1.2M' style counts."""
    if not text:
        return None
    m = _VIEWS_RE.search(text)
    if not m:
        return None
    val = re.sub(r"[\s,]", "", m.group(1))
    suf = (m.group(2) or "").upper().strip()
    return f"{val}{suf}" if suf else val


def _normalize_quality_label(label: str | None, url: str = "") -> str:
    text = str(label or "").strip()
    if text.isdigit():
        return f"{text}p"
    mq = re.search(r"(\d{3,4})[pP]", text)
    if mq:
        return f"{mq.group(1)}p"
    # Resolution encoded in the file name: ..._720p.mp4 / ..._720m.mp4
    mq = re.search(r"_(\d{3,4})[pm]\.mp4", url, re.IGNORECASE)
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"-(\d{3,4})\.mp4", url, re.IGNORECASE)
    if mq:
        return f"{mq.group(1)}p"
    if text:
        return text
    return "default"


def _quality_rank(label: str | None) -> int:
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    return int(digits) if digits else 0


def _extract_video_urls(html: str, video_id: Optional[str] = None) -> dict[str, Any]:
    """
    Extract video stream URLs from the KVS player flashvars:

        video_url: '<LQ mp4>', video_url_text: 'LQ',
        video_alt_url: '<HQ 720p mp4>', video_alt_url_text: 'HQ', ...

    Returns {"streams": [...], "hls": ..., "default": ..., "has_video": bool}
    """
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()

    flash = _parse_flashvars(html)
    if not video_id:
        video_id = flash.get("video_id") or _extract_video_id(
            _meta(BeautifulSoup(html or "", "lxml"), prop="og:url") or ""
        )

    for url_key, label_key in _STREAM_FIELD_PAIRS:
        raw = flash.get(url_key)
        if not raw:
            continue
        media = raw.replace("\\/", "/")
        if not media or media in seen:
            continue
        seen.add(media)
        streams.append(
            {
                "quality": _normalize_quality_label(flash.get(label_key), media),
                "url": media,
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )

    # Fallback: any get_file mp4 URL on the page
    if not streams:
        for raw in _GET_FILE_RE.findall((html or "").replace("\\/", "/")):
            media = raw.rstrip("/\\")
            if "_preview" in media.lower() or media in seen:
                continue
            seen.add(media)
            streams.append(
                {
                    "quality": _normalize_quality_label(None, media),
                    "url": media,
                    "format": "hls" if ".m3u8" in media.lower() else "mp4",
                }
            )

    # Fallback: <video>/<source> tags
    if not streams:
        soup = BeautifulSoup(html, "lxml")
        for source in soup.select("video source[src], video[src]"):
            src = str(source.get("src") or "")
            if not src or src in seen:
                continue
            seen.add(src)
            streams.append(
                {
                    "quality": _normalize_quality_label(source.get("label"), src),
                    "url": src,
                    "format": "hls" if ".m3u8" in src.lower() else "mp4",
                }
            )

    # HLS master playlist if present
    hls_url: Optional[str] = None
    hls_match = _M3U8_RE.search(html or "")
    if hls_match:
        hls_url = hls_match.group(0)
        if hls_url not in seen:
            streams.insert(0, {"quality": "adaptive", "url": hls_url, "format": "hls"})

    # Best quality first; embed fallback last; default = best MP4, else HLS
    if video_id and str(video_id).isdigit():
        embed = f"{BASE_SITE}embed/{video_id}/"
        if embed not in seen:
            streams.append({"quality": "embed", "url": embed, "format": "embed"})
    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
    mp4 = next((s["url"] for s in streams if s.get("format") == "mp4"), None)
    default_url = mp4 or (streams[0]["url"] if streams else None)

    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(streams),
    }


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    meta_desc = _meta(soup, name="description")

    title = _first_non_empty(og_title, _text(soup.find("title")))

    # Clean title suffixes
    if title:
        for suffix in (" - FPO.XXX", " - FPO", " FPO.XXX"):
            if title.upper().endswith(suffix.upper()):
                title = title[:-len(suffix)]

    description = _first_non_empty(og_desc, meta_desc)
    thumbnail = _first_non_empty(og_image, _itemprop(soup, "thumbnailUrl"))
    upload_date = _itemprop(soup, "uploadDate")

    json_ld = _parse_json_ld(soup)
    video_obj: Optional[dict[str, Any]] = None
    for obj in json_ld:
        t = obj.get("@type")
        if isinstance(t, list):
            if any(str(x).lower() == "videoobject" for x in t):
                video_obj = obj
                break
        if isinstance(t, str) and t.lower() == "videoobject":
            video_obj = obj
            break

    duration = None
    uploader = None
    category = None
    tags: list[str] = []
    views: Optional[str] = None

    if video_obj:
        title = _first_non_empty(title, video_obj.get("name"))
        description = _first_non_empty(description, video_obj.get("description"))

        thumb = video_obj.get("thumbnailUrl") or video_obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)

        duration = _normalize_duration(video_obj.get("duration"))

        author = video_obj.get("author")
        if isinstance(author, dict):
            uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
        elif isinstance(author, str):
            uploader = author.strip() or None

        # Extract views from interactionStatistic
        interaction = video_obj.get("interactionStatistic")
        if interaction:
            interactions = interaction if isinstance(interaction, list) else [interaction]
            for i in interactions:
                if not isinstance(i, dict):
                    continue
                itype = i.get("interactionType")
                is_watch = False
                if isinstance(itype, str):
                    is_watch = "WatchAction" in itype
                elif isinstance(itype, dict):
                    t = itype.get("@type")
                    if t and "WatchAction" in str(t):
                         is_watch = True
                
                if is_watch:
                    count = i.get("userInteractionCount")
                    if count:
                        views = _clean_count(str(count)) or str(count)
                        break

        genre = video_obj.get("genre")
        if isinstance(genre, str):
            category = genre.strip() or None
        elif isinstance(genre, list) and genre:
            category = str(genre[0]).strip() or None

        tags = _as_list(video_obj.get("keywords"))

    flash = _parse_flashvars(html)

    # Duration: meta video:duration (secs) -> meta itemprop duration (ISO) -> player region.
    # Never fall back to generic .duration spans — those belong to related video cards.
    if not duration:
        duration = _normalize_duration(_meta(soup, prop="video:duration"))
    if not duration:
        duration = _normalize_duration(_itemprop(soup, "duration"))
    if not duration:
        holder = soup.select_one(".video-holder, .block-video, #tab_video_info")
        if holder:
            duration = _find_duration_like_text(holder.get_text(" ", strip=True))

    # Views: UserViews meta -> interactionCount -> .video-info "N Views" text
    if not views:
        m = re.search(r"UserViews:\s*([\d\s,.]+)", html or "")
        if m:
            views = _clean_count(m.group(0).split(":", 1)[1])
    if not views:
        interaction = _itemprop(soup, "interactionCount")
        if interaction:
            m = re.search(r"([\d\s,.]+)", interaction)
            if m:
                views = _clean_count(m.group(1))
    if not views:
        info = soup.select_one(".video-info .info")
        if info:
            m = re.search(
                r"([\d\s,.]+)\s*Views", info.get_text(" ", strip=True), re.IGNORECASE
            )
            if m:
                views = _clean_count(m.group(1))

    # Uploader: .block-user .username (inside video info, not comment authors)
    if not uploader:
        user_link = soup.select_one(".video-info .block-user .username a, .block-user .username a")
        if user_link:
            uploader = _text(user_link)

    # Tags / categories: flashvars first, then links inside the video info block
    if not tags:
        tags = _as_list(flash.get("video_tags"))
    if not tags:
        tags = _as_list(flash.get("video_models"))
    if not category:
        cat_list = _as_list(flash.get("video_categories"))
        if cat_list:
            category = cat_list[0]

    info_block = soup.select_one(".video-info, #tab_video_info") or soup
    if not tags:
        for a in info_block.select('a[href*="/tags/"], a.tag, a[href*="/search/?q="]'):
            t = _text(a)
            if t:
                tags.append(t)
    tags = list(dict.fromkeys([t for t in tags if t]))

    if not category:
        for a in info_block.select('a[href*="/categories/"]'):
            t = _text(a)
            if t:
                category = t
                break

    # Related Videos Extraction
    related_videos = []
    rel_container = soup.select_one(".related-videos") or soup.find(
        class_=re.compile(r"related", re.IGNORECASE)
    )
    if rel_container:
        for item in rel_container.select(".item"):
            try:
                link = item.find("a")
                if not link:
                    continue

                href = link.get("href")
                if not href:
                    continue

                r_img = link.find("img")
                r_title = _first_non_empty(
                    link.get("title"),
                    _text(item.find("strong")),
                    r_img.get("alt") if r_img else None,
                )
                r_thumb = _best_image_url(r_img)

                r_dur = None
                dur_el = item.find(class_=re.compile(r"duration", re.IGNORECASE))
                if dur_el:
                    r_dur = _find_duration_like_text(_text(dur_el) or "")

                r_url = urljoin(url, href.strip())
                if "/video" not in r_url or r_url.rstrip("/") == url.rstrip("/"):
                    continue

                related_videos.append({
                    "url": r_url,
                    "title": r_title,
                    "thumbnail_url": r_thumb,
                    "duration": r_dur
                })

                if len(related_videos) >= 10:
                    break
            except Exception:
                continue

    # Extract video URLs for streaming
    video_id = flash.get("video_id") or _extract_video_id(url)
    video_info = _extract_video_urls(html, video_id)

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
        "related_videos": related_videos,
        "video": video_info,
    }


async def scrape(url: str) -> dict[str, Any]:
    # Direct /get_file/ media link: resolve the canonical video page for fresh
    # stream tokens + full metadata (get_file access tokens are session/expiry
    # bound). Fall back to serving the link itself as the stream.
    if _is_get_file_url(url):
        video_id = _extract_video_id(url)
        canon = await _canonical_from_embed(video_id) if video_id else None
        if canon:
            try:
                return await scrape(canon)
            except Exception:
                pass
        return _direct_stream_result(url)

    # /embed/{id} page: redirect to the canonical video page for full metadata
    # (title, views, uploader, HQ streams). Fall back to parsing the embed
    # player page itself if the canonical page is unreachable.
    fetch_url = url
    requested_embed = _is_embed_url(url)
    if requested_embed:
        video_id = _extract_video_id(url)
        canon = await _canonical_from_embed(video_id) if video_id else None
        if canon:
            fetch_url = canon

    try:
        html = await fetch_html(fetch_url)
    except Exception:
        if requested_embed and fetch_url != url:
            html = await fetch_html(url)
            return _parse_embed_page(html, url)
        raise

    return parse_page(html, fetch_url)


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    candidates: list[str] = []
    if page <= 1:
        candidates.append(root)
    else:
        # FPO.XXX uses /{page}/ pagination
        candidates.extend([
            f"{root}{page}/",
        ])

    html = ""
    used = ""
    last_exc: Exception | None = None
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html and ('class="item' in html or "/video/" in html):
                break
        except Exception as e:
            last_exc = e
            continue

    if not html:
        if last_exc:
            raise last_exc
        return []

    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # FPO.XXX uses .item class for video cards
    for block in soup.select(".item"):
        link = block.find("a")
        if not link:
            continue

        href = link.get("href")
        if not href or "/video" not in href:
            continue

        abs_url = urljoin(used or root, href.strip())

        if abs_url in seen:
            continue

        img = link.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        # Title: from strong.title or link/img attributes
        title = None
        strong = block.find("strong")
        if strong:
            title = _text(strong)
        if not title:
            title = _first_non_empty(link.get("title"), img.get("alt"))

        # Duration
        duration = None
        dur_el = block.find(class_=re.compile(r"duration", re.IGNORECASE))
        if dur_el:
            duration = _find_duration_like_text(_text(dur_el) or "")

        # Views ("242 515" / "242,515" / "1.2M")
        views = None
        views_el = block.find(class_=re.compile(r"views?", re.IGNORECASE))
        if views_el:
            views = _clean_count(_text(views_el) or "")

        seen.add(abs_url)
        items.append(
            {
                "url": abs_url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )
        if limit and len(items) >= limit:
            break

    return items