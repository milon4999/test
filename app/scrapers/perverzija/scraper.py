from __future__ import annotations

import html as _html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://tube.perverzija.com/"
SITE_HOST = "tube.perverzija.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"tube\.perverzija\.com/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_PLAYER_EMBED_RE = re.compile(
    r"https?://[^\"'\\\s<>]+/player/index\.php\?data=[0-9a-fA-F]+",
    re.IGNORECASE,
)
_DURATION_TEXT_RE = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")

# Root-level path segments that are never video posts on this WordPress site.
_RESERVED_SEGMENTS = frozenset(
    {
        "page",
        "tag",
        "tags",
        "studio",
        "vr",
        "featured-scenes",
        "full-movie",
        "category",
        "categories",
        "author",
        "search",
        "comments",
        "feed",
        "wp-content",
        "wp-admin",
        "wp-includes",
        "wp-login",
        "dmca",
        "terms",
        "privacy",
        "contact",
        "2257",
        "18-usc-2257",
        "advertise",
        "members",
        "login",
        "register",
        "video",
        "watch",
    }
)


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


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _unescape_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = _html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


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
    t = _unescape_text(title)
    if not t:
        return None
    t = re.sub(r"^\s*Watch\s+", "", t, flags=re.IGNORECASE)
    for suffix in (
        " | Perverzija.com",
        " - Perverzija.com",
        " | tube.perverzija.com",
        " - tube.perverzija.com",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _iso_duration_to_hms(value: Any) -> Optional[str]:
    raw = (str(value) if value is not None else "").strip().upper()
    if not raw.startswith("PT"):
        return None
    hours = minutes = seconds = 0
    matched = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)([HMS])", raw):
        matched = True
        n = int(float(num))
        if unit == "H":
            hours = n
        elif unit == "M":
            minutes = n
        elif unit == "S":
            seconds = n
    if not matched:
        return None
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


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


