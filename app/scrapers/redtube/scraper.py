from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    host_lower = host.lower()
    return "redtube.com" in host_lower or "redtube.net" in host_lower

def get_categories() -> list[dict]:
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
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=30.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

async def _resolve_proxy_url(proxy_url: str) -> list[dict]:
    """
    Resolve a RedTube proxy URL (e.g., /media/mp4?s=...) to actual CDN streams.
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
    streams = []
    hls_url = None
    
    # RedTube also uses mediaDefinitions often, similar to PH
    m = re.search(r'mediaDefinitions["\']?\s*:\s*(\[.*?\])', html, re.DOTALL)
    if not m:
         # sometimes wrapped in a larger object `storage_options` or `video_player_setup`
         m = re.search(r'var\s+page_params\s*=\s*(\{.*?\});', html, re.DOTALL)
    
    if m:
        try:
            raw = m.group(1)
            if raw.startswith("["):
                 data = json.loads(raw)
            else:
                 full = json.loads(raw)
                 data = full.get("mediaDefinitions", [])
                 if not data and "video" in full:
                     data = full["video"].get("mediaDefinitions", [])

            # Two-pass extraction: first try direct CDN URLs, then fall back to proxy URLs
            direct_streams = []
            proxy_streams = []
            
            for md in data:
                video_url = md.get("videoUrl")
                if not video_url: continue
                
                fmt = md.get("format")
                quality = md.get("quality")
                
                # Convert quality to string if it's an integer
                if isinstance(quality, int):
                    quality = str(quality)
                elif isinstance(quality, list):
                    quality = str(quality[0])
                
                # Build stream object
                stream = {
                    "quality": quality if quality else "unknown",
                    "url": video_url,
                    "format": fmt or "mp4"
                }
                
                # Categorize as direct CDN or proxy
                is_proxy = video_url.startswith("/media/") or "?s=eyJ" in video_url
                
                # Convert relative URLs to absolute
                if video_url.startswith("/"):
                    stream["url"] = "https://www.redtube.com" + video_url
                
                # Adjust quality and format for HLS
                if fmt == "hls" or ".m3u8" in video_url:
                    stream["quality"] = "adaptive"
                    stream["format"] = "hls"
                    if not is_proxy:
                        hls_url = stream["url"]
                
                # Add to appropriate list
                if is_proxy:
                    proxy_streams.append(stream)
                else:
                    direct_streams.append(stream)
            
            # Prefer direct CDN URLs, fall back to proxy if none found
            if direct_streams:
                streams = direct_streams
            else:
                streams = proxy_streams
                
        except Exception:
            pass
            
    # Determine default
    default_url = None
    if hls_url:
        default_url = hls_url
    elif streams:
        default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    title = None
    t_tag = soup.find("title")
    if t_tag: title = t_tag.get_text(strip=True).replace(" - RedTube", "")
    
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")
    
    duration = None
    # RedTube duration sometimes in meta video:duration (seconds)
    meta_dur = soup.find("meta", property="video:duration")
    if meta_dur:
        try:
            secs = int(meta_dur.get("content"))
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            if h > 0: duration = f"{h}:{m:02d}:{s:02d}"
            else: duration = f"{m}:{s:02d}"
        except: pass
        
    views = None
    # .views or .video-views
    v_el = soup.select_one(".video-views, .views")
    if v_el:
        txt = v_el.get_text(strip=True)
        # "123,456 Views"
        m = re.search(r'([\d,]+)', txt)
        if m: views = m.group(1)
        
    uploader = None
    u_el = soup.select_one(".video-channels-item a, .video-uploaded-by a")
    if u_el: uploader = u_el.get_text(strip=True)
    
    tags = []
    for t in soup.select(".video-tags a"):
        txt = t.get_text(strip=True)
        if txt: tags.append(txt)
        
    video_data = _extract_video_streams(html)
    
    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "RedTube",
        "tags": tags,
        "video": video_data,
        "related_videos": [], # TODO
        "preview_url": None
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    result = parse_page(html, url)
    
    # Check if we have proxy URLs and resolve them to real CDN streams
    video_data = result.get("video", {})
    streams = video_data.get("streams", [])
    
    # If any stream is a proxy URL, try to resolve it
    for stream in streams[:]:  # Copy list to modify while iterating
        stream_url = stream.get("url", "")
        if "/media/" in stream_url and "?s=eyJ" in stream_url:
            # This is a proxy URL - resolve it
            resolved_streams = await _resolve_proxy_url(stream_url)
            if resolved_streams:
                # Remove the proxy stream
                streams.remove(stream)
                # Add all resolved streams
                streams.extend(resolved_streams)
    
    # Update default URL based on resolved streams
    if streams:
        # Find HLS adaptive stream or highest quality MP4
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        if hls_stream:
            video_data["default"] = hls_stream["url"]
        else:
            # Find highest quality MP4
            mp4_streams = [s for s in streams if s.get("format") == "mp4"]
            if mp4_streams:
                # Sort by quality (try to get 1080, 720, etc.)
                qualities = {"1080": 4, "720": 3, "480": 2, "240": 1}
                mp4_streams.sort(key=lambda s: qualities.get(s.get("quality", ""), 0), reverse=True)
                video_data["default"] = mp4_streams[0]["url"]
    
    return result

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # RedTube: /?page=2 or /videos?page=2
    url = base_url.rstrip("/")
    if url in ("https://www.redtube.com", "http://www.redtube.com"):
         url = "https://www.redtube.com/"
    
    if "?" in url:
        url += f"&page={page}"
    else:
        url += f"?page={page}"
        
    try:
        html = await fetch_html(url)
    except Exception:
        return []
        
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Modern RedTube selectors: li.videoblock_list, also check for others just in case
    # The IDs in debug HTML were like id="mrv_198642241"
    
    for box in soup.select("li.videoblock_list, .video_id_container, .ph-video-block"):
        try:
            # Title & HREF
            # .video-title-wrapper a.video-title-text (modern) OR .video_title a (legacy)
            title_tag = box.select_one(".video-title-text, .video_title a, .title a")
            
            # Fallback for link if title_tag fails or href missing
            link_tag = box.select_one("a.video_link, a.img-wrapper")
            
            href = None
            title = None
            
            if title_tag:
                title = title_tag.get_text(strip=True)
                href = title_tag.get("href")
                
            if not href and link_tag:
                 href = link_tag.get("href")
                 if not title:
                     title = link_tag.get("title")
                     
            if not href: continue
            
            if not href.startswith("http"):
                href = "https://www.redtube.com" + href
                
            # Thumbnail
            thumb = None
            img = box.select_one("img.thumb, img.lazy, .video_thumb_image img")
            if img:
                thumb = img.get("data-src") or img.get("data-thumb_url") or img.get("src")
                if not title and img.get("alt"):
                     title = img.get("alt")
            
            # Duration
            dur_el = box.select_one(".tm_video_duration, .duration, .video_duration")
            duration = "0:00"
            if dur_el: duration = dur_el.get_text(strip=True)
            
            # Views
            # <span class='info-views'>19.5K</span>
            views = "0"
            views_tags = box.select("span.info-views")
            if views_tags:
                views = views_tags[0].get_text(strip=True)
            else:
                # Legacy
                v_el = box.select_one(".views, .video_views")
                if v_el: views = v_el.get_text(strip=True).replace("views", "").strip()

            # Uploader
            uploader = "Unknown"
            # .author-title-text
            u_el = box.select_one(".author-title-text, .username a")
            if u_el: uploader = u_el.get_text(strip=True)
            
            items.append({
                "url": href,
                "title": title or "Unknown",
                "thumbnail": thumb,
                "duration": duration,
                "views": views,
                "uploader": uploader,
                "preview_url": thumb # Use thumb as preview
            })
        except Exception as e:
            # print(f"Error parsing item: {e}")
            continue
            
    return items
