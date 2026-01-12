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

@router.get("/test-connection")
async def test_connection(
    url: str = Query(None, description="Optional specific URL to test"),
    referer: str = Query(None, description="Optional Referer URL")
):
    """
    Debug endpoint to diagnose Railway connectivity and cookies
    """
    import json
    
    # 1. Check curl_cffi status
    # We access the class variable HAS_CURL_CFFI via import in hls_proxy module
    from app.services.hls_proxy import HAS_CURL_CFFI, hls_proxy
    
    result = {
        "has_curl_cffi": HAS_CURL_CFFI,
        "cached_cookies_count": len(hls_proxy.cookie_cache.get(referer, [[]])[0]) if referer in hls_proxy.cookie_cache else 0,
        "tests": {}
    }
    
    # 2. Test Connection (HomePage)
    try:
        # Use the proxy's internal client logic to test connection
        # Force a fresh client or session to debug
        client = await hls_proxy.get_client()
        
        # Test 1: Homepage (to get cookies)
        home_url = "https://xhamster.com/"
        resp_home = await client.get(home_url)
        result["tests"]["homepage"] = {
            "status": resp_home.status_code,
            "headers_count": len(resp_home.headers),
            "cookies_captured": len(client.cookies) if hasattr(client, 'cookies') else 0
        }
    except Exception as e:
        result["tests"]["homepage"] = {"error": str(e)}
        
    # 3. Test Specific URL
    if url:
        try:
             # Try with current proxy logic
             resp_target = await client.get(url, headers={"Referer": referer} if referer else {})
             result["tests"]["target"] = {
                 "status": resp_target.status_code,
                 "content_length": len(resp_target.content),
                 "sample": str(resp_target.content[:100])
             }
        except Exception as e:
             result["tests"]["target"] = {"error": str(e)}
    
    return result
