from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html

BASE_SITE = "https://rou.video"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_SITE}/",
}

_VIDEO_HREF_RE = re.compile(r"^/v/([A-Za-z0-9_-]{8,})(?:[/?#]|$)", re.IGNORECASE)
_EV_RE = re.compile(
    r"ev:\$R\[\d+\]=\{d:\"([^\"]+)\",k:(\d+)\}",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?:(\d+)\s*小時)?(?:(\d+)\s*分)?(?:(\d+)\s*秒)?"
)


def can_handle(host: str) -> bool:
    return "rou.video" in (host or "").lower()


def get_categories() -> list[dict[str, object]]:
    try:
        path = os.path.join(os.path.dirname(__file__), "categories.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading rou.video categories: {e}")
    return []


def _decrypt_ev(d: str, k: int) -> dict[str, Any]:
    try:
        payload = (d or "").strip().replace("\\n", "").replace(" ", "+")
        pad = "=" * ((4 - len(payload) % 4) % 4)
        raw = base64.b64decode(payload + pad)
        shift = int(k) % 256
        # Match JS String.fromCharCode(code - k) wrapping into a byte string.
        text = bytes((b - shift) % 256 for b in raw).decode("utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    getter = getattr(el, "get_text", None)
    if callable(getter):
        return getter(" ", strip=True) or None
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


def _video_id_from_href(href: str) -> Optional[str]:
    path = urlparse(urljoin(BASE_SITE + "/", href or "")).path
    m = _VIDEO_HREF_RE.match(path)
    return m.group(1) if m else None


def _watch_url(video_id: str) -> str:
    return f"{BASE_SITE}/v/{video_id}"


def _format_seconds(value: Any) -> Optional[str]:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _normalize_duration(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _format_seconds(raw)
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return _format_seconds(text)
    found = _DURATION_RE.fullmatch(text.replace(" ", ""))
    if found and any(found.groups()):
        h = int(found.group(1) or 0)
        mm = int(found.group(2) or 0)
        s = int(found.group(3) or 0)
        return _format_seconds(h * 3600 + mm * 60 + s)
    clock = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return clock.group(0) if clock else text


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").split("/") if p]


def _build_list_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url or f"{BASE_SITE}/v")
    host = parsed.netloc or "rou.video"
    parts = _path_parts(parsed.path)
    page_num = max(int(page or 1), 1)

    if not parts or parts[0].lower() in {"home"}:
        new_path = "/v"
    else:
        new_path = "/" + "/".join(parts)

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page_num <= 1:
        query.pop("page", None)
    else:
        query["page"] = str(page_num)
    query_str = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme or "https", host, new_path, "", query_str, ""))


def _fetch_html_sync(url: str) -> str:
    from curl_cffi.requests import Session

    last_error: Exception | None = None
    for impersonate in ("chrome136", "chrome131", "chrome"):
        try:
            with Session(impersonate=impersonate) as client:
                resp = client.get(url, headers=_HEADERS, timeout=30.0, allow_redirects=True)
            if resp.status_code in (403, 429, 503):
                last_error = RuntimeError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.text or ""
        except Exception as e:
            last_error = e
            continue
    raise last_error or RuntimeError(f"Failed to fetch {url}")


async def _get_html(url: str) -> str:
    try:
        html = await fetch_html(url, headers=dict(_HEADERS))
        if html and ("/v/" in html or "coverImageUrl" in html or "og:title" in html):
            return html
    except Exception:
        pass
    return await asyncio.to_thread(_fetch_html_sync, url)


def _best_card_image(block: Any) -> Optional[str]:
    if not hasattr(block, "find_all"):
        return None
    for img in block.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        alt = (img.get("alt") or "").strip()
        cls = " ".join(img.get("class") or [])
        if "blur" in cls:
            continue
        if alt or "object-cover" in cls:
            return _https(urljoin(BASE_SITE + "/", src))
    img = block.find("img")
    src = (img.get("src") or "").strip() if img else ""
    if src and not src.startswith("data:"):
        return _https(urljoin(BASE_SITE + "/", src))
    return None


