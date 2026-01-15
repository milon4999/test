from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "beeg.com" in host.lower()

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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        # Beeg seems to check Referer for some API calls, but for HTML it's fine
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

async def scrape(url: str) -> dict[str, Any]:
    # Beeg video URLs are usually https://beeg.com/{id}
    # New pattern: https://beeg.com/-0{id} or similar
    # API: https://store.externulls.com/facts/file/{id}
    
    video_id = None
    # Extract last numeric sequence
    # This handles beeg.com/1234, beeg.com/-01234 (groups 1234 if we are careful)
    # Actually, beeg seems to use a long ID now.
    # regex: look for digits at end of string or before ?
    
    # Try generic search for digits in path
    path = url.split("?")[0]
    segments = path.split("/")
    for seg in reversed(segments):
        # clean segment
        clean = re.sub(r'[^0-9]', '', seg)
        if clean and len(clean) > 5: # IDs are usually long now
            video_id = clean
            break
            
    if not video_id:
        match = re.search(r'beeg\.com/.*?(\d+)', url)
        if match:
            video_id = match.group(1)
            
    if not video_id:
        return _parse_html_fallback(url, url) # Should fetch HTML if no ID found, but unlikely to work for SPA
        
    # Check if ID has leading zero but API expects int
    # API IDs in probe were int-like (no quotes in some views, but JSON keys are specific)
    # We'll try the ID as extracted. If it starts with 0 and fails, we can retry without.
    # Actually, if we use int(video_id), we strip leading zeros safe.
    try:
        api_id = str(int(video_id))
    except:
        api_id = video_id
        
    api_url = f"https://store.externulls.com/facts/file/{api_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://beeg.com",
        "Referer": "https://beeg.com/",
    }
    
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            resp = await client.get(api_url)
            if resp.status_code == 404 and len(str(api_id)) > 10:
                # Retry? maybe logic was wrong
                pass
            resp.raise_for_status()
            data = resp.json()
            return _parse_externulls_response(data, url, api_id)
    except Exception as e:
        print(f"Beeg scrape error: {e}")
        # Fallback to HTML if API fails? HTML is likely empty but worth a shot for legacy links
        pass

    html = await fetch_html(url)
    return _parse_html_fallback(html, url)

