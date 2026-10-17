from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core import pool


def can_handle(host: str) -> bool:
    h = host.lower()
    return "xxxparodyhd.net" in h or "urshort.live" in h or "vidara" in h


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = await pool.get_session()
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                resp.raise_for_status()

                # Check if a paginated request was redirected to the base URL (meaning no more pages)
                if "/page/" in url and "/page/" not in str(resp.url):
                    return ""

                return await resp.text()
        except Exception as e:  # noqa: BLE001 - retry transient network/rate-limit failures
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    return ""


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            url = str(v).strip()
            if url.startswith("//"):
                return f"https:{url}"
            return url
    return None


def _row_chips(soup: Any, label: str) -> list[str]:
    out = []
    for row in soup.select("div.row--chips, div.row--stars"):
        b = row.find("b")
        if b and _text(b) == label:
            for a in row.select("a.chip"):
                t = _text(a)
                if t:
                    out.append(t)
    return out


async def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_node = soup.select_one("h1.entry-title, h1.post-title, h1")
    title = _text(title_node) or _text(soup.find("title")) or "Unknown Video"

    # Clean title
    for suffix in (" - XXXParodyHD", " – XXXParodyHD", " | XXXParodyHD"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]

    # Description / meta
    desc_node = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    description = desc_node.get("content") if desc_node else None

    # Thumbnail (og:image)
    og_img = soup.find("meta", property="og:image")
    thumbnail = og_img.get("content") if og_img else None

    # Extract embedded player URLs from data-servers JSON on section.hlm
    embed_urls = []
    embed_host_priority = ("mixdrop", "voe", "playmate", "vidara", "luluvid", "doodstream")
    needs_resolution = ("mixdrop", "voe")

    hlm_section = soup.select_one("section.hlm[data-servers]")
    if hlm_section:
        try:
            servers_json = hlm_section.get("data-servers", "")
            if servers_json:
                servers = json.loads(servers_json)
                for idx, server in enumerate(servers):
                    url = server.get("u", "")
                    label = server.get("l", f"Player {idx + 1}")
                    if not url:
                        continue
                    low = url.lower()
                    host_tag = "other"
                    for h in embed_host_priority:
                        if h in low or h in label.lower():
                            host_tag = h
                            break
                    if host_tag == "other":
                        if "dood" in low or "playmogo" in low:
                            host_tag = "doodstream"
                        elif "lulu" in low:
                            host_tag = "luluvid"
                        elif "vidara" in low:
                            host_tag = "vidara"
                    embed_urls.append({
                        "url": url,
                        "label": label,
                        "host": host_tag,
                        "resolve": host_tag in needs_resolution or any(h in low for h in needs_resolution),
                    })
        except (json.JSONDecodeError, Exception):
            pass

    # Deduplicate by URL, keeping priority order (MixDrop -> VOE -> Playmate -> Lulu)
    seen_urls = set()
    ordered = []
    for host in embed_host_priority:
        for e in embed_urls:
            if e["host"] == host and e["url"] not in seen_urls:
                seen_urls.add(e["url"])
                ordered.append(e)
    embed_urls = ordered

    # Resolve MixDrop/VOE embeds to their REAL redirect targets at scrape time:
    # - MixDrop: HTTP 302 (e.g. mixdrop.my -> miixdrop.top — rotating mirrors)
    # - VOE: HTTP 200 but a JS redirect (window.location.href = ...) inside a
    #   ~750-byte bootstrap page -> extract the target with a regex.
    # Datacenter IPs may instead receive a challenge page — if resolution
    # fails the VOE stream is DROPPED (voe.sx serves a bootstrap page, not a
    # player, so the raw URL is unplayable), while MixDrop keeps its original
    # URL (mixdrop.my still 302s client-side).
    resolved: list[dict[str, Any]] = []
    to_resolve: dict[str, Optional[str]] = {
        e["url"]: None for e in embed_urls if e.get("resolve")
    }
    if to_resolve:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            to_resolve = {}
        else:
            async with AsyncSession(impersonate="chrome120", timeout=20.0) as client:
                for u in list(to_resolve.keys()):
                    try:
                        resp = await client.get(u, allow_redirects=False)
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = (resp.headers.get("Location") or "").strip()
                            if loc.startswith("http"):
                                to_resolve[u] = loc
                                continue
                        body = resp.text or ""
                        # VOE-style JS redirect inside the page body
                        m = re.search(
                            r"window\.location(?:\.href)?\s*=\s*[\"']([^\"']+)",
                            body,
                            re.IGNORECASE,
                        )
                        if m:
                            to_resolve[u] = m.group(1)
                            continue
                        # meta refresh fallback
                        m = re.search(
                            r'http-equiv="refresh"[^>]*url=([^"\'>]+)', body, re.IGNORECASE
                        )
                        if m:
                            to_resolve[u] = m.group(1).strip()
                            continue
                        # any /e/{id} URL on a foreign domain inside the body
                        m = re.search(
                            r"https?://(?!voe\.sx|mixdrop\.)[a-z0-9.-]+/e/[a-z0-9]+",
                            body,
                            re.IGNORECASE,
                        )
                        if m:
                            to_resolve[u] = m.group(0)
                    except Exception:
                        continue

    final_urls: list[dict[str, Any]] = []
    for e in embed_urls:
        resolved_url = to_resolve.get(e["url"])
        if e.get("resolve") and not resolved_url:
            if e["host"] == "voe":
                continue
        final_urls.append({**e, "url": resolved_url or e["url"]})
    embed_urls = final_urls

    # Build video data
    # XXXParodyHD doesn't host videos directly - it links to external embed players
    video_url = None
    streams = []
    if embed_urls:
        # Default: MixDrop if available (priority order: MixDrop -> VOE -> Playmate -> Lulu)
        # Match on the host TAG, not the URL — rewritten domains (miixdrop.top,
        # eugenemakedraw.com) no longer contain the original host names.
        priority_default = next(
            (e for pref in ("mixdrop", "voe", "playmate") for e in embed_urls if e["host"] == pref),
            None,
        )
        video_url = (priority_default or embed_urls[0])["url"]

        for idx, embed in enumerate(embed_urls):
            streams.append({
                "url": embed["url"],
                "quality": embed.get("label", f"Player {idx + 1}"),
                "format": "embed",
            })

    # Tags / Genres
    tags = _row_chips(soup, "Genres")
    if not tags:
        for a in soup.select('.entry-terms a[href*="/genre/"], .tag-links a, a[rel="tag"]'):
            t = _text(a)
            if t:
                tags.append(t)

    # Category info
    categories = _row_chips(soup, "Category")

    # Studio / Director
    studio = None
    _studios = _row_chips(soup, "Studio")
    if _studios:
        studio = _studios[0]

    # Cast (Pornstars)
    cast = _row_chips(soup, "Pornstars")

    # Year
    year = None
    _years = _row_chips(soup, "Release Year")
    if _years:
        year = _years[0]
    if not year:
        _y = soup.select_one("span.st--year")
        year = _text(_y) if _y else None

    # Views
    views = None
    _v = soup.select_one("span.st--views b")
    if _v:
        views = _text(_v)

    # Related Videos
    related_videos = []
    for item in soup.select(".related article.card, .related-movies article.card, article.card"):
        try:
            link = item.select_one("a.card__th, a[href]")
            if not link:
                continue
            r_url = link.get("href", "")
            if not r_url or "xxxparodyhd.net" not in r_url:
                continue

            r_title_el = item.select_one(".card__t a, h2 a, h3 a, .post-meta a")
            r_title = _text(r_title_el) if r_title_el else (link.get("title") or _text(link.select_one("img")))
            img = item.select_one(".card__img, img")
            r_thumb = _best_image_url(img)
            r_dur_el = item.select_one(".card__dur, .duration, .runtime")
            r_dur = _text(r_dur_el) if r_dur_el else None

            related_videos.append({
                "url": r_url,
                "title": r_title,
                "thumbnail_url": r_thumb,
                "duration": r_dur,
            })
            if len(related_videos) >= 10:
                break
        except Exception:
            continue

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "tags": tags,
        "categories": categories,
        "studio": studio,
        "cast": cast,
        "year": year,
        "views": views,
        "related_videos": related_videos,
        "video": {
            "default": video_url,
            "has_video": video_url is not None,
            "streams": streams,
        },
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return await parse_page(html, url)


