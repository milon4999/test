from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

SITE = "https://www.vrsmash.com"


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "vrsmash.com" or h.endswith(".vrsmash.com")


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
        "Referer": "https://www.vrsmash.com/",
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


def _normalize_duration_iso(value: Any) -> Optional[str]:
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


def _normalize_media_url(src: str, base: str = SITE + "/") -> Optional[str]:
    u = (src or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(base, u)
    if not u.startswith("http"):
        return None
    return u


def _clean_thumb_url(value: Optional[str]) -> Optional[str]:
    u = _normalize_media_url(value) if value else None
    if not u:
        return None
    # Rendered images are proxied through Cloudflare: https://www.vrsmash.com/cdn-cgi/image/.../<real>
    if "cdn-cgi/image/" in u:
        m = re.search(r"https?://cdn-pub\.vrsmash\.com/[^\s\"'<>]+", u)
        if m:
            return m.group(0)
    return u


def _title_from_alt(alt: Optional[str]) -> Optional[str]:
    if not alt:
        return None
    t = str(alt).strip()
    t = re.sub(r"\s*VR porn video from .+?\s*$", "", t)
    return t.strip() or None


def _extract_video_views(soup: BeautifulSoup) -> Optional[str]:
    for sub_item in soup.select(".ui-player-title__sub-item"):
        svg = sub_item.select_one("svg[aria-label='Views']")
        if svg:
            text_el = sub_item.select_one(".ui-player-title__sub-text")
            if text_el:
                return text_el.get_text(" ", strip=True)
    return None


def _find_video_object(json_ld: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    # A video detail page nests the VideoObject under mainEntity inside the @graph.
    for obj in json_ld:
        if obj.get("@type") == "VideoObject":
            return obj
    for obj in json_ld:
        main = obj.get("mainEntity")
        if isinstance(main, dict) and main.get("@type") == "VideoObject":
            return main
        if isinstance(main, dict):
            t = main.get("@type")
            types = [str(x).lower() for x in t] if isinstance(t, list) else [str(t).lower()]
            if "videoobject" in types:
                return main
    return None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)
    vo = _find_video_object(json_ld)

    title = _first_non_empty(
        _meta(soup, prop="og:title"),
        _meta(soup, name="twitter:title"),
        vo.get("name") if vo else None,
        soup.title.get_text(strip=True) if soup.title else None,
    ) or "Unknown Video"
    # Strip the site suffix from the <title> if present ("... VR Porn Video - VRSmash.com").
    title = re.sub(r"\s*[-|]\s*VRSmash\.com\s*$", "", title).strip() or title

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        vo.get("description") if vo else None,
    )

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if vo and vo.get("thumbnailUrl"):
        thumbnail = _first_non_empty(thumbnail, vo.get("thumbnailUrl"))

    duration = None
    og_dur = _meta(soup, prop="og:video:duration")
    if og_dur:
        try:
            duration = _normalize_duration_iso(int(og_dur))
        except Exception:
            duration = None
    if not duration and vo:
        duration = _normalize_duration_iso(vo.get("duration"))

    upload_date = vo.get("uploadDate") if vo else None
    if not upload_date:
        upload_date = _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        )

    tags: list[str] = []
    if vo:
        tags.extend(_as_list(vo.get("genre")))

    uploader = None
    if vo:
        producer = vo.get("producer") or vo.get("publisher")
        if isinstance(producer, dict):
            uploader = _first_non_empty(producer.get("name"), uploader)

    views = _extract_video_views(soup)

    category = None
    if tags:
        category = tags[0]

    # Stream: the page exposes a single signed MP4 via og:video.
    stream_url = _meta(soup, prop="og:video")
    stream_url = _normalize_media_url(stream_url) if stream_url else None
    streams: list[dict[str, str]] = []
    if stream_url:
        streams.append({"url": stream_url, "quality": "source", "format": "mp4"})

    video = {
        "streams": streams,
        "hls": None,
        "default": stream_url,
        "has_video": bool(stream_url),
    }

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
    netloc = parsed.netloc or "www.vrsmash.com"
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page > 1:
        query_items["p"] = str(page)
    return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))


