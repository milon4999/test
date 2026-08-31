from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_HOST = "sxyprn.com"
_BASE = f"https://{BASE_HOST}"

_POST_HREF_RE = re.compile(r"^/post/[0-9a-f]{12,13}\.html$", flags=re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == BASE_HOST or h.endswith("." + BASE_HOST)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = f"{_BASE}/") -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
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


def _collapse(text: str | None) -> Optional[str]:
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    return t or None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = _clean_list_title(title) or str(title).strip()
    for suffix in (
        " on SexyPorn OG",
        " on the SexyPorn",
        " on SexyPorn",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    # Match the generic "- [duration] (date) on the SexyPorn" tail if present.
    t = re.sub(r"\s*-\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*\(\d{2}\.\d{2}\.\d{4}\)\s+on the SexyPorn$", "", t).strip()
    return _collapse(t)


def _clean_list_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title)
    # The post text embeds hashtags, external links and {NEW}-style markers; strip them.
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"#\S+", " ", t)
    t = t.replace("{", " ").replace("}", " ")
    t = re.sub(r"\b(NEW|New)\b\s*", "", t, count=1)
    t = re.sub(r"\s*->\s*", " ", t)
    return _collapse(t)


def _extract_duration_iso(text: str | None) -> Optional[str]:
    """ISO-8601 PT11M2S -> 11:02 / PT1H22M17S -> 1:22:17."""
    if not text:
        return None
    m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(text))
    if not m:
        return None
    h, mnt, s = (int(x) if x else 0 for x in m.groups())
    if not h and not mnt and not s:
        return None
    if h:
        return f"{h}:{mnt:02d}:{s:02d}"
    return f"{mnt:02d}:{s:02d}"


def _extract_duration_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(\d{1,2}:)?\d{1,2}:\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d\s,.]*)\s*views", text, flags=re.IGNORECASE)
    if not m:
        return None
    return re.sub(r"[^\d]", "", m.group(1)) or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or "converting.png" in url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url
    return None


_EMBED_HOST_SUFFIXES = (
    "vidara.so",
    "vidara.to",
    "lulustream.com",
    "luluvdo.com",
    "doodstream.co",
    "doodstream.com",
    "dood.wf",
    "dood.yt",
    "dood.video",
    "dsvplay.com",
    "savefiles.com",
    "savefiles.io",
)


def _is_embed_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in _EMBED_HOST_SUFFIXES)