def _parse_externulls_response(item: dict, url: str, video_id: str) -> dict[str, Any]:
    file_info = item.get("file", {})
    
    # Title
    title = "Unknown"
    for metaf in file_info.get("data", []):
        if metaf.get("cd_column") == "sf_name":
            title = metaf.get("cd_value")
            break
            
    # Facts
    facts_list = item.get("fc_facts", [])
    facts = facts_list[0] if facts_list else {}
    
    views = str(facts.get("fc_st_views", "0"))
    
    # Duration
    duration_sec = file_info.get("fl_duration", 0)
    mins = duration_sec // 60
    secs = duration_sec % 60
    duration = f"{mins}:{secs:02d}"
    
    # Thumbnail
    thumb_url = None
    thumbs_list = facts.get("fc_thumbs", [])
    if thumbs_list:
        t_idx = thumbs_list[len(thumbs_list)//2]
        thumb_url = f"https://thumbs.externulls.com/videos/{video_id}/{t_idx}.webp"
        
    # Uploader
    uploader = "Beeg"
    for t in item.get("tags", []):
        if t.get("is_owner"):
            uploader = t.get("tg_name")
            break
            
    # Streams
    streams = []
    
    # 1. HLS Master (Multi)
    hls_res = file_info.get("hls_resources", {})
    multi = hls_res.get("fl_cdn_multi")
    if multi:
        streams.append({
            "quality": "adaptive",
            "format": "hls",
            "url": f"https://video.externulls.com/{multi}"
        })
        
    # 2. Individual Qualities (MP4/HLS segments?)
    # usually qualities -> h264 -> list
    qualities = file_info.get("qualities", {})
    # check h264, h265, av1
    for codec in ["h264", "h265", "av1"]:
        q_list = qualities.get(codec, [])
        for q_item in q_list:
            q_val = str(q_item.get("quality"))
            q_url = q_item.get("url")
            if q_url:
                streams.append({
                    "quality": f"{q_val}p",
                    "format": "mp4", # Assuming mp4 for these direct links based on browser/json
                    "url": f"https://video.externulls.com/{q_url}"
                })
                
    # Sort streams? VideoStreaming service handles sorting usually.
    # But let's ensure HLS is present.
    
    return {
        "url": url,
        "title": title,
        "description": "",
        "thumbnail_url": thumb_url,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "Beeg",
        "tags": [t.get("tg_name") for t in item.get("tags", []) if t.get("tg_name")] if item.get("tags") else [],
        "video": {
            "streams": streams,
            "default": streams[0]["url"] if streams else None,
            "has_video": len(streams) > 0
        },
        "preview_url": thumb_url
    }

def _parse_html_fallback(html: str, url: str) -> dict[str, Any]:
    # Placeholder for simple regex extraction if API fails
    return {
        "url": url,
        "video": {"streams": [], "has_video": False}
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # Beeg uses a separate API domain now: store.externulls.com
    API_BASE = "https://store.externulls.com/facts"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://beeg.com",
        "Referer": "https://beeg.com/",
    }
    
    offset = (page - 1) * limit
    
    # Determine endpoint
    if "q=" in base_url or "/search" in base_url:
        # Extract query
        # base_url might be "https://beeg.com/search?q=query"
        query = "asian" # Default
        if "q=" in base_url:
            query = base_url.split("q=")[1].split("&")[0]
        api_url = f"{API_BASE}/search?q={query}&limit={limit}&offset={offset}"
        
    elif "f=" in base_url:
        # Category: https://beeg.com/?f=Asian
        slug = base_url.split("f=")[1].split("&")[0]
        api_url = f"{API_BASE}/tag?slug={slug}&limit={limit}&offset={offset}"
        
    else:
        # Homepage / Default listing (Featured tag ID 27173)
        api_url = f"{API_BASE}/tag?id=27173&limit={limit}&offset={offset}"
        
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()
            
    except Exception as e:
        print(f"Beeg list error: {e}")
        return []
        
    items = []
    # Data is usually a list of objects
    if not isinstance(data, list):
         return []
         
    for item in data:
        try:
            file_info = item.get("file", {})
            # ID is usually in file.id or just id? 
            # Browser check said URL is -0{id} but API has numeric ID. 
            # We will use file['id']
            video_id = file_info.get("id")
            if not video_id:
                # Check top level id? item['id'] usually fact ID not video ID
                # item['fc_facts'][0]['id']?
                continue
                
            # Title
            title = "Unknown"
            # file['data'] is list of metadata
            for metaf in file_info.get("data", []):
                if metaf.get("cd_column") == "sf_name":
                    title = metaf.get("cd_value")
                    break
            
            # Facts (views, thumbs)
            facts_list = item.get("fc_facts", [])
            if not facts_list: continue
            facts = facts_list[0]
            
            views_raw = facts.get("fc_st_views", 0)
            
            # Format Views (e.g. 1.2M)
            if views_raw > 1000000:
                views = f"{views_raw/1000000:.1f}M"
            elif views_raw > 1000:
                views = f"{views_raw/1000:.1f}K"
            else:
                views = str(views_raw)
                
            # Thumbnail
            # Pattern: https://thumbs.externulls.com/videos/{id}/{thumb_id}.webp
            thumbs_list = facts.get("fc_thumbs", [])
            thumb_url = None
            if thumbs_list:
                # Pick a middle thumbnail
                t_idx = thumbs_list[len(thumbs_list)//2]
                thumb_url = f"https://thumbs.externulls.com/videos/{video_id}/{t_idx}.webp"
            
            # Duration
            duration_sec = file_info.get("fl_duration", 0)
            mins = duration_sec // 60
            secs = duration_sec % 60
            duration = f"{mins}:{secs:02d}"
            
            # Uploader
            uploader = "Beeg"
            tags = item.get("tags", [])
            for t in tags:
                if t.get("is_owner"):
                    uploader = t.get("tg_name")
                    break
            
            # URL
            # Browser subagent: https://beeg.com/-0{id}
            # We'll stick to simple /{id} unless blocked.
            video_url = f"https://beeg.com/{video_id}"
            
            items.append({
                "url": video_url,
                "title": title,
                "thumbnail_url": thumb_url,
                "duration": duration,
                "views": views,
                "uploader_name": uploader
            })
            
        except Exception:
            continue
            
    return items