def _parse_card(block: Any, *, exclude_id: str | None = None) -> Optional[dict[str, Any]]:
    href = block.get("href") if getattr(block, "name", None) == "a" else None
    if not href and hasattr(block, "select_one"):
        link = block.select_one('a[href^="/v/"]')
        href = link.get("href") if link else None
    video_id = _video_id_from_href(href or "")
    if not video_id or video_id == exclude_id:
        return None
    title = _first_non_empty(
        _text(block.select_one("h3") if hasattr(block, "select_one") else None),
        block.get("title") if hasattr(block, "get") else None,
    )
    if not title and hasattr(block, "find_all"):
        for img in block.find_all("img"):
            alt = (img.get("alt") or "").strip()
            if alt:
                title = alt
                break
    duration = None
    views = None
    if hasattr(block, "find_all"):
        for span in block.find_all("span"):
            raw = _text(span) or ""
            if "觀看" in raw or "观看" in raw:
                views = raw.replace("次觀看", "").replace("次观看", "").strip() or raw
            elif _DURATION_RE.fullmatch(raw.replace(" ", "")) and any(ch.isdigit() for ch in raw):
                duration = _normalize_duration(raw)
    return {
        "url": _watch_url(video_id),
        "title": (title or "Unknown Video").strip(),
        "thumbnail_url": _best_card_image(block),
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


def _parse_cards(
    soup: BeautifulSoup,
    *,
    exclude_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in soup.select('a[href^="/v/"]'):
        href = block.get("href") or ""
        if "/api/" in href:
            continue
        card = _parse_card(block, exclude_id=exclude_id)
        if not card or card["url"] in seen:
            continue
        # Skip header/nav links that are not video tiles.
        if not card.get("thumbnail_url") and not (hasattr(block, "select_one") and block.select_one("h3")):
            continue
        seen.add(card["url"])
        items.append(card)
        if limit and len(items) >= limit:
            break
    return items


def _extract_ev_stream(html: str) -> Optional[str]:
    m = _EV_RE.search(html or "")
    if not m:
        m = re.search(r"\bev:\{d:\"([^\"]+)\",k:(\d+)\}", html or "")
    if not m:
        return None
    decrypted = _decrypt_ev(m.group(1), int(m.group(2)))
    url = _first_non_empty(decrypted.get("videoUrl"), decrypted.get("url"))
    if not url:
        return None
    media = str(url).strip().replace("\\/", "/")
    if media.startswith("//"):
        media = "https:" + media
    elif media.startswith("/"):
        media = urljoin(BASE_SITE + "/", media)
    else:
        media = _https(media)
    if not media.startswith("http"):
        return None
    return media


def _related_from_html(soup: BeautifulSoup, *, exclude_id: str) -> list[dict[str, Any]]:
    return _parse_cards(soup, exclude_id=exclude_id, limit=12)


def parse_watch_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _video_id_from_href(urlparse(url).path) or ""
    title = _first_non_empty(
        _meta(soup, prop="og:title"),
        _text(soup.select_one("h1")),
        _text(soup.find("title")),
    )
    if title:
        for suffix in (" - 肉視頻,您的私人AV影院", " - 肉視頻", " | 肉視頻"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _meta(soup, prop="og:image")
    if thumbnail:
        thumbnail = _https(thumbnail)

    tags: list[str] = []
    for a in soup.select('a[href^="/t/"]'):
        name = _text(a)
        if name and name not in tags and len(name) < 40:
            tags.append(name)

    duration = None
    for span in soup.select("span"):
        raw = _text(span) or ""
        if _DURATION_RE.fullmatch(raw.replace(" ", "")) and any(ch.isdigit() for ch in raw):
            duration = _normalize_duration(raw)
            break

    stream_url = _extract_ev_stream(html)
    streams: list[dict[str, str]] = []
    if stream_url:
        fmt = "hls" if ".m3u8" in stream_url.lower() else "mp4"
        streams.append({"quality": "Auto", "url": stream_url, "format": fmt, "server": "Primary"})

    return {
        "url": url if url.startswith("http") else _watch_url(video_id),
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "upload_date": None,
        "uploader_name": None,
        "category": tags[0] if tags else None,
        "tags": tags,
        "related_videos": _related_from_html(soup, exclude_id=video_id),
        "video": {
            "streams": streams,
            "default": stream_url,
            "has_video": bool(streams),
        },
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _video_id_from_href(urlparse(url).path or "")
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {url}")
    fetch_url = _watch_url(video_id)
    html = await _get_html(fetch_url)
    data = parse_watch_page(html, fetch_url)
    if not data.get("title") and not data.get("video", {}).get("has_video"):
        raise ValueError(f"No video information found for ID: {video_id}")
    return data


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, object]]:
    page_url = _build_list_page_url(base_url or f"{BASE_SITE}/v", page)
    try:
        html = await _get_html(page_url)
    except Exception as e:
        print(f"Error listing rou.video videos: {e}")
        return []
    soup = BeautifulSoup(html, "lxml")
    items = _parse_cards(soup, limit=limit)
    if items:
        return items[:limit] if limit else items

    # Fallback: collect watch URLs if the card markup changes.
    seen: set[str] = set()
    for m in re.finditer(r'href="(/v/[A-Za-z0-9_-]{8,})"', html or ""):
        video_id = _video_id_from_href(m.group(1))
        if not video_id:
            continue
        href = _watch_url(video_id)
        if href in seen:
            continue
        seen.add(href)
        items.append(
            {
                "url": href,
                "title": "Unknown Video",
                "thumbnail_url": None,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )
        if limit and len(items) >= limit:
            break
    return items[:limit] if limit else items