def _normalize_embed_url(url: str) -> Optional[str]:
    """Convert plain watch links from post text to their embed (player) form.

    vidara.so/v/{id}      -> vidara.so/e/{id}
    lulustream.com/{id}   -> luluvdo.com/e/{id}
    dood*/{id}            -> dood*/e/{id}   (keep existing /e/ as-is)
    """
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    p = urlparse(u)
    if not p.scheme.startswith("http"):
        return None
    host = p.netloc.lower()
    path = p.path or "/"

    if host.endswith("vidara.so") or host.endswith("vidara.to"):
        m = re.match(r"^/v/([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://{host}/e/{m.group(1)}"
        return None

    if host.endswith("lulustream.com"):
        m = re.match(r"^/([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://luluvdo.com/e/{m.group(1)}"
        return None

    dood = any(host == h or host.endswith("." + h) for h in ("doodstream.co", "doodstream.com", "dood.wf", "dood.yt", "dood.video", "dsvplay.com"))
    if dood:
        m = re.match(r"^/e/([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://{host}/e/{m.group(1)}"
        m = re.match(r"^/(?:d/)?([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://{host}/e/{m.group(1)}"
        return None

    return None


def _extract_embed_streams(post_root: Any) -> dict[str, Any]:
    """External players from the post text. The on-page /cdn8/...vid direct link
    is intentionally NOT returned: sxyprn's signed .vid URLs 404 server-side."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    if post_root is not None:
        anchors = post_root.select("a.extlink[href], a.extlink_icon[href]")
    else:
        anchors = []

    for a in anchors:
        raw = (a.get("href") or "").strip()
        embed = _normalize_embed_url(raw)
        if not embed:
            if not raw.startswith("http"):
                continue
            candidate = raw if raw.startswith("https") else f"https:{raw}" if raw.startswith("//") else raw
            if not _is_embed_host(candidate):
                continue
            embed = candidate
        if embed in seen:
            continue
        seen.add(embed)
        streams.append(
            {
                "url": embed,
                "quality": f"Server {len(streams) + 1}",
                "format": "embed",
            }
        )

    default_url = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default_url,
        "has_video": bool(streams),
    }


def _post_text_title(container: Any) -> Optional[str]:
    """Extract post text without external-link anchors (their hostnames are noise)."""
    if container is None:
        return None
    text_el = container.select_one(".post_text")
    if text_el is None:
        return None
    clone = BeautifulSoup(str(text_el), "lxml")
    for ext in clone.select("a.extlink, a.extlink_icon"):
        ext.decompose()
    return _clean_list_title(clone.get_text(" ", strip=True))


def _parse_post_card(card: Any, seen: set[str]) -> Optional[dict[str, Any]]:
    a = card.select_one("a.js-pop[href^='/post/']") if card is not None else None
    if a is None:
        return None
    href = (a.get("href") or "").split("?", 1)[0].strip()
    if not _POST_HREF_RE.match(href):
        return None
    url = f"{_BASE}{href}"
    if url in seen:
        return None

    img = a.select_one("img")
    thumb = _best_image_url(img)
    if not thumb:
        return None

    title = _post_text_title(card) or _clean_list_title(a.get("title")) or "Unknown Video"
    title = _clean_title(title) or title

    duration_el = a.select_one(".duration_small")
    duration = _extract_duration_text(duration_el.get_text(" ", strip=True) if duration_el else None)

    views = None
    time_el = card.select_one(".post_control_time")
    if time_el is not None:
        views = _extract_views(time_el.get_text(" ", strip=True))

    uploader = None
    author_el = card.select_one(".pes_author_div .a_name")
    if author_el is not None:
        uploader = _collapse(author_el.get_text(" ", strip=True))

    seen.add(url)
    return {
        "url": url,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    if "Post Not Found" in html:
        raise ValueError(f"sxyprn post deleted or unavailable: {url}")

    soup = BeautifulSoup(html, "lxml")

    main_post = soup.select_one("div.post_el_post") or soup.select_one("div.post_el_small")

    title = _clean_title(
        _first_non_empty(
            _post_text_title(main_post),
            _meta(soup, prop="og:title"),
            main_post.select_one("h1").get_text(" ", strip=True) if main_post and main_post.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    thumbnail: Optional[str] = _meta(soup, prop="og:image")
    if thumbnail is None:
        thumb_meta = soup.find("meta", attrs={"itemprop": "thumbnailUrl"})
        thumbnail = str(thumb_meta.get("content")).strip() if thumb_meta and thumb_meta.get("content") else None
    video_tag = soup.select_one("video#player_el")
    if thumbnail is None and video_tag is not None and video_tag.get("poster"):
        thumbnail = str(video_tag.get("poster")).strip()
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration: Optional[str] = None
    duration_meta = soup.find("meta", attrs={"itemprop": "duration"})
    if duration_meta is not None and duration_meta.get("content"):
        duration = _extract_duration_iso(str(duration_meta.get("content")))
    if not duration and main_post is not None:
        info_el = main_post.find(string=re.compile(r"Video\s*Info\s*->"))
        if info_el is not None:
            duration = _extract_duration_text(str(info_el))

    views = None
    uploader_name = None
    if main_post is not None:
        time_el = main_post.select_one(".post_control_time")
        if time_el is not None:
            views = _extract_views(time_el.get_text(" ", strip=True))
        author_el = main_post.select_one(".pes_author_div .a_name")
        if author_el is not None:
            uploader_name = _collapse(author_el.get_text(" ", strip=True))

    tags: list[str] = []
    post_text_el = main_post.select_one(".post_text") if main_post is not None else None
    tag_root = post_text_el if post_text_el is not None else soup
    for tag_link in tag_root.select("a.hash_link"):
        label = tag_link.get("label") or tag_link.get_text(" ", strip=True)
        label = (label or "").strip().lstrip("#")
        if label:
            tags.append(label)
    tags = list(dict.fromkeys(tags))

    category = None
    if main_post is not None:
        subcat = main_post.select_one(".post_el_small_subcat")
        if subcat is not None:
            category = _collapse(subcat.get_text(" ", strip=True))

    video = _extract_embed_streams(main_post)

    related_videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.post_el_small"):
        item = _parse_post_card(card, seen)
        if item is None:
            continue
        related_videos.append(item)
        if len(related_videos) >= 24:
            break

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": category,
        "tags": tags,
        "upload_date": None,
        "video": video,
        "related_videos": related_videos,
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or BASE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    if path in ("", "/"):
        page_path = f"/main-{page}.html"
        return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))

    blog_m = re.match(r"^(/blog/[^/]+/)(\d+)(\.html)$", path, flags=re.IGNORECASE)
    if blog_m:
        page_path = f"{blog_m.group(1)}{max(0, page - 1)}{blog_m.group(3)}"
        return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))

    if path.lower().endswith(".html"):
        page_path = re.sub(r"\.html$", f"-{page}.html", path, flags=re.IGNORECASE)
    else:
        page_path = f"{path.rstrip('/')}-{page}.html"
    return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=f"{_BASE}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in soup.select("div.post_el_small"):
        if len(items) >= limit:
            break
        item = _parse_post_card(card, seen)
        if item is None:
            continue
        items.append(item)

    return items[:limit]
