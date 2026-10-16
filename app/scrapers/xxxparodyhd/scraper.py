from __future__ import annotations

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
    async with session.get(url, headers=headers, allow_redirects=True) as resp:
        resp.raise_for_status()
        
        # Check if a paginated request was redirected to the base URL (meaning no more pages)
        if "/page/" in url and "/page/" not in str(resp.url):
            return ""
            
        return await resp.text()


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

    # Extract embedded player URLs (iframes)
    embed_urls = []
    # Player hosts that actually play (verified 2026-09): MixDrop, VOE,
    # Playmate, LuluStream. FreeDL is a download host (no player) and is skipped.
    # MixDrop/VOE embeds redirect to rotating mirror domains at runtime — each
    # embed URL is RESOLVED LIVE below (HTTP 302 for mixdrop, JS
    # window.location for voe) instead of using a static domain map.
    embed_host_priority = ("mixdrop", "voe", "playmate", "vidara", "luluvid", "doodstream")
    embed_host_skip = ("frdl", "freedl", "nitroflare", "rapidgator")
    needs_resolution = ("mixdrop", "voe")
    for a_tag in soup.select(".su-spoiler-content a[href], .entry-content a[href], .Rtable1 a[href], .post-buttons a[href]"):
        href = a_tag.get("href", "")
        text = _text(a_tag) or ""
        low = href.lower()
        embed_hosts = ["dood", "doply", "vidnest", "player4me", "upns", "voe.sx",
                       "embedseek", "seekplayer", "mixdrop", "easyvidplayer", "rpmplay",
                       "luluvid", "streamtape", "playmate", "hxfile", "xshotcok", "urshort",
                       "vidara", "doodstream", "lulustream"]
        if any(h in low for h in embed_hosts):
            # Skip non-playing download hosts
            if any(x in low for x in embed_host_skip):
                continue
            # Tag the host BEFORE resolution (the resolved domain no longer
            # contains the original host name: voe.sx -> eugenemakedraw.com)
            host_tag = next((h for h in embed_host_priority if h in low), "other")
            embed_urls.append({
                "url": href,
                "label": text or "Player",
                "host": host_tag,
                "resolve": any(h in low for h in needs_resolution),
            })

    # Also check for iframes
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        if src and ("http" in src or src.startswith("//")):
            if src.startswith("//"):
                src = f"https:{src}"
            low = src.lower()
            if any(x in low for x in embed_host_skip):
                continue
            if not any(h in low for h in embed_hosts):
                continue
            host_tag = next((h for h in embed_host_priority if h in low), "other")
            embed_urls.append({
                "url": src,
                "label": "Embedded Player",
                "host": host_tag,
                "resolve": any(h in low for h in needs_resolution),
            })

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
    tags = []
    for a in soup.select('.entry-terms a[href*="/genre/"], .tag-links a, a[rel="tag"]'):
        t = _text(a)
        if t:
            tags.append(t)

    # Category info
    category_links = soup.select('.entry-terms a[href*="/category/"]')
    categories = [_text(cl) for cl in category_links if _text(cl)]

    # Studio / Director
    studio_links = soup.select('.entry-terms a[href*="/director/"]')
    studio = _text(studio_links[0]) if studio_links else None

    # Cast
    cast_links = soup.select('.entry-terms a[href*="/cast/"]')
    cast = [_text(cl) for cl in cast_links if _text(cl)]

    # Year
    year_links = soup.select('.entry-terms a[href*="/release-year/"]')
    year = _text(year_links[0]) if year_links else None

    # Views
    views = None
    views_match = re.search(r'Views:\s*([\d,]+)', html)
    if views_match:
        views = views_match.group(1)

    # Related Videos
    related_videos = []
    for item in soup.select(".related-movies article.post, .related-posts article.post, article.post"):
        try:
            link = item.select_one(".post-thumbnail a[href]")
            if not link:
                link = item.select_one("a[href]")
            if not link:
                continue
            r_url = link.get("href", "")
            if not r_url or "xxxparodyhd.net" not in r_url:
                continue

            r_title = item.select_one(".post-meta a, h2 a, .title a")
            r_title = _text(r_title) if r_title else (link.get("title") or _text(link.select_one("img")))
            img = item.select_one(".post-thumbnail img")
            r_thumb = _best_image_url(img)
            r_dur = item.select_one(".duration, .runtime, .post-thumbnail .duration")
            r_dur = _text(r_dur) if r_dur else None

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

    # XXXParodyHD uses article.post elements with .post-thumbnail and .post-meta
    for card in soup.select("article.post"):
        try:
            # Main link in .post-thumbnail
            link = card.select_one(".post-thumbnail a")
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

            # Title from .post-meta a (first link)
            title_el = card.select_one(".post-meta a")
            title = _text(title_el) if title_el else (
                link.get("title") or link.get("oldtitle") or _text(link)
            )
            if not title:
                continue

            # Thumbnail
            img = card.select_one(".post-thumbnail img, .post-thumbnail a img")
            thumb = _best_image_url(img)

            # Duration from span.duration inside .post-thumbnail
            dur_el = card.select_one(".post-thumbnail .duration")
            duration = _text(dur_el) if dur_el else None

            # Studio from .post-meta span.studio a
            studio_el = card.select_one(".post-meta span.studio a")
            studio = _text(studio_el) if studio_el else None

            items.append({
                "url": href,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "upload_time": None,
                "studio": studio,
            })
        except Exception:
            continue

    return items

