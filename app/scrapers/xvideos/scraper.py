
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return host.lower().endswith("xvideos.com")

def get_categories() -> list[dict]:
    import os
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
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
        return [x.strip() for x in re.split(r"[,\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None
    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            if h > 0:
                return f"{h}:{mm:02d}:{s:02d}"
            return f"{mm}:{s:02d}"
        return v or None
    return str(seconds_or_iso).strip() or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _find_duration_like_text(text: str) -> Optional[str]:
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    meta_desc = _meta(soup, name="description")

    # Preview URL extraction
    preview_url = None
    m_preview = re.search(r"setThumbSlide\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", html)
    if m_preview:
        preview_url = m_preview.group(1)

    # Strategy 1: Look for setVideoTitle('...')
    # This is the most accurate raw title from the player config
    m_title = re.search(r"setVideoTitle\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", html)
    js_title = m_title.group(1) if m_title else None

    title = _first_non_empty(js_title, og_title, _text(soup.find("title")))
    
    # distinct suffix removal
    if title:
        for suffix in (" - XVIDEOS.COM", " - XVIDEOS", " XVIDEOS.COM"):
            if title.upper().endswith(suffix):
                title = title[:-len(suffix)]

    description = _first_non_empty(og_desc, meta_desc)
    thumbnail = _first_non_empty(og_image)

    json_ld = _parse_json_ld(soup)
    video_obj: Optional[dict[str, Any]] = None
    for obj in json_ld:
        t = obj.get("@type")
        if isinstance(t, list):
            if any(str(x).lower() == "videoobject" for x in t):
                video_obj = obj
                break
        if isinstance(t, str) and t.lower() == "videoobject":
            video_obj = obj
            break

    duration = None
    uploader = None
    category = None
    tags: list[str] = []

    if video_obj:
        title = _first_non_empty(title, video_obj.get("name"))
        description = _first_non_empty(description, video_obj.get("description"))

        thumb = video_obj.get("thumbnailUrl") or video_obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)

        duration = _normalize_duration(video_obj.get("duration"))

        author = video_obj.get("author")
        if isinstance(author, dict):
            uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
        elif isinstance(author, str):
            uploader = author.strip() or None

        genre = video_obj.get("genre")
        if isinstance(genre, str):
            category = genre.strip() or None
        elif isinstance(genre, list) and genre:
            category = str(genre[0]).strip() or None

        tags = _as_list(video_obj.get("keywords"))

    if not tags:
        for a in soup.select('a[href*="/tags/"]'):
            t = _text(a)
            if t:
                tags.append(t)
    tags = list(dict.fromkeys([t for t in tags if t]))

    if not duration:
        # Try specific duration class first
        dur_node = soup.find(class_=re.compile(r"duration", re.IGNORECASE))
        if dur_node:
            duration = _find_duration_like_text(_text(dur_node) or "")
    if not duration:
        duration = _find_duration_like_text(soup.get_text(" ", strip=True))

    views: Optional[str] = None
    m = re.search(r'"viewCount"\s*:\s*"?([0-9][0-9,\.]*\s*[KMB]?)"?', html, re.IGNORECASE)
    if m:
        views = m.group(1).replace(" ", "")

    # ZERO-COST VIDEO EXTRACTION
    video_data = _extract_video_streams(html)

    # Related Videos Extraction
    related_videos = []
    # XVideos uses 'div.thumb-block' in 'div#video-suggestions' (sometimes different)
    # Generic wide search in the footer area
    
    # XVideos often puts them just after the video in a generic container or reuses thumb-blocks
    # Let's search for thumb-blocks that are NOT the main video (tough without specific container)
    # Look for the "Related Videos" header
    # Usually: <h2 ...>Related videos</h2> ... <div ...> ... blocks
    
    # Simpler: just find all thumb-blocks that are not the current URL?
    # Or specifically looked for the container
    
    # XVideos usually has <div id="video_related_content">
    rel_container = soup.find(id=re.compile("video_related_content|video-suggestions"))
    if rel_container:
        for block in rel_container.select(".thumb-block"):
            try:
                t_div = block.select_one(".thumb")
                if not t_div: continue
                link = t_div.find("a")
                if not link: continue
                
                href = link.get("href")
                if not href: continue
                
                # Title
                r_title = _first_non_empty(link.get("title"), block.select_one("p.title a") and block.select_one("p.title a").get("title"))
                
                # Image
                r_img = link.find("img")
                r_thumb = _best_image_url(r_img)
                
                # Duration
                r_dur = None
                meta_dur = block.select_one(".duration")
                if meta_dur: r_dur = _text(meta_dur)
                
                related_videos.append({
                    "url": f"https://www.xvideos.com{href}" if href.startswith("/") else href,
                    "title": r_title,
                    "thumbnail_url": r_thumb,
                    "duration": r_dur
                })
                
                if len(related_videos) >= 10: break
            except Exception:
                continue

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
        "video": video_data,
        "related_videos": related_videos, # Added related videos
        "preview_url": preview_url, # Added preview
    }


