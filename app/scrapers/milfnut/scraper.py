from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://milfnut.com/"
SITE_HOST = "milfnut.com"
SITE_ALIASES = frozenset({"milfnut.com", "www.milfnut.com"})
STREAM_CDN_HOSTS = frozenset({"tulipvid.net"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_NON_VIDEO_SLUGS = frozenset(
    {
        "about",
        "actor",
        "actors",
        "category",
        "categories",
        "comments",
        "contact",
        "dmca",
        "feed",
        "page",
        "pornstars",
        "privacy-policy",
        "tag",
        "tags",
        "terms-of-service",
        "wp-admin",
        "wp-content",
        "wp-json",
        "wp-login.php",
        "xmlrpc.php",
    }
)

_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_ISO_DURATION_RE = re.compile(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_M3U8_URL_RE = re.compile(r"""https?://[^\s"'<>\\]+\.m3u8(?:\?[^\s"'<>\\]*)?""", re.IGNORECASE)
_MP4_URL_RE = re.compile(r"""https?://[^\s"'<>\\]+\.mp4(?:\?[^\s"'<>\\]*)?""", re.IGNORECASE)
_LIST_ARTICLE_SELECTOR = "article.loop-video, article.thumb-block"


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".milfnut.com"):
        return True
    return h in STREAM_CDN_HOSTS or any(h.endswith("." + cdn) for cdn in STREAM_CDN_HOSTS)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    out: list[dict] = []
    seen_urls: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url:
            continue
        if not url.startswith("http"):
            url = urljoin(BASE_SITE, url.lstrip("/"))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({"name": name, "url": url, "video_count": 0})
    return out


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
    for suffix in (
        " | Milfnut",
        " - Milfnut",
        " – Milfnut",
        " | milfnut.com",
        " - milfnut.com",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    raw = str(url).strip().replace("\\/", "/")
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return urljoin(BASE_SITE, raw)
    if raw.startswith("http://"):
        return "https://" + raw[7:]
    return raw


def _itemprop_meta(soup: BeautifulSoup, *, root: Any, name: str) -> Optional[str]:
    """Read an itemprop meta from the VideoObject article (root) or anywhere on the page."""
    scope = root if root is not None else soup
    for node in scope.find_all(attrs={"itemprop": name}):
        content = node.get("content") if hasattr(node, "get") else None
        if content:
            return str(content).strip()
    return None


def _format_iso_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _ISO_DURATION_RE.search(str(text))
    if not m:
        local = _DURATION_RE.search(str(text))
        return local.group(0) if local else None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    mi, se = divmod(rem, 60)
    if h:
        return f"{h}:{mi:02d}:{se:02d}"
    return f"{mi}:{se:02d}"


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-main-thumb", "data-src", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        return _normalize_media_url(url)
    return None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    match = _DURATION_RE.search(str(text))
    return match.group(0) if match else None


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/{slug.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href.split("#", 1)[0].split("?", 1)[0])
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host and host != SITE_HOST:
        return None
    path = (parsed.path or "").strip("/")
    if not path or "/" in path:
        return None
    slug = path.lower()
    if slug in _NON_VIDEO_SLUGS or slug.startswith("wp-") or slug.endswith(".php"):
        return None
    return _canonical_video_url(path)


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").strip("/")
    if host != SITE_HOST or not path or "/" in path or path.lower() in _NON_VIDEO_SLUGS:
        raise ValueError(f"Unsupported Milfnut URL: {url}")
    return raw


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


def _streams_from_player_payload(payload_html: str) -> list[dict[str, str]]:
    """Extract media URLs from the decoded clean-tube-player payload."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern, fmt in ((_M3U8_URL_RE, "hls"), (_MP4_URL_RE, "mp4")):
        for match in pattern.finditer(payload_html or ""):
            src = _normalize_media_url(match.group(0))
            if not src or src in seen:
                continue
            seen.add(src)
            found.append({"url": src, "quality": "source" if fmt == "hls" else fmt, "format": fmt})
    return found


def _extract_player_embed_urls(soup: BeautifulSoup) -> list[str]:
    """Collect clean-tube-player player-x.php embed URLs (decode the q payload)."""
    embeds: list[str] = []
    seen: set[str] = set()

    def _add(url: Optional[str]) -> None:
        media = _normalize_media_url(url)
        if not media or media in seen:
            return
        seen.add(media)
        embeds.append(media)

    for iframe in soup.select("iframe[src]"):
        src = str(iframe.get("src") or "").strip()
        if "player-x.php" in src.lower():
            _add(src)
    return embeds


def _decode_player_payloads(page_html: str) -> list[str]:
    """Decode every player-x.php?q=<base64> payload into plain markup."""
    payloads: list[str] = []
    for match in re.finditer(
        r'player-x\.php\?q=([A-Za-z0-9+/=_-]+)',
        page_html or "",
        re.IGNORECASE,
    ):
        bucket = match.group(1)
        for candidate in (bucket, bucket.replace("-", "+").replace("_", "/")):
            try:
                padded = candidate + "=" * (-len(candidate) % 4)
                decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
            except Exception:
                continue
            if "http" in decoded:
                from urllib.parse import unquote

                payloads.append(unquote(decoded))
                break
    return payloads


def _streams_from_html(page_html: str, soup: Optional[BeautifulSoup] = None) -> dict[str, Any]:
    """Streams: direct HLS from itemprop/decoded player payload + player embed fallback."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    def _add(src: Optional[str], quality: str, fmt: str) -> None:
        media = _normalize_media_url(src)
        if not media or media in seen:
            return
        seen.add(media)
        streams.append({"url": media, "quality": quality, "format": fmt})
        if fmt == "hls" and hls_url is None:
            hls_url = media

    # 1) itemprop sources: contentUrl is a direct .m3u8
    for match in _M3U8_URL_RE.finditer(page_html or ""):
        _add(match.group(0), "source", "hls")

    # 2) clean-tube-player payloads (source tags in the decoded markup)
    for payload in _decode_player_payloads(page_html):
        for entry in _streams_from_player_payload(payload):
            if entry["url"] not in seen:
                _add(entry["url"], entry["quality"], entry["format"])

    # 3) generic regex fallbacks for other CDN files (skip preview assets)
    for match in _MP4_URL_RE.finditer(page_html or ""):
        src = match.group(0)
        if "preview" in src.lower() or "/videos_screenshots/" in src.lower():
            continue
        _add(src, "source", "mp4")

    # 4) player-x.php embed fallback so WebView playback keeps working
    if soup is None:
        soup = BeautifulSoup(page_html or "", "lxml")
    for embed in _extract_player_embed_urls(soup):
        if embed not in seen:
            seen.add(embed)
            streams.append({"url": embed, "quality": "Player", "format": "embed"})

    default = hls_url or next(
        (s["url"] for s in streams if s.get("format") == "mp4"),
        streams[0]["url"] if streams else None,
    )
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_articles(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select(_LIST_ARTICLE_SELECTOR):
        if len(items) >= limit:
            break
        link = block.select_one("a[href]")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                link.get("title"),
                img.get("alt") if img else None,
                link.get_text(" ", strip=True),
            )
        ) or "Unknown Video"
        title = _DURATION_RE.sub("", title).strip() or "Unknown Video"

        duration_el = block.select_one(".duration")
        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)
        if not duration:
            duration = _extract_duration(link.get_text(" ", strip=True))

        thumb = _normalize_media_url(block.get("data-main-thumb")) or _best_image_url(img)

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": None,
                "uploader_name": None,
                "preview_url": thumb,
            }
        )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    path = (parsed.path or "").strip("/")
    if not path:
        if page_num <= 1:
            return raw
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, f"/page/{page_num}/", "", urlencode(query), "")
        )

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)

    base_path = "/" + path.strip("/") + "/"
    new_path = base_path if page_num <= 1 else f"{base_path}page/{page_num}/"
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            urlencode(query),
            "",
        )
    )


