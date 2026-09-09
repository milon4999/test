from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://fullxcinema.com/"
SITE_HOST = "fullxcinema.com"
SITE_ALIASES = frozenset({"fullxcinema.com", "www.fullxcinema.com"})

_RESERVED_SLUGS = frozenset(
    {
        "category",
        "actors",
        "actor",
        "tags",
        "tag",
        "page",
        "feed",
        "comments",
        "wp-content",
        "wp-json",
        "wp-admin",
        "search",
    }
)

_POST_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?fullxcinema\.com/(?P<slug>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)
_MP4_RE = re.compile(
    r"https?://[^\s\"'<>]+\.mp4(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".fullxcinema.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    """fullxcinema sits behind Cloudflare: curl_cffi first (needs TLS 1.2 +
    browser headers), pool as fallback."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        pass
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        for imp in ("chrome120", "chrome116"):
            try:
                async with AsyncSession(impersonate=imp, timeout=45.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200 and len(resp.text) > 2000:
                        return resp.text
            except Exception:
                await asyncio_sleep(1.0)
    try:
        return await pool_fetch_html(url, headers=_pool_headers(referer))
    except Exception:
        return ""


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _pool_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    return headers


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
    if t.endswith(" - fullxcinema"):
        t = t[: -len(" - fullxcinema")].strip()
    return t or None


def _is_reserved_path(path: str) -> bool:
    parts = [p for p in (path or "").strip("/").split("/") if p]
    if not parts:
        return False
    if parts[0].lower() in _RESERVED_SLUGS:
        return True
    if len(parts) >= 2 and parts[0].lower() in ("category", "tag", "actor", "author"):
        return True
    return False


def _normalize_post_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0]
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    if _is_reserved_path(parsed.path or ""):
        return None
    if "/wp-content/" in (parsed.path or "").lower():
        return None
    m = _POST_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    slug = (m.group("slug") or "").lower()
    if not slug or slug in _RESERVED_SLUGS:
        return None
    return f"https://{SITE_HOST}/{slug}/"


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
        return url
    return None


def _decode_player_q(q: str) -> str:
    """clean-tube-player payload: base64(q) -> URL-encoded video.js markup.
    The inner src is DOUBLE-URL-encoded (%2520 = space)."""
    raw = (q or "").strip()
    if not raw:
        return ""
    pad = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(pad).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    # URL-decode percent sequences (may be double-encoded)
    decoded = re.sub(r"%25([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), decoded)
    decoded = decoded.replace("%20", " ").replace("\\/", "/")
    return decoded


def _streams_from_html(html: str, soup: BeautifulSoup, page_url: str) -> dict[str, Any]:
    """
    Two player patterns:
      A) direct MP4: meta[itemprop=contentURL] (cdn.freevidco.com) and/or the
         clean-tube-player iframe q payload with <source src="...mp4">
      B) external embed: .myiframe iframe#myiframe (heroero/videoupornia/...)
         with #sourcetabs alternate anchors.
    """
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # A1) itemprop=contentURL direct MP4
    content_url = (soup.find("meta", attrs={"itemprop": "contentURL"}) or {}).get("content")
    if content_url and content_url.startswith("http") and ".mp4" in content_url.lower():
        streams.append({"url": content_url.strip(), "quality": "source", "format": "mp4"})
        seen.add(content_url.strip())

    # A2) clean-tube-player q payload -> <source src>
    for iframe in soup.select('iframe[src*="player-x.php?q="]'):
        src = iframe.get("src") or ""
        mm = re.search(r"q=([^&\"']+)", src)
        if not mm:
            continue
        payload = _decode_player_q(mm.group(1))
        if not payload:
            continue
        for sm in re.finditer(r'<source[^>]+src="([^"]+)"', payload):
            u = sm.group(1).strip()
            if u.startswith("http") and u not in seen and ".mp4" in u.lower():
                seen.add(u)
                streams.append({"url": u, "quality": "source", "format": "mp4"})
        # poster from payload (unused for stream, but could set thumbnail)

    # B) external embeds: #myiframe + #sourcetabs anchors
    embed_hosts = ("heroero", "videoupornia", "dood", "doodstream", "mixdrop",
                   "streamtape", "suzihaza", "diasfem")
    for iframe in soup.select("#myiframe, .myiframe iframe, iframe.responsive-iframe"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        low = src.lower()
        if not src.startswith("http") or src in seen:
            continue
        if not any(h in low for h in embed_hosts):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "Server 1", "format": "embed"})

    if not streams:
        # No direct: expose the first recognized embed as the playable URL
        for a in soup.select("#sourcetabs a[href]"):
            href = (a.get("href") or "").strip()
            low = href.lower()
            if href.startswith("http") and any(h in low for h in embed_hosts):
                streams.append({"url": href, "quality": "Server 1", "format": "embed"})
                break

    if not streams:
        # Last resort: any direct mp4 regex hits in the page (non-uploads)
        for m in _MP4_RE.findall(html):
            u = m.strip()
            if "/wp-content/" in u or u in seen:
                continue
            seen.add(u)
            streams.append({"url": u, "quality": "source", "format": "mp4"})
            break

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_card_blocks(html: str, *, limit: int) -> list[dict[str, Any]]:
    """Parse article.loop-video cards: href, title, CSS thumb (data-main-thumb),
    verbatim abbreviated views (135K), duration (13:18)."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in re.split(r"(?=<article[^>]*class=\"[^\"]*loop-video)", html)[1:]:
        if len(items) >= limit:
            break
        lm = re.search(r"<a href=\"(https://fullxcinema\.com/[^\"]+/)\"[^>]*title=\"([^\"]*)\"", block)
        if not lm:
            continue
        url = _normalize_post_href(lm.group(1))
        if not url or url in seen:
            continue
        seen.add(url)

        chunk = block[: block.find("</article>") + 11] if "</article>" in block else block[:5000]

        title = None
        hm = re.search(r'<header class="entry-header">\s*<span>(.*?)</span>', chunk, re.S)
        if hm:
            title = _clean_title(hm.group(1).strip())
        if not title:
            title = _clean_title(lm.group(2))

        thumb = None
        tmm = re.search(r'data-main-thumb="([^"]+)"', chunk)
        if tmm:
            thumb = tmm.group(1)
        else:
            imm = re.search(r'<img[^>]+src="([^"]+)"', chunk)
            thumb = imm.group(1) if imm else None

        vm = re.search(r'<span class="views"><i class="fa fa-eye"></i>\s*([^<]+)</span>', chunk)
        views = vm.group(1).strip() if vm else None
        dm = re.search(r'<span class="duration"><i class="fa fa-clock-o"></i>\s*([^<]+)</span>', chunk)
        duration = dm.group(1).strip() if dm else None

        items.append(
            {
                "url": url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items[:limit]


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items = _parse_card_blocks(html, limit=limit)
    if len(items) < limit:
        for a in soup.select('a[href^="https://fullxcinema.com/"]'):
            if len(items) >= limit:
                break
            url = _normalize_post_href(a.get("href") or "")
            if not url or url in {i["url"] for i in items}:
                continue
            title_el = a.select_one("header.entry-header span")
            if not title_el:
                continue
            items.append(
                {
                    "url": url,
                    "title": _clean_title(title_el.get_text(" ", strip=True)) or "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )
    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/").rstrip("/") or "/"
    qs = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True) if v}

    # WordPress path pagination: /page/2/ (trailing slash required)
    if page_num > 1:
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"
        path = f"{path}/page/{page_num}/" if path != "/" else f"/page/{page_num}/"
    elif re.search(r"/page/\d+$", path, re.I):
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or "/"

    path = path if path.endswith("/") else f"{path}/"
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", urlencode(qs) if qs else "", "")
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_post_href(url) or url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("article img, .post-thumbnail img")),
    )

    description = _first_non_empty(
        _meta(soup, prop="og:description"), _meta(soup, name="description")
    )

    # Microdata on article[itemprop=video]: duration ISO (P0DT0H13M18S), uploadDate
    duration: Optional[str] = None
    upload_date: Optional[str] = None
    art = soup.select_one('article[itemprop="video"]')
    if art:
        dm = art.select_one('[itemprop="duration"]')
        if dm:
            raw = (dm.get("content") or dm.get_text(strip=True) or "").strip()
            im = re.match(
                r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", raw, re.IGNORECASE
            )
            if im:
                h = int(im.group(2) or 0)
                mi = int(im.group(3) or 0)
                s = int(im.group(4) or 0)
                if h:
                    duration = f"{h}:{mi:02d}:{s:02d}"
                elif mi or s:
                    duration = f"{mi:02d}:{s:02d}"
        um = art.select_one('[itemprop="uploadDate"]')
        if um:
            upload_date = (um.get("content") or um.get_text(strip=True) or "").strip() or None

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"]'):
        t = a.get_text(" ", strip=True)
        if t and t not in tags and len(t) < 60:
            tags.append(t)

    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or _streams_from_html(html, soup, canon)

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
    return "file not found" in low or "404" in low and "not found" in low


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_post_href(url)
    if not canon:
        raise ValueError(f"Unsupported fullxcinema URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    soup = BeautifulSoup(html, "lxml")
    video_data = _streams_from_html(html, soup, canon)
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