def _extract_video_streams(html: str) -> dict[str, Any]:
    """
    Extract video streams from XVideos HTML
    Uses the same engine as XNXX (html5player)
    """
    streams = []
    hls_url = None
    
    # Method 1: Find setVideoUrlHigh (best quality)
    high_match = re.search(r'html5player\.setVideoUrlHigh\([\'"](.+?)[\'"]\)', html)
    if high_match:
        streams.append({
            "quality": "1080p",
            "url": high_match.group(1),
            "format": "mp4"
        })
    
    # Method 2: Find setVideoUrlLow (lower quality)
    low_match = re.search(r'html5player\.setVideoUrlLow\([\'"](.+?)[\'"]\)', html)
    if low_match:
        low_url = low_match.group(1)
        # Only add if different from high quality
        if not high_match or low_url != high_match.group(1):
            streams.append({
                "quality": "480p",
                "url": low_url,
                "format": "mp4"
            })
    
    # Method 3: Find HLS stream (adaptive quality)
    hls_match = re.search(r'html5player\.setVideoHLS\([\'"](.+?)[\'"]\)', html)
    if hls_match:
        hls_url = hls_match.group(1)
        streams.append({
            "quality": "adaptive",
            "url": hls_url,
            "format": "hls"
        })
    
    # Determine default stream (prefer HLS, then highest quality)
    default_url = None
    if hls_url:
        default_url = hls_url
    elif streams:
        default_url = streams[0]["url"]  # First stream is highest quality
    
    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    candidates: list[str] = []
    sep = "&" if "?" in root else "?"
    if page <= 1:
        candidates.append(root)
    else:
        # If category, standard is /c/Name/2
        if "/c/" in root or "/category" in root:
             candidates.append(f"{root.rstrip('/')}/{page}")
             
        candidates.extend(
            [
                f"{root}new/{page}/",
                f"{root}{sep}p={page - 1}",
                f"{root}{sep}page={page}",
            ]
        )

    html = ""
    used = ""
    last_exc: Exception | None = None
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html:
                break
        except Exception as e:
            last_exc = e
            continue

    if not html:
        if last_exc:
            raise last_exc
        return []

    soup = BeautifulSoup(html, "html.parser")
    base_uri = httpx.URL(used)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Iterate over standard xVideos thumb blocks
    for block in soup.select("div.thumb-block"):
        # Determine URL and Thumbnail from the .thumb div
        thumb_div = block.select_one(".thumb")
        if not thumb_div:
            continue
            
        link_el = thumb_div.find("a")
        if not link_el:
            continue
        
        href = link_el.get("href")
        if not href or "/video" not in href:
            continue
            
        try:
            abs_url = str(base_uri.join(href))
        except Exception:
            abs_url = href
            
        if abs_url in seen:
            continue
            
        img = link_el.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            # Fallback: sometimes data-src is in a script tag or different structure?
            # For now, if no thumb, skip or generic?
            # actually, xvideos consistently uses data-src. 
            # If it's missing, it might be a text ad or weird block.
            continue

        # Extract Title: prefer p.title > a, then link title, then img alt
        title = None
        title_p = block.select_one("p.title a")
        if title_p:
            title = _first_non_empty(title_p.get("title"), _text(title_p))
        if not title:
            title = _first_non_empty(link_el.get("title"), img.get("alt"))

        # Clean potential Suffixes in listing titles (rare but possible)
        if title:
            if title.upper().endswith(" - XVIDEOS.COM"):
                title = title.replace(" - XVIDEOS.COM", "").replace(" - XVIDEOS", "")

        # Duration
        duration = None
        dur_el = block.select_one(".duration, .video-duration")
        if dur_el:
             raw_dur = _text(dur_el)
             # Handle "21 min" format
             if raw_dur:
                 # Check for "X min"
                 m_min = re.search(r"(\d+)\s*min", raw_dur, re.IGNORECASE)
                 if m_min:
                     mins = int(m_min.group(1))
                     duration = f"{mins // 60}:{mins % 60:02d}:00" if mins >= 60 else f"{mins}:00"
                 else:
                    duration = _find_duration_like_text(raw_dur)
                    if not duration and ":" not in raw_dur:
                         # fallback for existing logic
                         pass
                    elif not duration:
                         duration = raw_dur

        # Uploader
        uploader_name = None
        # New selector: just look for the name span inside metadata
        name_el = block.select_one(".metadata .name")
        if name_el:
            uploader_name = _text(name_el)
        else:
             # Fallback to old strict strategy just in case structure varies significantly
             up_el = block.select_one(".metadata a[href*='/profiles/'], .metadata a[href*='/channels/'], .metadata a[href*='/models/'], .metadata a[href*='/pornstars/']")
             if up_el:
                 uploader_name = _text(up_el)
            
        # Views
        views = None
        meta_text = block.select_one(".metadata")
        if meta_text:
            raw_meta = _text(meta_text) or ""
            # Regex for views (e.g. 1.2M Views, 500 Views, 174.9k Views)
            # HTML text: " - 174.9k Views - "
            m = re.search(r"([0-9\.,]+\s*[KMB]?)\s*Views", raw_meta, re.IGNORECASE)
            if m:
                views = m.group(1).replace(" ", "").replace(",", "").upper()

        seen.add(abs_url)
        items.append(
            {
                "url": abs_url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader_name or "XVideos",
            }
        )



    return items