def get_categories() -> list[dict[str, Any]]:
    file_path = os.path.join(os.path.dirname(__file__), "categories.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    target_url = base_url.rstrip("/")

    if page > 1:
        if "?" in target_url:
            base, query = target_url.split("?", 1)
            target_url = f"{base.rstrip('/')}/page/{page}/?{query}"
        else:
            target_url = f"{target_url}/page/{page}/"
    elif not target_url.endswith("/") and "?" not in target_url:
        target_url += "/"

    try:
        html = await fetch_html(target_url)
    except Exception as e:
        # ClientResponseError from aiohttp raise_for_status() exposes .status
        if getattr(e, "status", None) == 404:
            return []
        raise

    if not html:
        return []
        
    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []

    # XXXParodyHD uses article.card elements with .card__th, .card__t, .card__dur
    for card in soup.select("article.card"):
        try:
            # Skip ad cards (card--zn) with no video link
            link = card.select_one("a.card__th")
            if not link:
                link = card.select_one("a[href]")
            if not link:
                continue

            href = link.get("href", "") or link.get("data-href", "")
            if not href:
                continue

            skip_patterns = ["/category/", "/genre/", "/director/", "/cast/",
                             "/release-year/", "/page/", "#", "/tag/", "/pornstar/"]
            if any(p in href for p in skip_patterns):
                continue
            if "xxxparodyhd.net" not in href:
                continue

            # Title from h2.card__t a
            title_el = card.select_one(".card__t a")
            title = _text(title_el) if title_el else (
                link.get("title") or link.get("oldtitle") or _text(link)
            )
            if not title:
                continue

            # Thumbnail
            img = card.select_one(".card__img, a.card__th img")
            thumb = _best_image_url(img)

            # Duration from span.card__dur
            dur_el = card.select_one(".card__dur")
            duration = _text(dur_el) if dur_el else None

            # Year from span.card__q
            year_el = card.select_one(".card__q")
            year = _text(year_el) if year_el else None

            # Studio from .card__by or a.card__tx
            studio_el = card.select_one(".card__by, a.card__tx")
            studio = _text(studio_el) if studio_el else None

            items.append({
                "url": href,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "upload_time": year,
                "studio": studio,
            })
        except Exception:
            continue

    return items[:limit]

