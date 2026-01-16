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
    from app.scrapers import xnxx, xhamster, xvideos, masa49, pornhub, youporn, redtube, beeg, spankbang
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
    elif pornhub.can_handle(host):
        scraper_module = pornhub
    elif youporn.can_handle(host):
        scraper_module = youporn
    elif redtube.can_handle(host):
        scraper_module = redtube
    elif beeg.can_handle(host):
        scraper_module = beeg
    elif spankbang.can_handle(host):
        scraper_module = spankbang
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported host: {host}. Supported: xnxx, xhamster, xvideos, masa49, pornhub, youporn, redtube, beeg, spankbang"
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
    
    # Post-process streams to wrap with proxy if needed (Beeg, etc.)
    # This logic mirrors get_stream_url's proxy wrapping but for the entire list
    if video_data.get("has_video") and video_data.get("streams"):
        from urllib.parse import quote
        
        # Helper to wrap URL
        def proxy_wrap(stream_url):
            should_proxy = False
            referer = ""
            
            if "externulls.com" in stream_url or "beeg.com" in stream_url:
                if ".m3u8" in stream_url or "media=hls" in stream_url:
                    should_proxy = True
                    referer = "https://beeg.com/"
                
            if should_proxy:
                encoded_url = quote(stream_url)
                encoded_referer = quote(referer)
                # Ensure api_base_url is valid
                base = api_base_url if api_base_url else "http://localhost:8000"
                
                proxy_url = f"{base}/api/v1/hls/proxy?url={encoded_url}&referer={encoded_referer}"
                # RedTube user_agent logic removed
                    
                return proxy_url
            return stream_url

        # Wrap extracted streams
        for stream in video_data["streams"]:
            stream["url"] = proxy_wrap(stream["url"])
            
        # Wrap default stream
        if video_data.get("default"):
            video_data["default"] = proxy_wrap(video_data["default"])
            
        # Wrap HLS stream specifically if present as key
        if video_data.get("hls"):
            video_data["hls"] = proxy_wrap(video_data["hls"])

    # Build response with consistent field order
    # For SpankBang, exclude metadata fields as they're not reliably extracted
    if scraper_module == spankbang:
        # SpankBang: minimal metadata
        response = {
            "url": url,
            "tags": metadata.get("tags", []),
            "related_videos": metadata.get("related_videos", []),
            "video": video_data,
            "playable": True,
        }
    else:
        # All other sources: full metadata
        response = {
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
            "playable": True,
        }
    
    return response


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
            
        # PROXY WRAPPER FOR BEEG and RedTube
        should_proxy = False
        referer = ""
        
        if "externulls.com" in stream_url or "beeg.com" in stream_url:
             should_proxy = True
             referer = "https://beeg.com/"
             
        if should_proxy:
            from urllib.parse import quote
            # Construct proxy URL
            # api_base_url comes from get_video_info caller
            # We need to ensure we have a valid api_base_url
            if not api_base_url:
                api_base_url = "http://localhost:8000" # fallback
                
            encoded_url = quote(stream_url)
            encoded_referer = quote(referer)
            
            stream_url = f"{api_base_url}/api/v1/hls/proxy?url={encoded_url}&referer={encoded_referer}"
            # RedTube user_agent logic removed
            
    return {
        "stream_url": stream_url,
        "quality": selected_quality,
        "format": fmt
    }
