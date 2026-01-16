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
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    # Headers to send to upstream
    headers = {}
    
    # Forward User-Agent from request if available, or allow override via query
    ua = user_agent if user_agent else request.headers.get("user-agent")
    if ua:
        headers["User-Agent"] = ua
        
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
        
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url)
            
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail="Upstream error")
            
            content_type = resp.headers.get("content-type", "")
            
            # If it's an m3u8 playlist, we need to rewrite URLs
            if "mpegurl" in content_type.lower() or url.endswith(".m3u8") or ".m3u8" in url:
                content = resp.text
                base_url = str(request.base_url).rstrip("/")
                proxy_base = f"{base_url}/api/v1/hls/proxy"
                
                # Function to replace URLs
                def replace_url(match):
                    target = match.group(1)
                    # If relative URL, resolve it
                    if not target.startswith("http"):
                        target = urljoin(url, target)
                        
                    # Re-encode params
                    params = f"?url={quote(target)}"
                    if referer:
                        params += f"&referer={quote(referer)}"
                    if origin:
                        params += f"&origin={quote(origin)}"
                        
                    return f"{proxy_base}{params}"
                
                # Use regex to find and replace URIs in the m3u8 content
                # M3U8 lines are either directives (#...) or URIs
                # We need to be careful not to replace things inside #EXT-X-KEY if they are not URLs?
                # Actually, standard m3u8 format puts URIs on their own lines OR in attributes
                
                # Simple line-by-line processing is safer
                lines = content.split('\n')
                new_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        new_lines.append(line)
                        continue
                        
                    if line.startswith("#"):
                        # Handle URI attributes in tags like #EXT-X-KEY:METHOD=AES-128,URI="..."
                        if 'URI="' in line:
                             # This is a bit complex regex-wise, but essential for encrypted streams
                             # For now, Beeg doesn't seem to use encryption keys in the transparent way, 
                             # or at least the URLs we saw were plain HLS.
                             # Let's skip complex attribute parsing for this MVP unless needed.
                             new_lines.append(line)
                        else:
                            new_lines.append(line)
                    else:
                        # It's a URI line (segment or sub-playlist)
                        target = line
                        if not target.startswith("http"):
                            target = urljoin(url, target)
                        
                        params = f"?url={quote(target)}"
                        if referer:
                            params += f"&referer={quote(referer)}"
                        if origin:
                            params += f"&origin={quote(origin)}"
                        if user_agent:
                            headers["User-Agent"] = user_agent # Ensure header is set for this request too (redundant but safe)
                            # Actually this is loop for rewriting URLs for NEXT requests
                            params += f"&user_agent={quote(user_agent)}"
                            
                        new_lines.append(f"{proxy_base}{params}")
                
                modified_content = "\n".join(new_lines)
                
                return Response(
                    content=modified_content,
                    media_type="application/vnd.apple.mpegurl",
                    headers={"Access-Control-Allow-Origin": "*"}
                )
            
            else:
                # It's a segment (TS, MP4, Key, etc.) - Stream it
                return StreamingResponse(
                    resp.aiter_bytes(),
                    media_type=content_type,
                    headers={"Access-Control-Allow-Origin": "*"}
                )
                
    except Exception as e:
        logger.error(f"HLS Proxy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