def parse_video_page(page_html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    article = soup.find("article", attrs={"itemtype": "https://schema.org/VideoObject"})

    title = _clean_title(
        _first_non_empty(
            _itemprop_meta(soup, root=article, name="name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1.entry-title").get_text(" ", strip=True)
            if soup.select_one("h1.entry-title")
            else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _itemprop_meta(soup, root=article, name="thumbnailUrl"),
        _meta(soup, prop="og:image"),
    )
    thumbnail = _normalize_media_url(thumbnail)

    description = _first_non_empty(
        _itemprop_meta(soup, root=article, name="description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    duration = _format_iso_duration(
        _itemprop_meta(soup, root=article, name="duration")
    )
    if not duration:
        duration_el = soup.select_one(".video-player .duration")
        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)

    upload_date = _first_non_empty(
        _itemprop_meta(soup, root=article, name="uploadDate"),
        _meta(soup, prop="article:published_time"),
        _meta(soup, prop="article:modified_time"),
    )

    tags: list[str] = []
    for a in soup.select(".tags a, .tags-list a, .post-tags a, a[rel='tag']"):
        txt = a.get_text(" ", strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    actors: list[str] = []
    for a in soup.select("#video-actors a, a[href*='/actor/']"):
        txt = a.get_text(" ", strip=True)
        if txt and txt not in actors:
            actors.append(txt)

    uploader = _first_non_empty(
        _itemprop_meta(soup, root=article, name="author.name"),
        "Milfnut",
    )

    related = _parse_list_articles(soup, limit=40)
    related = [r for r in related if r.get("url") != url]

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "category": tags[0] if tags else None,
        "tags": tags + actors,
        "upload_date": upload_date,
        "video": _streams_from_html(page_html, soup=soup),
        "related_videos": related,
        "preview_url": thumbnail,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    page_url = _resolve_video_url(canon)
    page_html = await fetch_page(page_url, referer=BASE_SITE)
    return parse_video_page(page_html, page_url)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or BASE_SITE
    page_url = _build_list_page_url(normalized_base, page)
    try:
        page_html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(page_html, "lxml")
    return _parse_list_articles(soup, limit=limit)
