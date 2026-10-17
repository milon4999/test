from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html

BASE = "https://beta.xfreehd.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://beta.xfreehd.com/",
}


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return h == "xfreehd.com" or h.endswith(".xfreehd.com")


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: Optional[str] = None, name: Optional[str] = None) -> Optional[str]:
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
            out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _video_object(objs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for obj in objs:
        t = obj.get("@type")
        if isinstance(t, list):
            if any(str(x).lower() == "videoobject" for x in t):
                return obj
        elif isinstance(t, str) and t.lower() == "videoobject":
            return obj
    return None


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None
    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            return f"{h}:{mm:02d}:{s:02d}" if h > 0 else f"{mm}:{s:02d}"
        m2 = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", v)
        return m2.group(0) if m2 else (v or None)
    return str(seconds_or_iso).strip() or None


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/(?:video|embed)/(\d+)", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _parse_card_duration(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(\d{1,2}):(\d{2}:\d{2})\b", text)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"\b(\d{1,5}):(\d{2})\b", text)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"(\d+)\s*h(?:\s*(\d+))?\s*m\b", text, flags=re.IGNORECASE)
    if m:
        h = int(m.group(1))
        min = int(m.group(2) or 0)
        return f"{h}:{min:02d}:00"
    m = re.search(r"(\d+)\s*m\s*(\d+)\s*s\b", text, flags=re.IGNORECASE)
    if m:
        min = int(m.group(1))
        sec = int(m.group(2))
        return f"{min}:{sec:02d}"
    m = re.search(r"(\d+)\s*s\b", text, flags=re.IGNORECASE)
    if m:
        sec = int(m.group(1))
        return f"0:{sec:02d}"
    m = re.search(r"(\d+)\s*m\b", text, flags=re.IGNORECASE)
    if m:
        min = int(m.group(1))
        h, rest = divmod(min, 60)
        return f"{h}:{rest:02d}:00" if h else f"{rest:02d}:00"
    return None


def _extract_tags_from_html(html: str) -> list[str]:
    tags: list[str] = []
    m = re.search(r"media/fluid/js\.php\?tx=([A-Za-z0-9+/=]+)", html or "")
    if m:
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", "ignore")
        except Exception:
            decoded = ""
        for part in decoded.split(","):
            p = part.strip()
            if p and p not in tags:
                tags.append(p)
    return tags


def _normalize_video_url(raw: str) -> Optional[str]:
    href = (raw or "").strip()
    if not href:
        return None
    m = re.match(r"^https?://[^/]*xfreehd\.com(/video/\d+/[^/?#]*)/?", href, flags=re.IGNORECASE)
    if m:
        return f"{BASE}{m.group(1)}"
    return None


def _views_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"([\d.,]+\s*[KkMmBb]?)\s*(?:views?)", text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"^\s*([\d.,]+\s*[KkMmBb]?)\s*$", text.strip())
    if not m:
        return None
    val = m.group(1).replace(" ", "").rstrip(".")
    if not val:
        return None
    if val[-1:].isdigit():
        val = val.replace(",", "")
    return val


