"""
Video Streaming Module
Extract and serve video streaming URLs
"""

from fastapi import HTTPException
from typing import Optional
import logging

logger = logging.getLogger(__name__)


async def get_video_info(url: str, api_base_url: str = "http://localhost:8000") -> dict:
    """
    Get video streaming information for a given URL
    
    Args:
        url: Video page URL (e.g., https://xnxx.com/video-123)
        api_base_url: Base URL of the API for proxy links (e.g., https://my-api.com)
        
    Returns:
        {
            ...
        }
    """
    # Import here to avoid circular dependency
    from app.scrapers import xnxx, xhamster, xvideos, masa49
    from urllib.parse import urlparse
    
    # Parse URL to get host
    parsed = urlparse(url)
    host = parsed.netloc
    
    logger.info(f"Getting video info for: {url}")
    
    # Determine which scraper to use
    scraper_module = None
    if xnxx.can_handle(host):
        scraper_module = xnxx
    elif xhamster.can_handle(host):
        scraper_module = xhamster
    elif xvideos.can_handle(host):
        scraper_module = xvideos
    elif masa49.can_handle(host):
        scraper_module = masa49
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported host: {host}. Supported: xnxx.com, xhamster.com, xvideos.com, masa49.org"
        )
    
    try:
        # Scrape the page (now includes video URLs)
        metadata = await scraper_module.scrape(url)
    except Exception as e:
        logger.error(f"Failed to scrape video info: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to extract video info: {str(e)}"
        )
    
    # Check if video URLs were extracted
    video_data = metadata.get("video", {})
    if not video_data.get("has_video"):
        raise HTTPException(
            status_code=404,
            detail="No video streams found for this URL. Video may be premium or removed."
        )
    
    return {
        "url": url,
        "title": metadata.get("title"),
        "description": metadata.get("description"),
        "thumbnail_url": metadata.get("thumbnail_url"),
        "duration": metadata.get("duration"),
        "views": metadata.get("views"),
        "uploader_name": metadata.get("uploader_name"),
        "category": metadata.get("category"),
        "tags": metadata.get("tags", []),
        "related_videos": metadata.get("related_videos", []),
        "preview_url": metadata.get("preview_url"),
        "video": video_data,
        "playable": True
    }


async def get_stream_url(url: str, quality: str = "default", api_base_url: str = "http://localhost:8000") -> dict:
    """
    Get direct stream URL for a specific quality
    
    Args:
        url: Video page URL
        quality: Desired quality (1080p, 720p, 480p, or "default")
        api_base_url: Base URL for proxy links
        
    Returns:
        {"stream_url": "https://...mp4", "quality": "1080p", "format": "mp4"}
    """
    # Note: get_video_info is async, so this needs to be awaited if called directly.
    # But usually this is called by endpoint which calls get_video_info first.
    # Refactoring: we'll just call get_video_info here too.
    # Using default localhost for this low-level helper as it returns raw data
    info = await get_video_info(url, api_base_url=api_base_url) 
    video_data = info["video"]
    
    if quality == "default":
        stream_url = video_data["default"]
        selected_quality = "default"
    else:
        # Find matching quality
        streams = video_data["streams"]
        matching = [s for s in streams if s["quality"] == quality]
        
        if matching:
            stream_url = matching[0]["url"]
            selected_quality = matching[0]["quality"]
        else:
            # Fallback to default
            stream_url = video_data["default"]
            selected_quality = "default"
            logger.warning(f"Quality {quality} not available, using default")
    
    # Determine format and refine quality label
    fmt = "mp4"
    if ".m3u8" in stream_url:
        fmt = "hls"
        if selected_quality == "default":
            selected_quality = "adaptive"
            
    return {
        "stream_url": stream_url,
        "quality": selected_quality,
        "format": fmt
    }
