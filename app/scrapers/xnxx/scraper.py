
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return host.lower().endswith("xnxx.com")

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


def _extract_video_urls(html: str) -> dict[str, Any]:
    """
    Extract video stream URLs from XNXX page
    
    XNXX uses html5player JavaScript to set video URLs:
    - setVideoUrlLow(...) - Lower quality MP4
    - setVideoUrlHigh(...) - Higher quality MP4  
    - setVideoHLS(...) - HLS adaptive stream
    
    Returns:
        {
            "streams": [{"quality": "1080p", "url": "...", "format": "mp4"}, ...],
            "hls": "https://..../master.m3u8",
            "default": "https://..../video.mp4",
            "has_video": true/false
        }
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
    hls_match = re.search(r'html5player\.setVideoHLS\s*\(\s*[\'"](.+?)[\'"]\s*\)', html)
    if hls_match:
        hls_url = hls_match.group(1)
    
    # Method 4: Alternative - look for video URLs in player JSON config
    if not streams:
        # Sometimes XNXX has video URLs in a JSON config object
        config_match = re.search(r'video_url["\']?\s*:\s*["\']([^"\']+)["\']', html)
        if config_match:
            streams.append({
                "quality": "default",
                "url": config_match.group(1),
                "format": "mp4"
            })
    
    # Determine default stream (prefer highest quality)
    default_url = None
    if streams:
        default_url = streams[0]["url"]  # First stream is highest quality
    elif hls_url:
        default_url = hls_url
    
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": len(streams) > 0 or hls_url is not None
    }


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    meta_desc = _meta(soup, name="description")

    # Strategy 1: Look for setVideoTitle('...')
    # This is the most accurate raw title from the player config
    m_title = re.search(r"setVideoTitle\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", html)
    js_title = m_title.group(1) if m_title else None

    title = _first_non_empty(js_title, og_title, _text(soup.find("title")))

    # distinct suffix removal
    if title:
        for suffix in (" - XNXX.COM", " - XNXX", " XNXX.COM"):
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

    views: Optional[str] = None

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

        # Extract views from interactionStatistic
        interaction = video_obj.get("interactionStatistic")
        if interaction:
            interactions = interaction if isinstance(interaction, list) else [interaction]
            for i in interactions:
                if not isinstance(i, dict):
                    continue
                itype = i.get("interactionType")
                is_watch = False
                if isinstance(itype, str):
                    is_watch = "WatchAction" in itype
                elif isinstance(itype, dict):
                    t = itype.get("@type")
                    if t and "WatchAction" in str(t):
                         is_watch = True
                
                if is_watch:
                     count = i.get("userInteractionCount")
                     if count:
                         views = str(count)
                         break

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

    if not views:
        # Strategy 3: Regex for visible view count in metadata text
        # e.g. " - 402,455" at the end of the metadata block
        # or "7min | 360p - 402,455"
        meta_node = soup.select_one(".metadata")
        if meta_node:
             txt = _text(meta_node) or ""
             # Look for number at the end of the string, confusingly XNXX sometimes puts it there
             # match things like "- 266,039" or "- 1.2M"
             m_fallback = re.search(r"-\s*(\d+(?:\.\d+)?|\d[\d,\.]*)\s*([KMB])?$", txt, re.IGNORECASE)
             if m_fallback:
                  num = m_fallback.group(1).replace(" ", "").replace(",", "")
                  suf = (m_fallback.group(2) or "").upper()
                  views = f"{num}{suf}" if suf else num

    if not views:
        # Strategy 4: Layout with .metadata .right containing "16.3M 100%"
        right_span = soup.select_one(".metadata .right")
        if right_span:
            r_text = _text(right_span) or ""
            # Look for number that is NOT followed by % (rating)
            # Match 16.3M, 200K, 12345
            matches = re.finditer(r"(\d+(?:\.\d+)?|\d[\d,\.]*)\s*([KMB])?", r_text, re.IGNORECASE)
            for m_match in matches:
                # Check if followed by %
                end_idx = m_match.end()
                # Skip if immediate next char is % (ignoring whitespace in between if needed, but the regex included whitespace before KMB)
                # Let's check the original string slice after the match
                post_match = r_text[end_idx:].lstrip()
                if post_match.startswith("%"):
                    continue
                
                # Check if it looks like a year (e.g. 2022) or resolution (1080) if no suffix? 
                # Usually views are large or have suffix.
                # But simple heuristic: first non-percentage number in .right is usually views.
                val = m_match.group(1).replace(" ", "").replace(",", "")
                suf = (m_match.group(2) or "").upper()
                views = f"{val}{suf}" if suf else val
                break

    if not views:
        m = re.search(r'"viewCount"\s*:\s*"?([0-9][0-9,\.]*\s*[KMB]?)"?', html, re.IGNORECASE)
        if m:
            views = m.group(1).replace(" ", "")
    
    if not category:
        for a in soup.select('a[href*="/categories/"]'):
            t = _text(a)
            if t:
                category = t
                break

    # Related Videos Extraction
    related_videos = []
    # XNXX typically uses 'div#related-videos' containing 'div.thumb-block'
    rel_container = soup.find(id="related-videos")
    if rel_container:
        for block in rel_container.select(".thumb-block"):
            try:
                # Extract basic info from related block
                t_div = block.select_one(".thumb")
                if not t_div: continue
                link = t_div.find("a")
                if not link: continue
                
                href = link.get("href")
                if not href: continue
                
                # Title
                r_title = _first_non_empty(link.get("title"), block.select_one(".thumb-under p a") and block.select_one(".thumb-under p a").get("title"))
                
                # Image
                r_img = link.find("img")
                r_thumb = _best_image_url(r_img)
                
                # Duration
                r_dur = None
                meta = block.select_one(".metadata")
                if meta:
                     txt = _text(meta) or ""
                     d_m = re.search(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b", txt)
                     if d_m: r_dur = d_m.group(0)

                related_videos.append({
                    "url": f"https://www.xnxx.com{href}" if href.startswith("/") else href,
                    "title": r_title,
                    "thumbnail_url": r_thumb,
                    "duration": r_dur
                })
                
                if len(related_videos) >= 10: break
            except Exception:
                continue

    # NEW: Extract video URLs for streaming
    video_info = _extract_video_urls(html)

    # Preview Extraction
    preview_url = None
    # XNXX html5player.setThumbSlide('...') or setThumbSlideBig('...')
    # These are sprite sheets or single images, but sometimes setVideoPreview or similar exists?
    # Actually setThumbSlide often points to a sprite sheet which is tricky to use as a "video preview".
    # BUT, recently XNXX/XVideos added setVideoPreview for some ids.
    # Let's look for known preview patterns.
    
    # 1. Slide/Timeline
    # html5player.setThumbSlide('/img/00/00/00/big.jpg') -> often not a video.
    
    # 2. XNXX sometimes exposes a short mp4 preview in JSON-LD or meta?
    # No, usually they use "mouse over" sprites.
    
    # Let's try to capture the "ThumbSlide" url as a "preview_image" or "preview_scrubber"
    # For now, let's look for setThumbSlideBig
    pv_match = re.search(r"html5player\.setThumbSlideBig\(['\"](.+?)['\"]\)", html)
    if pv_match:
        preview_url = pv_match.group(1)

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
        "related_videos": related_videos,
        "video": video_info,
        "preview_url": preview_url, # Added preview (sprite/scrubber)
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    # XNXX commonly uses ?p=0-based page index on some listings.
    # XNXX commonly uses ?p=0-based page index on some listings.
    candidates: list[str] = []
    if page <= 1:
        # If it looks like the homepage, use a popular category with pagination instead
        # Using /search/trending because it's the primary trending content
        if "xnxx.com" in root and len(root.split("/")) <= 4: 
             candidates.append(f"{root}search/trending")
        else:
             candidates.append(root)
    else:
        # For pagination on search pages, XNXX often uses 0-indexed pagination
        # Check if we're on a search category page
        if "xnxx.com/search/" in root:
            # Remove trailing slash if present for consistent URL building
            clean_root = root.rstrip('/')
            # Category pagination typically uses /{page-1}
            candidates.extend([
                f"{clean_root}/{page - 1}",
                f"{clean_root}?p={page - 1}",
            ])
        elif "xnxx.com" in root and len(root.split("/")) <= 4:
            # Homepage-like URLs - default to trending category with pagination
            candidates.extend([
                f"{root}search/trending/{page - 1}",
            ])
        else:
            # Generic pagination patterns
            sep = "&" if "?" in root else "?"
            candidates.extend([
                f"{root}{page - 1}/",       #  0-indexed direct append
                f"{root}{sep}p={page - 1}",
                f"{root}{sep}page={page}",
            ])

    html = ""
    used = ""
    last_exc: Exception | None = None
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html and "thumb-block" in html:
                break
            else:
                pass  # Try next candidate
        except Exception as e:
            last_exc = e
            continue

    if not html:
        if last_exc:
            raise last_exc
        return []

    soup = BeautifulSoup(html, "lxml")
    base_uri = httpx.URL(used)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Iterate over standard XNXX thumb blocks (sharing structure with XVideos often)
    # usually div.thumb-block
    for block in soup.select("div.thumb-block"):
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
            continue

        # Title: inside .thumb-under > p > a (first paragraph, no class)
        title = None
        # Try finding the first paragraph in thumb-under that contains an 'a'
        # The title class is NOT present in xnxx, so we just check for p > a
        title_p = block.select_one(".thumb-under p a")
        if title_p:
            title = _first_non_empty(title_p.get("title"), _text(title_p))
        if not title:
            title = _first_non_empty(link_el.get("title"), img.get("alt"))
            
        if title:
             if title.upper().endswith(" - XNXX.COM"):
                title = title.replace(" - XNXX.COM", "").replace(" - XNXX", "")

        # Duration
        duration = None
        # Often in .thumb-under > p.metadata text (not inside .right)
        dur_el = block.select_one(".metadata")
        if dur_el:
             # Look for typical duration format in text
             txt = _text(dur_el) or ""
             d_match = re.search(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b", txt)
             if d_match:
                 duration = d_match.group(0)
             # Also try text like "15min" or "60min"
             if not duration:
                  m_min = re.search(r"(\d+)min", txt)
                  if m_min:
                       mins = int(m_min.group(1))
                       h = mins // 60
                       m = mins % 60
                       duration = f"{h}:{m:02d}:00" if h > 0 else f"{m}:00"

        
        # Uploader
        uploader_name = None
        # Usually in .thumb-under .metadata a
        # Or .uploader .name
        up_el = block.select_one(".uploader .name, .metadata a[href*='/pornstar/'], .metadata a[href*='/profiles/'], .metadata a[href*='/model/']")
        if up_el:
            uploader_name = _text(up_el)
            
        # Views
        views = None
        meta_text = block.select_one(".metadata")
        if meta_text:
            raw_meta = meta_text.get_text(" ", strip=True)
            # Regex 1: Explicit "views" keyword e.g. "100 views", "1.5M views"
            m = re.search(r"(\d+(?:\.\d+)?|\d[\d,\.]*)\s*([KMB])?\s*(?:views|view)\b", raw_meta, re.IGNORECASE)
            
            # Regex 2: Suffix only e.g. "2.4M" (common in some XNXX layouts)
            # Use finditer to skip percentages (ratings)
            if not views:
                matches = re.finditer(r"(\d+(?:\.\d+)?|\d[\d,\.]*)\s*([KMB])?\b", raw_meta, re.IGNORECASE)
                for m_match in matches:
                    # Check if followed by %
                    end_idx = m_match.end()
                    post_match = raw_meta[end_idx:].lstrip()
                    if post_match.startswith("%"):
                        continue
                        
                    # Basic validation: XNXX views usually have K/M/B or are just plain numbers.
                    # If it's a plain number under 100 might be duration (min) or something else?
                    # But usually lists allow it.
                    # Check if we already found duration and this looks like duration?
                    # The duration logic above consumes "min" or "MM:SS".
                    
                    val = m_match.group(1).replace(" ", "").replace(",", "")
                    suf = (m_match.group(2) or "").upper()
                    
                    # If just a small number without suffix, risky. But typically views have suffix on index.
                    # Let's enforce suffix OR explicit context if possible. 
                    # But the regex allows optional suffix. 
                    # If we accept optional suffix, we might match "7" from "7min" (caught byDuration?)
                    # actually "7min" would be "7" then "min". regex relies on boundary. 
                    
                    # Safer: Only accept if suffix exists OR if we are confident it's not a year/duration.
                    if not suf and len(val) < 3:
                        continue # Skip small numbers without suffix (likely noise)

                    views = f"{val}{suf}" if suf else val
                    break

            if views:
                pass # Already set
            elif m: # Old fallback (variable m from Regex 1 if it matched and we didn't overwrite)
                 # Wait, m from Regex 1 logic was:
                 pass

        seen.add(abs_url)
        items.append(
            {
                "url": abs_url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader_name,
            }
        )



    return items
