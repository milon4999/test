from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://film-adult.video/en/"
SITE_HOST = "film-adult.video"
SITE_ALIASES = frozenset({"film-adult.video", "www.film-adult.video"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?film-adult\.video/(?:en/)?(?P<vid>\d+)-[^/?#]+\.html/?$",
    re.IGNORECASE,
)
_EMBED_SRC_RE = re.compile(r"\.src\s*=\s*\"(https?://[^\"]+)\"", re.IGNORECASE)
# The real markup uses $(document).one('click', '#video2_container', ...), NOT
# $("#video2_container").one(...) — so the selector order differs from siska.
_CONTAINER_RE = re.compile(r"[\"']#(\w+_container)[\"']")
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)
_PLAYER_LABELS = {
    "video2_container": "Server 1",
    "video3_container": "Server 2",
    "video_container": "Server 3",
    "trailer_container": "Trailer",
}
_PLAYER_ORDER = ["video2_container", "video3_container", "video_container", "trailer_container"]
# hgcloud.to serves a JS-bootstrap that redirects to rotating mirrors; the same
# /e/{id} works on every mirror. Verified live (2026-09): hanerix.com, vibuxer.com,
# audinifer.com all serve the real player.
_HGCLOUD_MIRRORS = ["hanerix.com", "vibuxer.com", "audinifer.com"]
_PLAYER_HOST_LABELS = {
    "hgcloud": "HgCloud",
    "playmogo": "Playmogo",
    "morencius": "Morencius",
    "voe.sx": "VOE",
    "luluvid": "LuluStream",
}


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".film-adult.video")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        for imp in ("chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.text:
                        return resp.text
            except Exception:
                continue
    try:
        return await pool_fetch_html(url, headers=headers)
    except Exception:
        return ""


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    return t or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://{SITE_HOST}{href}"
    m = _VIDEO_PAGE_RE.match(href.split("#", 1)[0])
    if not m:
        return None
    vid = m.group("vid")
    if not vid.isdigit():
        return None
    return f"https://{SITE_HOST}/en/{vid}.html" if False else f"https://{SITE_HOST}/en/{vid}.html"


def _canonical_page_url(raw_url: str) -> Optional[str]:
    """Accepts /en/{vid}-{slug}.html and /{vid}-{slug}.html forms,
    preserving the full `vid-slug.html` tail."""
    m = re.search(r"/(?:en/)?((?P<vid>\d+)-[^/?#]+\.html)", (raw_url or ""), re.IGNORECASE)
    if not m:
        return None
    tail = m.group(1)
    if not m.group("vid").isdigit():
        return None
    return f"https://{SITE_HOST}/en/{tail}"


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://{SITE_HOST}{url}"
        return url
    return None


def _streams_from_html(html: str) -> dict[str, Any]:
    """Player embeds are injected on click from inline scripts:
    `$(document).one('click', '#video2_container', ...){ s2.src = \"https://host/e/id\"; }`.
    video2_container = main player, video3 = second, trailer = trailer.

    NOTE: `hgcloud.to` serves a 819-byte \"Loading...\" bootstrap page (its
    main.js redirects to a rotating mirror — hanerix.com / vibuxer.com /
    audinifer.com, changing per refresh). Since the mirror can't be resolved
    reliably server-side, hgcloud embeds are REPLACED by all known mirrors
    (each mirror serves the real player for the same embed id)."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for m in _CONTAINER_RE.finditer(html):
        container = m.group(1)
        # search the surrounding script for its src assignment
        window = html[m.start(): html.find("</script>", m.start()) + 9]
        sm = _EMBED_SRC_RE.search(window)
        if not sm:
            continue
        url = sm.group(1).strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        low = url.lower()
        label = _PLAYER_HOST_LABELS.get(container, None)
        if not label:
            label = next((v for h, v in _PLAYER_HOST_LABELS.items() if h in low), None)
        if not label:
            label = _PLAYER_LABELS.get(container, f"Server {len(streams) + 1}")
        streams.append(
            {
                "url": url,
                "quality": label,
                "format": "embed",
                "_order": _PLAYER_ORDER.index(container)
                if container in _PLAYER_ORDER
                else len(_PLAYER_ORDER),
            }
        )

    # Priority order: main player (video2) -> second (video3) -> trailer last
    streams.sort(key=lambda s: s["_order"])
    for s in streams:
        s.pop("_order", None)

    # Replace hgcloud bootstrap URLs with the rotating mirror pool (same
    # /e/{id} works on every mirror; each refresh may pick a different one)
    expanded: list[dict[str, str]] = []
    for s in streams:
        m = re.match(r"https://hgcloud\.to/e/([a-z0-9]+)/?$", s["url"], re.IGNORECASE)
        if m:
            eid = m.group(1)
            for mirror in _HGCLOUD_MIRRORS:
                expanded.append({"url": f"https://{mirror}/e/{eid}", "quality": f"{s['quality']} ({mirror.split('.')[0].capitalize()})", "format": "embed"})
        else:
            expanded.append(s)
    streams = expanded

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_poster_cards(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.poster[href]"):
        if len(items) >= limit:
            break
        url = _canonical_page_url(a.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = a.select_one("h3.poster__title")
        title = _clean_title(title_el.get_text(" ", strip=True)) if title_el else None
        img = a.select_one("img")
        thumb = _best_image_url(img)

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items = _parse_poster_cards(soup, limit=limit)
    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw)
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/en/").rstrip("/") or "/en"

    # DLE pagination: /en/page/2/, /en/movies/hd-720p/page/2/ (trailing slash)
    if page_num > 1:
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/en"
        path = f"{path}/page/{page_num}/"
    elif re.search(r"/page/\d+$", path, re.I):
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/en"

    qs = {k: v for k, v in (p.split("=", 1) for p in parsed.query.split("&") if "=" in p)}
    query = urlencode(qs) if qs else ""
    return urlunparse((parsed.scheme or "https", parsed.netloc or "film-adult.video", path, "", query, ""))


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _canonical_page_url(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"), _meta(soup, name="description")
    )
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one(".poster__img img, img")),
    )

    # JSON-LD Movie graph: name/description/datePublished + Movie + actor
    duration: Optional[str] = None
    upload_date: Optional[str] = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for g in data.get("@graph", [data]) if isinstance(data, dict) else []:
            if g.get("datePublished") and not upload_date:
                upload_date = g.get("datePublished")

    # HTML5 duration meta: "01:28:24" appears in the description text; skip.

    tags: list[str] = []
    for a in soup.select('a[href*="/tags/"], a[href*="/movies/"], a[class="cat"]'):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and 1 < len(t) < 60:
            tags.append(t)

    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or _streams_from_html(html)

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


def _is_missing_media_page(html: str) -> bool:
    low = (html or "").lower()
    return "404" in low and "not found" in low


async def scrape(url: str) -> dict[str, Any]:
    canon = _canonical_page_url(url)
    if not canon:
        raise ValueError(f"Unsupported film-adult URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    video_data = _streams_from_html(html)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or BASE_SITE
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, html, limit=limit)
