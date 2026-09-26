from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


SITE_HOST = "w8.mypornerleak.com"


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return h == "mypornerleak.com" or h.endswith(".mypornerleak.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or f"https://{SITE_HOST}/",
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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-main-thumb", "data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (" - MyPornerLeak", " | MyPornerLeak"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _normalize_post_href(href: str) -> Optional[str]:
    """Return canonical single-post URL on a mypornerleak.com host, or None."""
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "mypornerleak.com" not in parsed.netloc.lower():
        return None
    if any(x in parsed.path.lower() for x in ("/wp-content/", "/wp-json/", "/wp-admin/", "/feed/", "/page/", "/category/", "/tag/", "/actors")):
        return None
    if parsed.query:
        return None

    path = parsed.path.strip("/")
    if not path or "/" in path:
        return None
    slug = path.split("/", 1)[0].lower()
    blocked = {
        "actors", "page", "category", "tag", "search", "about", "contact",
        "privacy-policy", "dmca", "18-u-s-c-2257", "terms", "onlyfans-porn",
    }
    if slug in blocked:
        return None

    host = "mypornerleak.com" if parsed.netloc.lower() == "mypornerleak.com" else parsed.netloc.lower()
    return urlunparse(("https", host, f"/{slug}/", "", "", ""))


def _collect_embed_urls(soup: BeautifulSoup) -> list[str]:
    """Collect player embed URLs from `span.change-video[data-embed]` (Player 01..NN)."""
    urls: list[str] = []
    seen: set[str] = set()
    for el in soup.select(".change-video[data-embed]"):
        src = (el.get("data-embed") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        urls.append(src)
    return urls


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
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

    embed_urls = _collect_embed_urls(soup)

    # Same pattern as hornysimp / xxxparodyhd: expose each player tab as its own embed stream.
    streams: list[dict[str, str]] = []
    for idx, e in enumerate(embed_urls, start=1):
        streams.append({"url": e, "quality": f"Player {idx:02d}", "format": "embed"})

    default_url = embed_urls[0] if embed_urls else None

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": [],
        "upload_date": None,
        "video": {
            "streams": streams,
            "hls": None,
            "default": default_url,
            "has_video": bool(default_url),
        },
        "related_videos": [],
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
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path)

    # WordPress search: /?s={query} -> add paged param.
    if query_items.get("s"):
        query_items["paged"] = str(page)
        return urlunparse((scheme, netloc, clean_path, "", urlencode(query_items), ""))

    # Everything else (home, categories, filters): path-based /page/{n}/ pagination.
    page_path = clean_path.rstrip("/") + f"/page/{page}/"
    return urlunparse((scheme, netloc, page_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or f"https://{SITE_HOST}/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_post_href(a.get("href") or "")
        if not href or href in seen:
            continue

        container = a.find_parent("article") or a
        img = a.find("img") or (container.find("img") if container else None)

        # The card exposes a dedicated thumbnail on the <article> via data-main-thumb.
        thumb = _best_image_url(container) if container.get("data-main-thumb") else None
        if not thumb:
            thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = container.select_one(".entry-header span") if container else None
        duration_el = container.select_one(".duration") if container else None

        title = (
            (title_el.get_text(" ", strip=True) if title_el else None)
            or a.get("title")
            or (img.get("alt") if img else None)
            or a.get_text(" ", strip=True)
        )
        title = _clean_title(title) or "Unknown Video"

        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": None,
                "uploader_name": None,
            }
        )

    return items[:limit]
