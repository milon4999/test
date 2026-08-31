from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

import aiohttp

from app.core.pool import fetch_html as pool_fetch_html, pool

BASE_HOST = "porntrex.com"
_BASE = f"https://www.{BASE_HOST}"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# PornTrex serves an expired TLS certificate; verify=False is required.
_FETCH_KWARGS: dict[str, Any] = {"ssl": False}

_VIDEO_HREF_RE = re.compile(r"^/video/\d+/[^/?#]+/?$", flags=re.IGNORECASE)

_DEAD_RE = re.compile(
    r"this video (?:was|has been) deleted|video (?:was|has been) removed"
    r"|no longer available|video is unavailable",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"(video(?:_alt)?_url\d*)\s*:\s*'([^']+)'", re.IGNORECASE)
_TEXT_RE = re.compile(r"(video(?:_alt)?_url\d*)_text\s*:\s*'([^']*)'", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == BASE_HOST or h == f"www.{BASE_HOST}" or h.endswith("." + BASE_HOST)


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
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    return await pool_fetch_html(url, headers=headers, **_FETCH_KWARGS)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" | PornTrex", " - PornTrex", " | PornTrex.com", " - PornTrex.com"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


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


def _quality_rank(label: str | None) -> int:
    if not label:
        return -1
    m = re.search(r"(\d{3,4})\s*p", label, re.IGNORECASE)
    return int(m.group(1)) if m else -1


def _parse_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    # ISO-8601 (KVS JSON-LD): PT12M34S / PT1H2M3S
    iso = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(text))
    if iso and any(iso.groups()):
        h, mnt, s = (int(x) if x else 0 for x in iso.groups())
        return f"{h}:{mnt:02d}:{s:02d}" if h else f"{mnt:02d}:{s:02d}"
    m = re.search(r"\b(\d{1,2}:)?\d{1,2}:\d{2}\b", str(text))
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"([\d,\.\s]+)\s*views", text, flags=re.IGNORECASE)
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
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").split("?", 1)[0].strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{_BASE}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if not (host == BASE_HOST or host == f"www.{BASE_HOST}" or host.endswith("." + BASE_HOST)):
        return None
    if not _VIDEO_HREF_RE.match(parsed.path or ""):
        return None
    return urlunparse(("https", f"www.{BASE_HOST}", parsed.path.rstrip("/") + "/", "", "", ""))


async def _resolve_get_file(get_file_url: str, *, timeout: float = 20.0) -> Optional[str]:
    """Follow the cookie-bound get_file 302 in the pooled session (same session as the
    page fetch) and return the final portable signed CDN URL. None when resolve fails."""
    sep = "&" if "?" in get_file_url else "?"
    url = f"{get_file_url}{sep}rnd={int(time.time() * 1000)}"
    session = await pool.get_session()
    try:
        async with session.get(
            url,
            headers={"User-Agent": _UA, "Referer": f"{_BASE}/", "Range": "bytes=0-1"},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            ssl=False,
        ) as resp:
            final = str(resp.url)
            status = resp.status
    except Exception:
        return None
    if status >= 400 or "/get_file/" in final:
        return None
    return final


def _extract_flashvars_streams(html: str) -> list[dict[str, str]]:
    """KVS flashvars: video_url / video_alt_url{,2,3} + *_text quality labels.

    Same-session get_file resolution happens later (async) — here we only collect.
    """
    quality_by_var: dict[str, str] = {}
    for m in _TEXT_RE.finditer(html):
        quality_by_var[m.group(1).lower()] = m.group(2).strip()

    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(html):
        var = m.group(1).lower()
        url = m.group(2)
        if url in seen:
            continue
        seen.add(url)
        streams.append({"url": url, "quality": quality_by_var.get(var) or "source", "format": "mp4"})
    return streams


async def _resolve_streams(streams: list[dict[str, str]]) -> list[dict[str, str]]:
    # Sequential on purpose: get_file tokens are single-use/session-bound and the
    # server 410s on parallel hits; resolve one, then the next (goon-foss pattern).
    out: list[dict[str, str]] = []
    for s in streams:
        resolved = await _resolve_get_file(s["url"])
        out.append({**s, "url": resolved} if resolved else s)
    return out


