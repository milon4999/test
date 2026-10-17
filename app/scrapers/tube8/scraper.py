from __future__ import annotations

import json
import re
import os
from typing import Any

import httpx
from bs4 import BeautifulSoup


from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    host_lower = host.lower()
    return "tube8.com" in host_lower


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.tube8.com/",
        "Cookie": "platform=pc"
    }
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True, verify=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception:
        return await pool_fetch_html(url, headers=headers)


async def _resolve_proxy_url(proxy_url: str) -> list[dict]:
    """
    Resolve a Tube8 proxy URL (e.g., /media/mp4?s=...) to actual CDN streams.
    Returns a list of stream objects with quality, url, format.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(proxy_url)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            if isinstance(data, list):
                streams = []
                for item in data:
                    quality = item.get("quality")
                    video_url = item.get("videoUrl")
                    fmt = item.get("format", "mp4")
                    
                    if video_url:
                        # Convert quality to string
                        if isinstance(quality, int):
                            quality = str(quality)
                        
                        streams.append({
                            "quality": quality if quality else "unknown",
                            "url": video_url,
                            "format": fmt
                        })
                return streams
    except Exception:
        pass
    
    return []

def _extract_video_streams(html: str) -> dict[str, Any]:
    """
    Tube8 is on MindGeek/Aylo network — same mediaDefinitions structure as PornHub/RedTube.
    """
    streams: list[dict] = []
    hls_url = None

    # Try page_params (Tube8 standard)
    m = re.search(r"var\s+page_params\s*=\s*(\{.*?\});", html, re.DOTALL)

    if m:
        try:
            raw = m.group(1)
            full = json.loads(raw)
            
            # Navigate to mediaDefinitions: page_params -> video_player_setup -> playervars -> mediaDefinitions
            setup = full.get("video_player_setup", {})
            playervars = setup.get("playervars", {})
            data = playervars.get("mediaDefinitions", [])
            
            if not data:
                # Fallback to general mediaDefinitions search
                m_alt = re.search(r"mediaDefinitions[\"']?\s*:\s*(\[.*?\])", html, re.DOTALL)
                if m_alt:
                    data = json.loads(m_alt.group(1))

            for md in data:
                video_url = md.get("videoUrl")
                if not video_url:
                    continue

                fmt = md.get("format", "mp4")
                quality = md.get("quality", "")

                if isinstance(quality, int):
                    quality = str(quality)
                elif isinstance(quality, list):
                    quality = str(quality[0]) if quality else ""

                if video_url.startswith("/"):
                    video_url = "https://www.tube8.com" + video_url

                stream = {
                    "quality": quality or "unknown",
                    "url": video_url,
                    "format": fmt,
                }

                if fmt == "hls" or ".m3u8" in video_url:
                    q = quality or ""
                    if isinstance(q, str) and q.isdigit():
                        q = f"{q}p"
                    elif not q:
                        q = "adaptive"
                    mq = re.search(r"/(\d{3,4})[pP]?/", video_url)
                    if not mq:
                        mq = re.search(r"(\d{3,4})[pP]?[_/]", video_url)
                    
                    if mq and q == "adaptive":
                        q = f"{mq.group(1)}p"
                    stream["quality"] = q
                    stream["format"] = "hls"
                    if "/" not in (video_url or ""):
                        pass
                    if not hls_url:
                        hls_url = video_url

                streams.append(stream)
        except Exception:
            pass

    # Sort by quality descending
    def _qval(s: dict) -> int:
        q = s.get("quality", "")
        digits = "".join(filter(str.isdigit, str(q)))
        return int(digits) if digits else 0

    streams.sort(key=_qval, reverse=True)

    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {"streams": streams, "default": default_url, "has_video": bool(streams)}


def _format_duration(secs: int) -> str:
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = None
    title_el = soup.select_one("h1.video-title-text, h1.tm_video_title, h1")
    if title_el:
        title = title_el.get_text(strip=True)
    if not title:
        meta_title = soup.find("meta", property="og:title")
        if meta_title:
            title = meta_title.get("content")
    if not title:
        t_tag = soup.find("title")
        if t_tag:
            title = t_tag.get_text(strip=True)
            for suffix in [" - Tube8", " | Tube8", " - tube8.com"]:
                title = title.replace(suffix, "")

    # Thumbnail from og:image
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb:
        thumbnail = meta_thumb.get("content")

    # Duration from page_params or video:duration meta
    duration = None
    m = re.search(r"var\s+page_params\s*=\s*(\{.*?\});", html, re.DOTALL)
    if m:
        try:
            raw = m.group(1)
            full = json.loads(raw)
            setup = full.get("video_player_setup", {})
            playervars = setup.get("playervars", {})
            dur_secs = playervars.get("duration")
            if dur_secs:
                duration = _format_duration(int(dur_secs))
        except Exception:
            pass

    if not duration:
        meta_dur = soup.find("meta", property="video:duration")
        if meta_dur:
            try:
                secs = int(meta_dur.get("content"))
                duration = _format_duration(secs)
            except Exception:
                pass

    # Views - try multiple sources
    views = None
    # Try meta tag first (schema.org VideoObject)
    meta_views = soup.find("meta", property="og:see_also")
    if not meta_views:
        # Try JSON-LD
        m_views = re.search(r'"interactionCount"[:\s]*["\']?([\d,.kmKM]+)', html)
        if m_views:
            views = m_views.group(1).upper()
    if not views:
        # Try text search for patterns like "6.3M Views"
        m_views = re.search(r'([\d,.]+)\s*[Mm]\s*Views', html)
        if m_views:
            views = f"{m_views.group(1)}M"
    if not views:
        v_el = soup.select_one(".video-views, .views, .video_views, .view-count, [data-views]")
        if v_el:
            txt = v_el.get_text(strip=True)
            m = re.search(r"([\d,.]+)\s*[km]?", txt, re.IGNORECASE)
            if m:
                views = m.group(1).upper()

    # Uploader - try multiple sources
    uploader = None
    # From JSON-LD author
    m_author = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
    if m_author:
        uploader = m_author.group(1)
    if not uploader:
        u_el = soup.select_one(".channel-name, .video-channel-name, a[href*='/channel/'], .video-channels-item a, .video-uploaded-by a, .video_channel a, .author-title-text")
        if u_el:
            uploader = u_el.get_text(strip=True)

    # Tags
    tags: list[str] = []
    for t in soup.select(".video-tags a, .tag a"):
        txt = t.get_text(strip=True)
        if txt:
            tags.append(txt)

    video_data = _extract_video_streams(html)

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "Tube8",
        "tags": tags,
        "video": video_data,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    result = parse_page(html, url)
    
    # Check if we have proxy URLs and resolve them to real CDN streams
    video_data = result.get("video", {})
    streams = video_data.get("streams", [])
    
    for stream in streams[:]:  # Copy list to modify while iterating
        stream_url = stream.get("url", "")
        if "/media/" in stream_url and "?s=" in stream_url:
            # This is a proxy URL - resolve it
            resolved_streams = await _resolve_proxy_url(stream_url)
            if resolved_streams:
                # Remove the proxy stream
                streams.remove(stream)
                
                for rs in resolved_streams:
                    q = rs.get("quality", "")
                    
                    if rs.get("format") == "hls" or ".m3u8" in rs.get("url", ""):
                        rs["format"] = "hls"
                        # Extract quality from URL if we have adaptive or unknown quality
                        if not q or q == "adaptive" or q == "unknown" or q == "hls":
                            mq = re.search(r"/(\d{3,4})[pP]?/", rs.get("url", ""))
                            if not mq:
                                mq = re.search(r"(\d{3,4})[pP]?[_/]", rs.get("url", ""))
                            if mq:
                                rs["quality"] = f"{mq.group(1)}p"
                            else:
                                rs["quality"] = "adaptive"
                        elif str(q).isdigit():
                            rs["quality"] = f"{q}p"
                        else:
                            rs["quality"] = q
                    else:
                        # Ensure suffix 'p' for numeric MP4 qualities
                        if q and str(q).isdigit():
                            rs["quality"] = f"{q}p"
                
                streams.extend(resolved_streams)

    # Rearrange: Sort strictly by numeric quality descending
    def get_quality_val(s):
        q = s.get("quality", "")
        if not q: return 0
        # Extract digits: "720P" -> 720
        digits = "".join(filter(str.isdigit, str(q)))
        return int(digits) if digits else 0
        
    # Filter out 180p quality as requested
    streams = [s for s in streams if get_quality_val(s) != 180]
    streams.sort(key=get_quality_val, reverse=True)
    
    # Write back to result
    result["video"]["streams"] = streams
    
    # Preference: HLS first if available, else first MP4
    if streams:
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        if hls_stream:
            result["video"]["default"] = hls_stream["url"]
        else:
            result["video"]["default"] = streams[0]["url"]
            
    result["video"]["has_video"] = len(streams) > 0
    return result

async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    url = base_url.rstrip("/")

    if page > 1:
        # Tube8 pagination pattern: /newest/page/2/ (not ?page=)
        if url.endswith(".html"):
            url = url[: -len(".html")]
        url = f"{url}/page/{page}/"

    try:
        html = await fetch_html(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    seen_hrefs: set[str] = set()

    def _best_image(img) -> str | None:
        if img is None:
            return None
        for attr in ("data-src", "data-poster", "data-mediabook", "src"):
            val = img.get(attr)
            if val and not val.startswith("data:"):
                return val
        return None

    def _make_item(href: str, title: str, thumb: str | None, card) -> dict | None:
        """Build a list item dict from an article.video-box card element."""
        if not href:
            return None

        # Normalize URL for deduplication
        if not href.startswith("http"):
            href = "https://www.tube8.com" + ("/" if not href.startswith("/") else "") + href
        href = href.split("?")[0].rstrip("/")

        if href in seen_hrefs:
            return None
        seen_hrefs.add(href)

        # Duration: div.video-duration / .tm_video_duration (MM:SS)
        duration = "0:00"
        dur_el = card.select_one(".tm_video_duration, .video-duration, .video-duration-text, [data-duration]")
        if dur_el:
            txt = dur_el.get_text(strip=True)
            if txt:
                duration = txt
            elif dur_el.get("data-duration"):
                duration = dur_el.get("data-duration")

        # Views: span.info-views with "Views:" label (rating span has same class)
        views = "0"
        for v_el in card.select(".info-views, .video-views, .views, .video-views-count"):
            label = v_el.get_text(" ", strip=True)
            if "rating" in label.lower() or "like" in label.lower() or "%" in label:
                continue
            m = re.search(r"([\d,.]+[km]?)", label)
            if m:
                views = m.group(1).upper()
                break

        # Uploader: article[data-uploader-name] is most reliable, fallback to a.author-title-text
        uploader = "Unknown"
        data_uploader = card.get("data-uploader-name")
        if data_uploader and data_uploader.lower() not in ("none", "unknown", "null"):
            uploader = data_uploader
        else:
            u_el = card.select_one(".author-title-text, .video-author-text, .username, .uploader-name, a[href*='/user/'], a[href*='/channels/'], a[href*='/amateur/']")
            if u_el:
                txt = u_el.get_text(strip=True)
                if txt and txt.lower() not in ("none", "unknown", "null"):
                    uploader = txt

        return {
            "url": href,
            "title": title or "Unknown",
            "thumbnail_url": thumb,
            "duration": duration,
            "views": views,
            "uploader_name": uploader,
            "preview_url": thumb,
        }

    # ── Layout: article.video-box cards (current Tube8 markup) ────────────────
    for card in soup.select("article.video-box"):
        if len(items) >= limit:
            break
        try:
            # Video href from thumbnail or title link
            a = card.select_one('a[href*="/porn-video/"], a.video-title-text, a.tm_video_link, a.video-box-image') or card.select_one("a[href]")
            href = a.get("href", "") if a else ""

            thumb = _best_image(card.select_one("img"))

            # Title from title link, then aria-label, then img alt
            title = ""
            title_el = card.select_one(".video-title-text, .tm_video_title, .video-title, .video-card-title")
            if title_el:
                title = title_el.get("title") or title_el.get_text(strip=True)
            if not title:
                title = (card.get("aria-label") or "").strip()
            if not title:
                img = card.select_one("img")
                title = (img.get("alt") if img else None) or ""

            item = _make_item(href, title, thumb, card)
            if item:
                items.append(item)
        except Exception:
            continue

    return items[:limit]


