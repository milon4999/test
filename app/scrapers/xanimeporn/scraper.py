from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html
from app.core.pool import pool as pool_client


SITE_HOST = "xanimeporn.com"
SITE_URL = f"https://{SITE_HOST}"
TITLE_SUFFIX = " | X Anime Porn"

# The player source is injected client-side: the theme POSTs to admin-ajax.php
# (`action=get_video_url`) and receives the signed `watch.xanimeporn.com/...mp4` URL.
ADMIN_AJAX_URL = f"{SITE_URL}/wp-admin/admin-ajax.php"
# Main archive listing. Footer/top widgets reuse `ul.listing-tube` (Most Viewed,
# Top Rated, Random), so always scope parsing to the `#content` listing.
MAIN_LISTING_SELECTOR = "#content ul.listing-tube"


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_page(url: str) -> str:
    headers = {**_BASE_HEADERS, "Referer": f"{SITE_URL}/"}
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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    if t.endswith(TITLE_SUFFIX):
        t = t[: -len(TITLE_SUFFIX)].strip()
    return t or None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-lazy-src", "data-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("data:"):
            continue
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _quality_from_query(url: str) -> str:
    try:
        q = dict(parse_qsl(urlparse(url).query)).get("quality")
    except Exception:
        q = None
    if q:
        q = re.sub(r"[^0-9pP]", "", str(q)).lower()
        if re.fullmatch(r"\d{3,4}p", q):
            return q
    return "source"


def _quality_score(q: str) -> int:
    m = re.search(r"(\d{3,4})", q or "")
    return int(m.group(1)) if m else 0


def _extract_post_id(html: str) -> Optional[str]:
    """WordPress post id used by the theme's player AJAX call (`ajax_object.post_id`)."""
    for pattern in (
        r'"post_id"\s*:\s*"?(\d+)"?',
        r'"postId"\s*:\s*"?(\d+)"?',
        r'data-post_id="(\d+)"',
    ):
        m = re.search(pattern, html or "")
        if m:
            return m.group(1)
    return None


def _is_player_stream_url(url: str) -> bool:
    if not url.startswith("http"):
        return False
    parsed = urlparse(url)
    if SITE_HOST not in parsed.netloc.lower():
        return False
    return parsed.path.lower().endswith((".mp4", ".m3u8", ".webm"))