def _extract_related(soup: BeautifulSoup, limit: int = 24) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/video/']"):
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue
        img = a.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue
        title = a.get("title") or (img.get("alt") if img is not None else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"
        seen.add(href)
        related.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )
        if len(related) >= limit:
            break
    return related


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    if _DEAD_RE.search(html):
        raise ValueError(f"porntrex video deleted/unavailable: {url}")

    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    thumbnail = _meta(soup, prop="og:image")
    video_tag = soup.select_one("video")
    if thumbnail is None and video_tag is not None and video_tag.get("poster"):
        thumbnail = str(video_tag.get("poster")).strip()
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    views = None
    uploader_name = None

    for obj in _parse_json_ld(soup):
        types = obj.get("@type")
        tnames = [str(x).lower() for x in types] if isinstance(types, list) else [str(types or "").lower()]
        if "videoobject" not in tnames:
            continue
        if obj.get("name"):
            title = _clean_title(str(obj["name"])) or title
        duration = _first_non_empty(duration, str(obj.get("duration")) if obj.get("duration") else None)
        if obj.get("thumbnailUrl"):
            thumb = obj.get("thumbnailUrl")
            if isinstance(thumb, list):
                thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
            thumbnail = _first_non_empty(thumbnail, str(thumb) if thumb else None)
        author = obj.get("author")
        if isinstance(author, dict):
            uploader_name = _first_non_empty(author.get("name"), uploader_name)
        elif isinstance(author, str):
            uploader_name = _first_non_empty(author, uploader_name)

    text_blob = soup.get_text(" ", strip=True)
    if not duration:
        duration = _parse_duration(text_blob)
    views = _extract_views(text_blob)

    tags: list[str] = []
    for tag_link in soup.select("a[href*='/search/'], a[href*='/tags/']"):
        label = (tag_link.get_text(" ", strip=True) or "").strip()
        if not label or len(label) > 40:
            continue
        if label.lower() in ("tags", "-", "|"):
            continue
        tags.append(label)
    tags = list(dict.fromkeys(tags))[:20]

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
        "upload_date": None,
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
            "_pending": _extract_flashvars_streams(html),
        },
        "related_videos": _extract_related(soup),
        "preview_url": None,
    }


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


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    data = parse_video_page(html, url)
    pending: list[dict[str, str]] = data["video"].pop("_pending", [])
    if pending:
        resolved = await _resolve_streams(pending)
        playable = [s for s in resolved if "/get_file/" not in s["url"]]
        streams = playable or resolved
        streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
        hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
        data["video"] = {
            "streams": streams,
            "hls": hls,
            "default": streams[0]["url"] if streams else None,
            "has_video": bool(streams),
        }
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or f"www.{BASE_HOST}"
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    if re.search(r"/latest-updates/(\d+)/?$", path):
        page_path = re.sub(r"/(\d+)/?$", f"/{page}/", path)
        return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))
    if re.search(r"/search/(\d+)/?$", path):
        page_path = re.sub(r"/(\d+)/?$", f"/{page}/", path)
        return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))

    # KVS generic: /videos/{n}/, /categories/{slug}/{n}/, /members/{name}/{n}/
    m = re.match(r"^(.*?/)(\d+)/?$", path)
    if m:
        page_path = f"{m.group(1)}{page}/"
        return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))
    page_path = path.rstrip("/") + f"/{page}/"
    return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))


def _parse_list_card(a: Any) -> Optional[dict[str, Any]]:
    href = _normalize_video_href(a.get("href") or "")
    if not href:
        return None

    img = a.find("img") or (a.select_one(".video-tile-model img") if a else None)
    thumb = _best_image_url(img)
    if not thumb:
        return None

    title = (
        a.get("title")
        or (img.get("alt") if img is not None else None)
        or a.get_text(" ", strip=True)
    )
    if not title or title in ("POST THUMB", "thumb"):
        return None
    title = _clean_title(title) or "Unknown Video"

    duration = None
    dur_el = a.select_one(".duration, .durations") if a is not None else None
    if dur_el is not None:
        duration = _parse_duration(dur_el.get_text(" ", strip=True))

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": None,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or f"{_BASE}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href*='/video/']"):
        if len(items) >= limit:
            break
        item = _parse_list_card(a)
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)

    return items[:limit]
