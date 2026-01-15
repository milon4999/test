from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

from curl_cffi.requests import AsyncSession

async def fetch_html(url: str) -> str:
    # Use curl_cffi to bypass Cloudflare
    async with AsyncSession(
        impersonate="chrome120",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Cookie": "age_verified=1; sb_theme=dark",
        },
        timeout=20.0
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    
    # SpankBang usually has a simple 'stream_url' variable in JS
    # var stream_url = "https://...";
    # Or 'stream_data' object
    
    # Look for stream_url
    video_url = None
    
    # 1. Check for stream_url
    m = re.search(r'stream_url\s*=\s*["\'](https?://.*?)["\']', html)
    if m:
        video_url = m.group(1)
        streams.append({
            "quality": "default", # Usually the highest available or adaptive
            "url": video_url,
            "format": "mp4" # or m3u8 if detected
        })
        
    # 2. Check for stream_data = { ... "4k": "...", "1080p": "..." }
    # var stream_data = {"4k":[],"1080p":["https:\/\/..."],"720p":["..."]};
    m_data = re.search(r'var\s+stream_data\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m_data:
        try:
            data = json.loads(m_data.group(1))
            for q, urls in data.items():
                if isinstance(urls, list) and len(urls) > 0:
                    url = urls[0] # Prefer first
                elif isinstance(urls, str):
                    url = urls
                else:
                    continue
                    
                if url:
                    url = url.replace('\\/', '/')
                    streams.append({
                        "quality": q.replace('p', ''),
                        "url": url,
                        "format": "mp4"
                    })
        except:
             pass
             
    # Determine default
    default_url = None
    # Sort streams by quality if possible (4k > 1080 > 720 > 480)
    # But usually 'default' (stream_url) is acceptable
    
    if streams:
        # If we have a stream_url, it's often the best bet for default
        if video_url:
            default_url = video_url
        else:
            default_url = streams[0]["url"]
            
    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    title = None
    t_tag = soup.select_one("h1")
    if t_tag: title = t_tag.get_text(strip=True)
    
    thumbnail = None
    # og:image
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")
    
    duration = "0:00"
    # Try to find duration in meta
    # <meta itemprop="duration" content="PT6M33S" /> is standard but SpankBang varies
    # Or parsing from sidebar
    
    uploader = "SpankBang"
    u_el = soup.select_one(".user a, .user-name")
    if u_el: uploader = u_el.get_text(strip=True)
    
    tags = []
    for t in soup.select(".categories a, .tags a"):
        txt = t.get_text(strip=True)
        if txt and txt.lower() not in ["tags", "categories"]:
            tags.append(txt)
            
    video_data = _extract_video_streams(html)
    
    return {
        "url": url,
        "title": title or "Unknown",
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": "0", 
        "uploader_name": uploader or "SpankBang",
        "category": "SpankBang",
        "tags": tags,
        "video": video_data,
        "related_videos": [], 
        "preview_url": None
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # Pagination: spankbang.com/upcoming/2
    
    url = base_url.rstrip("/")
    
    # Check if header/footer link
    # Spankbang standard: /2 for page 2
    if page > 1:
        if "/s/" in url: # Search
            url = f"{url}/{page}"
        else:
            url = f"{url}/{page}"
            
    try:
        html = await fetch_html(url)
    except Exception:
        return []
        
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Updated Selectors based on browser analysis
    container_selector = ".js-video-item, .video-item, .video-list-video"
    
    for item in soup.select(container_selector):
        try:
            # Title & Link
            link = item.select_one("a.n, a.thumb") 
            if not link:
                # Fallback: any link with /video/
                link = item.find("a", href=re.compile(r"/video/"))
            
            if not link: continue
            
            href = link.get("href")
            if not href: continue
             
            if href.startswith("/"): href = "https://spankbang.com" + href
            
            # Title might be in link title or text, or separate .n element if link is thumb
            title_el = item.select_one(".n")
            if title_el: title = title_el.get_text(strip=True)
            else: title = link.get("title") or link.get_text(strip=True)

            # Thumbnail
            img = item.find("img")
            thumb = None
            if img:
                thumb = img.get("data-src") or img.get("src")
                if thumb and thumb.startswith("//"): thumb = "https:" + thumb
                
            # Duration
            duration = "0:00"
            dur_tag = item.select_one(".l, .t, .duration, div.absolute")
            if dur_tag: duration = dur_tag.get_text(strip=True)
            
            # Views
            views = "0"
            view_tag = item.select_one(".v, .views, .stats")
            if view_tag:
                 txt = view_tag.get_text(strip=True)
                 m = re.search(r'([\d\.kKdM]+)', txt)
                 if m: views = m.group(1)
            
            items.append({
                "url": href,
                "title": title or "Unknown",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": "Unknown"
            })
            
        except:
            continue
            
    return items
