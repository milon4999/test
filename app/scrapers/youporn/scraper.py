from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "youporn.com" in host.lower()

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
        # Mobile cookie might be needed? Usually desktop is safer for parsing.
        # YouPorn might not strict check 'platform=pc' but good practice if structure varies
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    hls_url = None
    
    # YouPorn often puts data in 'page_params' or similar var
    # Look for mediaDefinitions
    # var page_params = {... "mediaDefinitions": [...] ...};
    
    m = re.search(r'var\s+page_params\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not m:
        # Fallback: look for other JSON blobs
        m = re.search(r'mediaDefinitions\s*:\s*(\[.*?\])', html, re.DOTALL)
    
    data = None
    if m:
        try:
            raw = m.group(1)
            # If it captured "var x = {...};", group 1 is "{...}"
            # If fallback captured "[...]", group 1 is "[...]"
            if raw.startswith("["):
                 # It's just the list
                 media_defs = json.loads(raw)
            else:
                 # It's the full object
                 full_data = json.loads(raw)
                 media_defs = full_data.get("mediaDefinitions", [])
                 if not media_defs and "video" in full_data:
                     # sometimes nested?
                     media_defs = full_data["video"].get("mediaDefinitions", [])

            for md in media_defs:
                video_url = md.get("videoUrl")
                if not video_url: continue
                
                fmt = md.get("format") # mp4, hls
                quality = md.get("quality") # 720p, 1080p, etc
                
                if isinstance(quality, list): quality = str(quality[0])
                
                # Check format
                if fmt == "hls" or ".m3u8" in video_url:
                     hls_url = video_url
                     streams.append({
                        "quality": "adaptive",
                        "url": video_url,
                        "format": "hls"
                    })
                elif fmt == "mp4":
                     streams.append({
                        "quality": str(quality) if quality else "unknown",
                        "url": video_url,
                        "format": "mp4"
                    })
        except Exception:
            pass

    # Generic fallback if no JSON found: check for <video> sources
    if not streams:
        soup = BeautifulSoup(html, "lxml")
        video = soup.find("video")
        if video:
            src = video.get("src")
            if src:
                 streams.append({"quality": "unknown", "url": src, "format": "mp4"})
            for source in video.find_all("source"):
                src = source.get("src")
                type_ = source.get("type", "")
                if src:
                    fmt = "hls" if "mpegurl" in type_ or ".m3u8" in src else "mp4"
                    if fmt == "hls":
                        hls_url = src
                    streams.append({"quality": "unknown", "url": src, "format": fmt})

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
    
    # Title
    title = None
    meta_title = soup.find("meta", property="og:title")
    if meta_title: title = meta_title.get("content")
    if not title:
        t_tag = soup.find("title")
        if t_tag: title = t_tag.get_text(strip=True)
    
    if title:
        title = title.replace(" - YouPorn", "").replace(" | YouPorn", "")

    # Thumbnail
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")

    # Duration
    duration = None
    meta_dur = soup.find("meta", property="video:duration")
    if meta_dur:
        try:
            secs = int(meta_dur.get("content"))
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration = f"{h}:{m:02d}:{s:02d}"
            else:
                duration = f"{m}:{s:02d}"
        except:
            pass
            
    # Views
    views = None
    # Usually in a div with class "video-infos" or similar
    # Look for explicit structure or regex
    # "x,xxx,xxx Views"
    text_blob = soup.get_text(" ", strip=True)
    m_views = re.search(r'([\d,]+)\s+views', text_blob, re.IGNORECASE)
    if m_views:
        views = m_views.group(1)

    # Uploader
    uploader = None
    # Look for "Uploaded by X" or class "submitter"
    submitter_div = soup.select_one(".submitter, .video-uploaded-by, .uploader-name")
    if submitter_div:
        uploader = submitter_div.get_text(strip=True).replace("Uploaded by:", "").strip()

    # Tags
    tags = []
    # common tag container
    for t in soup.select(".categories-wrapper a, .tags-wrapper a, .video-tags a"):
        txt = t.get_text(strip=True)
        if txt: tags.append(txt)
    
    # Streams
    video_data = _extract_video_streams(html)

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "YouPorn",
        "tags": tags,
        "video": video_data,
        "related_videos": [], 
        "preview_url": None 
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # YouPorn pagination: /video?page=2 or /category/asian?page=2
    # If base_url is root, likely need /video
    
    url = base_url.rstrip("/")
    if url in ("https://www.youporn.com", "http://www.youporn.com"):
        url = "https://www.youporn.com/video"
        
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
    
    # YouPorn listing: div.video-box or similar
    # Selector: .video-box
    for box in soup.select(".video-box"):
        try:
            # Check if it's a real video box
            if "js-video-box" not in box.get("class", []):
                 # sometimes ad?
                 pass
            
            link = box.select_one("a")
            if not link: continue
            href = link.get("href")
            if not href: continue
             
            if not href.startswith("http"):
                href = "https://www.youporn.com" + href
                
            # Thumbnail
            img = link.select_one("img")
            thumb = None
            if img:
                thumb = img.get("data-src") or img.get("src")
            
            # Title
            title_div = box.select_one(".video-title")
            title = None
            if title_div: title = title_div.get_text(strip=True)
            if not title and img: title = img.get("alt")
            
            # Duration
            dur_div = box.select_one(".duration")
            duration = None
            if dur_div: duration = dur_div.get_text(strip=True)
            
            # Views
            views = None
            # Info usually in .video-infos -> "X views"
            infos = box.select_one(".video-infos")
            if infos:
                info_txt = infos.get_text()
                m = re.search(r'([\d,KM\.]+)\s*views', info_txt, re.IGNORECASE)
                if m: views = m.group(1)
            
            # Uploader
            uploader = None
            # often not shown in grid, or hidden.
            
            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
            })
        except Exception:
            continue
            
    return items