def _normalize_video_url(href: str) -> Optional[str]:
    u = _normalize_media_url(href)
    if not u:
        return None
    parsed = urlparse(u)
    if "vrsmash.com" not in parsed.netloc.lower():
        return None
    m = re.match(r"^/video/([^/]+)/?$", parsed.path)
    if not m:
        return None
    return urlunparse((parsed.scheme or "https", "www.vrsmash.com", f"/video/{m.group(1)}/", "", "", ""))


def _collect_ld_lookup(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for obj in _parse_json_ld(soup):
        if isinstance(obj.get("associatedMedia"), list):
            for media in obj["associatedMedia"]:
                if not isinstance(media, dict):
                    continue
                u = _normalize_video_url(media.get("url") or "")
                if u:
                    lookup[u] = media
        if isinstance(obj.get("mainEntity"), dict) and isinstance(obj["mainEntity"].get("associatedMedia"), list):
            for media in obj["mainEntity"]["associatedMedia"]:
                if not isinstance(media, dict):
                    continue
                u = _normalize_video_url(media.get("url") or "")
                if u:
                    lookup[u] = media
    return lookup


def _publisher_name(media: dict[str, Any]) -> Optional[str]:
    pub = media.get("publisher") or media.get("producer")
    if isinstance(pub, dict):
        return _first_non_empty(pub.get("name"))
    if isinstance(pub, str):
        return pub.strip() or None
    return None


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    ld_lookup = _collect_ld_lookup(soup)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in soup.select("article.ui-video-card"):
        if len(items) >= limit:
            break
        a = card.select_one("a[href^='/video/']")
        if not a:
            continue
        url = _normalize_video_url(a.get("href") or "")
        if not url or url in seen:
            continue

        img = card.select_one("img.ui-video-card__cover")
        thumb = _clean_thumb_url(img.get("src") if img else None)
        if not thumb and img:
            srcset = (img.get("srcset") or "").split(" ")[0].strip()
            thumb = _clean_thumb_url(srcset)

        dur_el = card.select_one(".ui-video-card__time span")
        duration = dur_el.get_text(" ", strip=True) if dur_el else None
        views = None
        for text_el in card.select("span.ui-video-card__text"):
            t = text_el.get_text(" ", strip=True)
            if re.match(r"^\d+(?:\.\d+)?[KMB]?$", t):
                views = t
                break
        if views is None:
            text_spans = card.select("span.ui-video-card__text")
            if text_spans:
                views = text_spans[-1].get_text(" ", strip=True) or None
        studio_a = card.select_one("a.ui-video-card__studio-link")
        uploader = studio_a.get("title") if studio_a else None

        ld = ld_lookup.get(url) or {}
        title = _first_non_empty(
            ld.get("name"),
            _title_from_alt(img.get("alt") if img else None),
        ) or "Unknown Video"
        thumb = _first_non_empty(_clean_thumb_url(ld.get("thumbnailUrl")) if ld.get("thumbnailUrl") else None, thumb)
        uploader = _first_non_empty(uploader, _publisher_name(ld))
        upload_date = _first_non_empty(ld.get("uploadDate"))

        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
                "upload_date": upload_date,
            }
        )

    # Fallback: JSON-LD associatedMedia only (e.g. if card markup changes).
    if not items:
        for url, media in ld_lookup.items():
            if len(items) >= limit:
                break
            title = media.get("name") or "Unknown Video"
            thumb = _clean_thumb_url(media.get("thumbnailUrl")) if media.get("thumbnailUrl") else None
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": None,
                    "views": None,
                    "uploader_name": _publisher_name(media),
                    "upload_date": media.get("uploadDate"),
                }
            )

    return items[:limit]
