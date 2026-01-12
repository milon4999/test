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
    
    # Auto-Proxy for sites with hotlink protection (xHamster)
    if "xhamster.com" in host or "xhcdn" in str(video_data).lower():
        # api_base_url is passed from the endpoint
        
        # Ensure api_base_url doesn't end with slash
        base_url = api_base_url.rstrip("/")
        
        # Wrap streams
        if video_data.get("streams"):
            for s in video_data["streams"]:
                s["url"] = get_proxy_url(s["url"], api_base=base_url, referer=url)
        
        # Wrap HLS
        if video_data.get("hls"):
            video_data["hls"] = get_proxy_url(video_data["hls"], api_base=base_url, referer=url)
            
        # Wrap Default
        if video_data.get("default"):
            video_data["default"] = get_proxy_url(video_data["default"], api_base=base_url, referer=url)

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
    
    return {
        "stream_url": stream_url,
        "quality": selected_quality,
        "format": "mp4",
        "available_qualities": [s["quality"] for s in video_data["streams"]]
    }


def get_proxy_url(original_url: str, api_base: str = "http://localhost:8000", referer: str = None) -> str:
    """
    Convert original video URL to proxied URL (for IP-restricted videos)
    
    Args:
        original_url: Original video/HLS URL
        api_base: Your API base URL
        referer: Optional original video page URL to use as Referer header
        
    Returns:
        Proxied URL that bypasses IP restrictions
    """
    import urllib.parse
    encoded_url = urllib.parse.quote(original_url, safe='')
    
    referer_param = ""
    if referer:
        encoded_referer = urllib.parse.quote(referer, safe='')
        referer_param = f"&referer={encoded_referer}"
    
    # Check if it's an M3U8 playlist
    if ".m3u8" in original_url:
        return f"{api_base}/api/hls/playlist?url={encoded_url}{referer_param}"
    else:
        return f"{api_base}/api/hls/proxy?url={encoded_url}{referer_param}"
