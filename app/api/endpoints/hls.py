import httpx
from fastapi import APIRouter, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from urllib.parse import urljoin, quote
import logging
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Pattern to find URLs in m3u8 files
URL_PATTERN = re.compile(r'(https?://[^\s]+)')

@router.get("/proxy", summary="HLS Proxy")
async def hls_proxy(
    url: str = Query(..., description="Target HLS URL"),
    referer: str = Query(None, description="Referer header to send"),
    origin: str = Query(None, description="Origin header to send"),
    user_agent: str = Query(None, description="User-Agent header to send"),
    request: Request = None
):
    """
    Proxy HLS manifests and segments to bypass CORS/Referer restrictions.
    Rewrites URLs in m3u8 files to point back to this proxy.
    Handles BrazzPW-style meta-refreshes and masked MIME types.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    headers = {}
    ua = user_agent if user_agent else request.headers.get("user-agent")
    if ua:
        headers["User-Agent"] = ua
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
        
    try:
        # We manually manage the client lifecycle to allow true streaming
        # without closing the client before StreamingResponse finishes.
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0)
        current_url = url
        
        req = client.build_request("GET", current_url)
        resp = await client.send(req, stream=True)
        
        # 1. Handle Meta-Refresh or session initialization (common in BrazzPW)
        content_type = resp.headers.get("content-type", "").lower()
        is_html = "text/html" in content_type
        
        if resp.status_code == 403 or is_html:
            await resp.aread()
            if "#EXTM3U" not in resp.text:
                m = re.search(r'url=([^"\']*)', resp.text, re.I)
                if m:
                    refresh_url = urljoin(current_url, m.group(1))
                    logger.info(f"Following meta-refresh to: {refresh_url}")
                    await client.get(refresh_url) # Just visit it to get cookies
                    await resp.aclose()
                    
                    # Retry original
                    req = client.build_request("GET", current_url)
                    resp = await client.send(req, stream=True)
                    content_type = resp.headers.get("content-type", "").lower()
                else:
                    logger.info("Retrying request to handle potential session initialization...")
                    await resp.aclose()
                    
                    req = client.build_request("GET", current_url)
                    resp = await client.send(req, stream=True)
                    content_type = resp.headers.get("content-type", "").lower()
        
        if resp.status_code >= 400:
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream error: {resp.status_code}")
        
        url_lower = url.lower()
        
        # 2. Manifest Rewriting
        if "mpegurl" in content_type or url_lower.endswith(".m3u8") or ".m3u8" in url_lower:
            await resp.aread()
            content = resp.text
            await resp.aclose()
            await client.aclose()
            
            from app.config.settings import settings
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
            base_url = settings.BASE_URL.rstrip("/") if settings.BASE_URL else f"{scheme}://{host}"
            proxy_base = f"{base_url}/api/v1/hls/proxy"
            
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#EXT-X-KEY") and 'URI="' in line:
                        new_lines.append(line)
                    else:
                        new_lines.append(line)
                else:
                    target = urljoin(current_url, line)
                    params = f"?url={quote(target)}"
                    if referer: params += f"&referer={quote(referer)}"
                    if origin: params += f"&origin={quote(origin)}"
                    if user_agent: params += f"&user_agent={quote(user_agent)}"
                    new_lines.append(f"{proxy_base}{params}")
            
            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        
        # 3. Segment Streaming with Content-Type Sniffing
        else:
            async def stream_generator():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()
            
            response_headers = {"Access-Control-Allow-Origin": "*"}
            for h in ["Content-Range", "Content-Length", "Accept-Ranges"]:
                if h.lower() in resp.headers:
                    response_headers[h] = resp.headers[h.lower()]
            
            final_media_type = content_type
            
            # force video/mp2t for anything not a manifest if we suspect masking
            if "brazzpw.com" in url and "image/" in content_type:
                final_media_type = "video/mp2t"

            from starlette.background import BackgroundTask
            return StreamingResponse(
                stream_generator(),
                status_code=resp.status_code,
                media_type=final_media_type,
                headers=response_headers,
                background=BackgroundTask(client.aclose)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"HLS Proxy error: {e}")
        try:
            await client.aclose()
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))
