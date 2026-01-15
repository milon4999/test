from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "spankbang.com" in host.lower()

def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

async def fetch_html(url: str) -> str:
    # Try curl_cffi first (bypasses Cloudflare)
    try:
        async with AsyncSession(
            impersonate="safari15_3",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.3 Safari/605.1.15",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://spankbang.com/",
                "Cookie": "age_verified=1; sb_theme=dark",
            },
            timeout=20.0
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        print(f"⚠️ SpankBang curl_cffi failed: {e}. Falling back to httpx...")
        # Fallback to httpx if curl_cffi fails
        import httpx
        async with httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Cookie": "age_verified=1; sb_theme=dark",
            },
            follow_redirects=True,
            timeout=20.0
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    
    # SpankBang usually has video sources in <source> tags or JavaScript
    # Try multiple methods to extract streams
    
    #1. Parse <source> tags from video element (most reliable)
    soup = BeautifulSoup(html, "lxml")
    sources = soup.select("video source, source")  # Get all source tags, not just those with src
    for source in sources:
        # Check both src and data-src attributes
        src = source.get("src") or source.get("data-src")
        if src and (src.startswith("http") or src.startswith("//")):
            if src.startswith("//"):
                src = "https:" + src
            
            # Skip thumbnail/preview URLs (they contain /t/ and td.mp4)
            if "/t/" in src and "td.mp4" in src:
                continue
            
            # Skip if URL looks like a thumbnail server (tbv.sb-cd.com)
            if "tbv.sb-cd.com" in src:
                continue
            
            # Try to extract quality from data attributes or URL
            quality = source.get("size") or source.get("label") or source.get("data-res")
            
            # If no quality attribute, try to extract from URL (e.g., "9584549-480p.mp4")
            if not quality:
                m = re.search(r'[-_](\d+p)\.mp4', src)
                if m:
                    quality = m.group(1).replace('p', '')  # Extract "480" from "480p"
                else:
                    quality = "unknown"
            
            # Detect format from URL
            fmt = "hls" if ".m3u8" in src else "mp4"
            
            streams.append({
                "quality": str(quality),
                "url": src,
                "format": fmt
            })
    
    # 2. Check for stream_url variable (fallback)
    if not streams:
        m = re.search(r'stream_url\s*=\s*["\'](https?://.*?)["\']', html)
        if m:
            video_url = m.group(1)
            streams.append({
                "quality": "default",
                "url": video_url,
                "format": "mp4"
            })
        
    # 3. Check for stream_data object (second fallback)
    if not streams:
        m_data = re.search(r'var\s+stream_data\s*=\s*(\{.*?\});', html, re.DOTALL)
        if m_data:
            try:
                data = json.loads(m_data.group(1))
                for q, urls in data.items():
                    if isinstance(urls, list) and len(urls) > 0:
                        url = urls[0]
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
    if streams:
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
    
    url = base_url
    
    # Spankbang standard: /2 for page 2
    if page > 1:
        url = base_url.rstrip("/")
        if url == "https://spankbang.com":
             url = "https://spankbang.com/trending_videos"
        elif "/s/" in url:
             # Ensure /s/ URLs keep structure: /s/query/page
             # If url was .../s/amateur, make it .../s/amateur/2
             pass 
        
        # Append page number
        url = f"{url}/{page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []
        
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Updated Selectors based on browser analysis
    # Strategy: Find the container with the MOST video items to avoid "Featured" or "Trending" widgets
    container_selector = ".js-video-item, .video-item, .video-list-video"
    selected_items = []
    
    # 1. Try to find specific container lists first
    media_lists = soup.select(".js-media-list")
    if media_lists:
        # Sort by number of video items inside
        best_container = None
        max_items = -1
        
        for c in media_lists:
            count = len(c.select(container_selector))
            if count > max_items:
                max_items = count
                best_container = c
                
        if best_container and max_items > 0:
            selected_items = best_container.select(container_selector)
    
    # 2. Fallback: If no containers found (or empty), select from global soup
    if not selected_items:
         selected_items = soup.select(container_selector)
         
    for item in selected_items:
        try:
            # Get the main link (usually a.thumb for thumbnail)
            link = item.select_one("a")
            if not link: continue
            
            href = link.get("href")
            if not href: continue
             
            if href.startswith("/"): href = "https://spankbang.com" + href
            
            # Title: in span with text-secondary class (or fallback to .n)
            title = "Unknown"
            title_el = item.select_one("span.text-secondary.text-body-md, .n")
            if title_el:
                title = title_el.get_text(strip=True)

            # Thumbnail
            img = item.find("img")
            thumb = None
            if img:
                thumb = img.get("data-src") or img.get("src")
                if thumb and thumb.startswith("//"): thumb = "https:" + thumb
                
            # Duration: in data-testid="video-item-length"
            duration = "0:00"
            dur_tag = item.select_one('[data-testid="video-item-length"]')
            if dur_tag: 
                duration = dur_tag.get_text(strip=True)
            
            # Views: Find span containing view count (has text-body-md class)
            views = "0"
            # Find spans and check for numeric content that looks like views (e.g., "11K", "2.5M")
            for span in item.find_all("span"):
                classes = span.get("class", [])
                if any("text-body-md" in c for c in classes):
                    txt = span.get_text(strip=True)
                    # Check if it contains numbers and is short (likely views)
                    if txt and any(c.isdigit() for c in txt) and len(txt) <= 10:
                        m = re.search(r'([\d\.]+[KkMm]?)', txt)
                        if m:
                            views = m.group(1)
                            break
            
            # Uploader: in span with text-action-tertiary class
            uploader = "Unknown"
            uploader_el = item.select_one("span.text-action-tertiary")
            if uploader_el:
                uploader = uploader_el.get_text(strip=True)
            
            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader
            })
            
        except:
            continue
            
    return items
