from fastapi import APIRouter, Query, Response, Request
from fastapi.responses import StreamingResponse
from app.services.hls_proxy import hls_proxy

router = APIRouter()

@router.get("/proxy")
async def proxy_stream(
    url: str = Query(..., description="URL to proxy"),
    referer: str = Query(None, description="Optional Referer URL")
):
    """
    General proxy for video segments and resources.
    """
    # If it's a TS file, stream it
    if ".ts" in url or ".m4s" in url: 
         return await hls_proxy.stream_segment(url, referer=referer)
    
    # Otherwise generic proxy
    return await hls_proxy.proxy_request(url, referer=referer)

@router.get("/playlist")
async def proxy_playlist(
    request: Request, 
    url: str = Query(..., description="M3U8 Playlist URL"),
    referer: str = Query(None, description="Optional Referer URL")
):
    """
    Proxy M3U8 playlist and rewrite internal URLs to use /proxy endpoint.
    """
    # We use the base URL of the incoming request to rewrite links
    # This ensures links work on both localhost and production (Railway)
    
    # Priority: 1. Env Var (BASE_URL) 2. Request Host
    from app.config.settings import settings
    
    if settings.BASE_URL:
        base_url = settings.BASE_URL.rstrip("/")
    else:
        # request.base_url returns e.g. "https://my-app.railway.app/"
        base_url = str(request.base_url).rstrip("/")
    
    # Construct the proxy endpoint URL
    base_proxy_url = f"{base_url}/api/hls/proxy"
    
    content = await hls_proxy.proxy_m3u8(url, base_proxy_url, referer=referer)
    
    return Response(
        content=content, 
        media_type="application/vnd.apple.mpegurl"
    )