async def fetch_page(url: str) -> str:
    return await fetch_html(url, headers=dict(_HEADERS))


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    canonical = None
    video_tag = soup.find("video", attrs={"data-target": True}) or soup.find(attrs={"data-target": True})
    if video_tag is not None:
        candidate = (video_tag.get("data-target") or "").strip()
        if candidate and "/video/" in candidate:
            canonical = f"{BASE}{candidate}" if candidate.startswith("/") else candidate

    title = _first_non_empty(
        _meta(soup, prop="og:title"),
        _meta(soup, name="twitter:title"),
    )
    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
    )
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
    )
    embed_meta = _first_non_empty(
        _meta(soup, name="twitter:player"),
        _meta(soup, prop="og:video:url"),
    )

    json_ld = _parse_json_ld(soup)
    vobj = _video_object(json_ld)

    duration = None
    upload_date = None
    views = None
    category = None
    tags: list[str] = []

    if vobj:
        ld_name = vobj.get("name")
        if ld_name:
            m_cat = re.search(r"\|\s*([^|]+?)\s*-\s*T\d+\s*-\s*XFREEHD\s*$", str(ld_name), flags=re.IGNORECASE)
            if m_cat:
                category = m_cat.group(1).strip()
        duration = _normalize_duration(vobj.get("duration"))
        m_date = re.match(r"(\d{4}-\d{2}-\d{2})", str(vobj.get("uploadDate") or ""))
        if m_date:
            upload_date = m_date.group(1)
        interaction = vobj.get("interactionStatistic")
        if isinstance(interaction, dict):
            interaction = [interaction]
        if isinstance(interaction, list):
            for entry in interaction:
                if not isinstance(entry, dict):
                    continue
                itype = entry.get("interactionType")
                if isinstance(itype, str) and "WatchAction" in itype:
                    count = entry.get("userInteractionCount")
                    if count:
                        views = str(count).strip()
                        break

    if title:
        title = re.sub(r"\s*\|\s*[^|]*-\s*T\d+\s*-\s*XFREEHD\s*$", "", title, flags=re.IGNORECASE).strip()
    else:
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        m_t = re.search(r"Embed Player\s*:\s*(.+)$", page_title, flags=re.IGNORECASE)
        if m_t:
            title = m_t.group(1).strip()

    tags = _extract_tags_from_html(html)
    if not category and tags:
        category = tags[0]

    video_id = _extract_video_id(url)
    embed_url = None
    if embed_meta and "/embed/" in embed_meta:
        m = re.search(r"https?://[^/]*xfreehd\.com/embed/\d+", embed_meta, flags=re.IGNORECASE)
        if m:
            embed_url = m.group(0)
    if not embed_url and video_id:
        embed_url = f"{BASE}/embed/{video_id}"

    streams: list[dict[str, str]] = []
    if embed_url:
        streams.append({"url": embed_url, "quality": "xfreehd", "format": "embed"})

    video = {
        "streams": streams,
        "hls": None,
        "default": embed_url,
        "has_video": bool(embed_url),
    }

    related_videos: list[dict[str, Any]] = []

    return {
        "url": url,
        "title": title or "Unknown Video",
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": category,
        "tags": tags,
        "upload_date": upload_date,
        "related_videos": related_videos,
        "preview_url": None,
        "video": video,
        "canonical_url": canonical,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    result = parse_video_page(html, url)
    canonical = result.pop("canonical_url", None)
    if "/embed/" in (url or "").lower() and canonical:
        try:
            result = parse_video_page(await fetch_page(canonical), url)
            result["url"] = url
        except Exception:
            pass
    return result


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = "https"
    netloc = "beta.xfreehd.com"
    path = p.path or "/"
    query_items = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "page"]

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    m = re.search(r"/page=(\d+)/?$", path)
    if m:
        path = path[: m.start()] + ("/" if path[: m.start()].endswith("/") else "")
    if not path or path == "/":
        path = f"/page={page}"
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    query_items.append(("page", str(page)))
    return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.video-link[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_url(a.get("href") or "")
        if not href or href in seen:
            continue

        img = a.find("img")
        thumb = None
        if img:
            for attr in ("data-src", "data-original", "src"):
                v = img.get(attr)
                if isinstance(v, str) and v.strip():
                    thumb = v.strip()
                    break
        if not thumb:
            continue
        if thumb.startswith("//"):
            thumb = f"https:{thumb}"
        if thumb.startswith("/"):
            thumb = f"{BASE}{thumb}"

        title_el = a.select_one(".video-title-new")
        title = _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt"),
            a.get_text(" ", strip=True),
        )

        duration = None
        dur_el = a.select_one(".duration-new")
        if dur_el:
            duration = _parse_card_duration(dur_el.get_text(" ", strip=True))
        if not duration and img is not None:
            duration = _parse_card_duration(a.get_text(" ", strip=True))

        views = None
        views_el = a.select_one(".video-views-new")
        if views_el:
            views = _views_from_text(views_el.get_text(" ", strip=True))

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items


def search_url(query: str) -> str:
    return f"{BASE}/search?search_query={quote(unquote(query or ''))}&search_type=videos"


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