def _is_video_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.split(":")[0].removeprefix("www.") != SITE_HOST:
        return False
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    if slug in _RESERVED_SEGMENTS or slug.startswith("wp-"):
        return False
    if "." in slug and slug.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico", ".html", ".php", ".xml")):
        return False
    return True


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if not _is_video_url(href):
        return None
    return href


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").strip("/").split("/") if p]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)

    parts = _path_parts(parsed.path)
    if len(parts) >= 2 and parts[-2] == "page" and parts[-1].isdigit():
        parts = parts[:-2]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    else:
        new_path = "/" + "/".join(parts + ["page", str(page_num)]) + "/"

    query = "&".join(
        f"{k}={v}" for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", query, ""))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-lazy-src", "data-original", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if url.endswith((".ico", ".svg")):
            continue
        return _normalize_media_url(url)
    # srcset fallback: first candidate
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        first = str(srcset).split(",")[0].strip().split(" ")[0]
        if first and first.startswith("http"):
            return _normalize_media_url(first)
    return None


def _parse_duration_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DURATION_TEXT_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return f"{int(m.group(1))}:{int(m.group(2)):02d}:{int(m.group(3)):02d}"
    return f"{int(m.group(2)):02d}:{int(m.group(3)):02d}"


def _parse_list_card(card: Any) -> Optional[dict[str, Any]]:
    anchor = card.select_one(".item-thumbnail a[href]")
    if anchor is None:
        anchor = card.select_one("a[href]")
    if anchor is None:
        return None

    canon = _normalize_video_href(anchor.get("href") or "")
    if not canon:
        return None

    img = anchor.find("img") or card.find("img")
    thumb = _best_image_url(img)

    title = _clean_title(
        _first_non_empty(
            (card.select_one(".item-head h2 a") or anchor).get("title"),
            anchor.get("title"),
            img.get("alt") if img else None,
            (card.select_one(".item-head h2 a") or anchor).get_text(" ", strip=True),
            anchor.get_text(" ", strip=True),
        )
    ) or "Unknown Video"

    duration = _parse_duration_text(card.get_text(" ", strip=True))
    if duration is None:
        dur_span = card.select_one(".rating-bar, .time_dur")
        if dur_span is not None:
            duration = _parse_duration_text(dur_span.get_text(" ", strip=True))

    uploader = None
    studio_link = card.select_one("a[href*='/studio/']")
    if studio_link is not None:
        uploader = _unescape_text(studio_link.get_text(" ", strip=True))

    return {
        "url": canon,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "preview_url": thumb,
    }


def _parse_cards(soup: BeautifulSoup, exclude_url: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.video-item"):
        parsed = _parse_list_card(card)
        if not parsed or parsed["url"] in seen:
            continue
        if exclude_url and parsed["url"].rstrip("/") == exclude_url.rstrip("/"):
            continue
        seen.add(parsed["url"])
        items.append(parsed)
    return items


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
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


def _parse_json_ld_video_object(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or "VideoObject" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates: list[Any] = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            candidates = [data]
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates.extend(graph)
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") == "VideoObject":
                return node
    return {}


def _extract_embed_urls(html: str, soup: BeautifulSoup) -> list[str]:
    embeds: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        media = _normalize_media_url(url)
        if not media or media in seen:
            return
        if not _PLAYER_EMBED_RE.fullmatch(media):
            # Accept other player iframes only when they look like video players
            low = media.lower()
            if not any(x in low for x in ("player", "embed", "iframe")):
                return
        seen.add(media)
        embeds.append(media)

    container = soup.select_one("#player-embed iframe[src]")
    if container is not None:
        _add(str(container.get("src") or ""))

    for m in _PLAYER_EMBED_RE.finditer(html or ""):
        _add(m.group(0))
        if embeds:
            break

    # data-embed attribute holds HTML-entity-encoded iframe markup
    for el in soup.select("[data-embed]"):
        raw = str(el.get("data-embed") or "")
        if raw:
            for m in re.finditer(r'src=["\']([^"\']+)["\']', _html.unescape(raw)):
                _add(m.group(1))
                break

    return embeds


def _streams_from_html(html: str, soup: BeautifulSoup) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    for embed in _extract_embed_urls(html, soup):
        streams.append({"url": embed, "quality": "embed", "format": "embed"})

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_json_ld_video_object(soup)

    title = _clean_title(
        _first_non_empty(
            ld.get("headline"),
            ld.get("name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        ld.get("thumbnailUrl") if isinstance(ld.get("thumbnailUrl"), str) else None,
        _meta(soup, prop="og:image"),
    )
    if not thumbnail and isinstance(ld.get("thumbnailUrl"), list) and ld.get("thumbnailUrl"):
        thumbnail = str(ld["thumbnailUrl"][0])
    if not thumbnail and isinstance(ld.get("image"), dict):
        thumbnail = ld["image"].get("url")
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    description = _unescape_text(
        _first_non_empty(ld.get("description"), _meta(soup, prop="og:description"))
    )

    duration = _iso_duration_to_hms(ld.get("duration"))
    if duration is None:
        duration = _parse_duration_text(soup.get_text(" ", strip=True))

    tags: list[str] = []
    for link in soup.select("a[href*='/tag/']"):
        txt = _unescape_text(link.get_text(" ", strip=True))
        if txt and txt.lower() not in {t.lower() for t in tags}:
            tags.append(txt)

    uploader = _unescape_text((ld.get("author") or {}).get("name")) if isinstance(ld.get("author"), dict) else None
    if uploader is None:
        author_link = soup.select_one("a[href*='/author/']")
        if author_link is not None:
            uploader = _unescape_text(author_link.get_text(" ", strip=True))

    category = None
    studio_link = soup.select_one("a[href*='/studio/']")
    if studio_link is not None:
        category = _unescape_text(studio_link.get_text(" ", strip=True))

    views: Optional[str] = None
    views_span = soup.select_one(".post-views-count")
    if views_span is not None:
        raw = re.sub(r"[^\d]", "", views_span.get_text(" ", strip=True))
        if raw:
            views = raw

    upload_date = ld.get("uploadDate") or ld.get("datePublished")

    related = _parse_cards(soup, exclude_url=url)

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
        "video": _streams_from_html(html, soup),
        "related_videos": related,
        "preview_url": thumbnail,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=BASE_SITE)
    return parse_video_page(html, canon)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url or BASE_SITE, page)
    try:
        html = await fetch_page(page_url, referer=BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items = _parse_cards(soup)
    return items[:limit]