async def resolve_player_stream(page_url: str, html: str) -> Optional[str]:
    """Resolve the direct MP4 the site player uses (signed `watch.xanimeporn.com` URL).

    The detail page ships an empty `<video><source src=''>`; the theme fills it with a
    POST to `admin-ajax.php` (`action=get_video_url`, `idpost=<post_id>`) that returns the
    signed stream URL. Failures are non fatal - callers fall back to the Downloads tab.
    """
    post_id = _extract_post_id(html)
    if not post_id:
        return None

    headers = {
        **_BASE_HEADERS,
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        session = await pool_client.get_session()
        async with session.post(
            ADMIN_AJAX_URL,
            data={"action": "get_video_url", "idpost": post_id},
            headers=headers,
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                return None
            text = (await resp.text()).strip()
    except Exception:
        return None

    if not text or "\n" in text or len(text) > 500:
        return None
    return text if _is_player_stream_url(text) else None


def _extract_download_streams(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Download links expose the real playable MP4 on videos.xanimeporn.com."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.select('div.su-tabs-pane[data-title="Downloads"] a'):
        href = (a.get("href") or "").strip()
        if not href or not href.startswith("http"):
            continue
        if "download.php" not in href:
            continue
        quality = _quality_from_query(href)
        if href in seen:
            continue
        seen.add(href)
        streams.append({"url": href, "quality": quality, "format": "mp4"})

    if not streams:
        for a in soup.select("a[href*='download.php']"):
            href = (a.get("href") or "").strip()
            if not href.startswith("http") or "download.php" not in href:
                continue
            quality = _quality_from_query(href)
            if href in seen:
                continue
            seen.add(href)
            streams.append({"url": href, "quality": quality, "format": "mp4"})

    streams.sort(key=lambda s: _quality_score(s.get("quality", "")), reverse=True)
    return streams


def _extract_streams(
    soup: BeautifulSoup,
    player_stream: Optional[str] = None,
) -> dict[str, Any]:
    streams = _extract_download_streams(soup)

    seen = {s["url"] for s in streams}
    for video in soup.select("video[src], video source[src]"):
        src = (video.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(f"{SITE_URL}/", src)
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        streams.append(
            {
                "url": src,
                "quality": "source",
                "format": "hls" if ".m3u8" in src.lower() else "mp4",
            }
        )

    # Signed player URL (same file the site plays). Appended last so the
    # permanent quality ladder above keeps priority for `video.default`.
    if player_stream and player_stream not in seen:
        seen.add(player_stream)
        streams.append(
            {
                "url": player_stream,
                "quality": "source",
                "format": "hls" if ".m3u8" in player_stream.lower() else "mp4",
            }
        )

    default_url = next((s["url"] for s in streams if s.get("format") == "mp4"), None)
    hls_url = next((s["url"] for s in streams if s.get("format") == "hls"), None)

    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(streams),
    }


def _parse_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def parse_video_page(
    html: str,
    url: str,
    player_stream: Optional[str] = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1 span").get_text(" ", strip=True) if soup.select_one("h1 span") else None,
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

    upload_date = _meta(soup, prop="article:published_time")
    category = _meta(soup, prop="article:section")

    tags: list[str] = []
    for a in soup.select('#cat-tag ul li a[rel="tag"]'):
        txt = a.get_text(" ", strip=True)
        if txt:
            tags.append(txt)

    duration = None
    uploader = None

    for obj in json_ld:
        types = obj.get("@type")
        type_names = [str(x).lower() for x in types] if isinstance(types, list) else [str(types).lower()]
        if "blogposting" in type_names or "videoobject" in type_names:
            title = _clean_title(_first_non_empty(title, obj.get("name"), obj.get("headline"))) or title
            description = _first_non_empty(description, obj.get("description"))
            thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
            if isinstance(thumb, list):
                thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
            thumbnail = _first_non_empty(thumbnail, thumb)
            author = obj.get("author")
            if isinstance(author, dict):
                uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
            elif isinstance(author, str):
                uploader = author.strip() or None
            upload_date = _first_non_empty(upload_date, obj.get("datePublished"), obj.get("dateModified"))
            category = _first_non_empty(category, obj.get("articleSection"))
            tags.extend(_as_list(obj.get("keywords")))

    views_node = soup.select_one("div.views-infos")
    views = _clean_views_text(views_node.get_text(" ", strip=True)) if views_node else None

    duration_node = soup.select_one("div.time-infos")
    if duration_node:
        duration = _parse_duration(duration_node.get_text(" ", strip=True)) or duration

    tags = list(dict.fromkeys([t for t in tags if t]))
    video = _extract_streams(soup, player_stream)

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
    player_stream = await resolve_player_stream(url, html)
    return parse_video_page(html, url, player_stream=player_stream)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or SITE_HOST
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path or "/")
    if query_items.get("s"):
        query_items["paged"] = str(page)
        return urlunparse((scheme, netloc, clean_path or "/", "", urlencode(query_items), ""))

    paged_path = clean_path.rstrip("/") + f"/page/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{SITE_URL}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if SITE_HOST not in parsed.netloc.lower():
        return None
    if any(x in parsed.path.lower() for x in ("/wp-content/", "/wp-json/", "/page/", "/author/", "/feed/", "/tag/")):
        return None
    if parsed.query:
        return None

    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return None
    slug = segments[0].lower()
    blocked_exact = {
        "hentai-series",
        "hentai-list",
        "top-10",
        "contact-us",
        "privacy-policy",
        "dmca",
        "about",
        "category",
        "categories",
        "censored",
        "uncensored",
        "search",
        "feed",
    }
    if slug in blocked_exact:
        return None
    return urlunparse(("https", SITE_HOST, f"/{slug}/", "", "", ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    listing = soup.select_one(MAIN_LISTING_SELECTOR) or soup.select_one("ul.listing-tube")
    if listing is None:
        return []

    for li in listing.find_all("li", recursive=False) or listing.find_all("li"):
        if len(items) >= limit:
            break
        a = li.find("a", href=True)
        if a is None:
            continue
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        img = li.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        duration = None
        views = None
        d_node = li.select_one("div.time-infos")
        if d_node:
            duration = _parse_duration(d_node.get_text(" ", strip=True))
        v_node = li.select_one("div.views-infos")
        if v_node:
            views = _clean_views_text(v_node.get_text(" ", strip=True))

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
